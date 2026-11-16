# -*- coding: utf-8 -*-
"""按展位表批量采集企业招聘信息（联网并行）。

链路与页面里的「企业搜索」完全一致：
    bing 多查询 -> 抓页面（JS 站用本机 Edge 无头渲染）-> page_matches 校验
    -> 大模型结构化抽取（失败退规则）-> 写回 company_jobs

并行：4 线程（与 portal.api_lookup 同口径）。招聘聚合站（BOSS/智联/51job/猎聘/
应届生）也进候选，只是必须页面里出现企业全称或短名才认领，避免张冠李戴。

纪律：不重试、不 sleep 循环；单家失败如实记 miss，继续下一家；每批落库。

用法：
    python -X utf8 collect_roster.py --limit 1        # 冒烟
    python -X utf8 collect_roster.py                  # 全量待采集
    python -X utf8 collect_roster.py --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("FAIR_JS_RENDER", "1")   # JS 招聘页用无头浏览器渲染

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.environ.get("FAIR_DB") or os.path.join(DATA, "careersafari.db")
TODO = os.path.join(DATA, "_roster_todo.json")
MISS = os.path.join(DATA, "_roster_miss.json")
PROGRESS = os.path.join(os.path.dirname(HERE), "PROGRESS_采集.md")

sys.path.insert(0, HERE)
import portal  # noqa: E402

LOCK = None


def init_lock():
    global LOCK
    import threading
    LOCK = threading.Lock()


ENRICH_KEYS = ("city", "province", "nature", "industry", "scale_tier", "is_listed",
               "confidence", "enriched", "city_raw", "nature_raw", "industry_raw")


def _title_key(job):
    """岗位名做合并键：去空白、全角括号归一；空名和非 dict 直接丢弃。"""
    if not isinstance(job, dict):
        return ""
    return re.sub(r"\s+", "", str(job.get("title") or "")).replace("（", "(").replace("）", ")")


def merge_jobs(old_jobs, new_jobs):
    """采集只做增量补全：同名岗用本次新岗顶替（带 URL），旧库独有岗位保留。

    整体替换会让「本次只抓到 3 岗」把旧库核对过的 41 岗抹成 3 岗，和 MISS 冲空
    是同一类破坏。按名字合并后，新库岗位数只会增加、URL 只会变多。
    """
    merged, pos = [], {}
    for job in old_jobs or []:
        key = _title_key(job)
        if not key or key in pos:
            continue
        pos[key] = len(merged)
        merged.append(job)
    for job in new_jobs or []:
        key = _title_key(job)
        if not key:
            continue
        if key in pos:
            merged[pos[key]] = job          # 新岗带 source_url，顶替同名旧岗
        else:
            pos[key] = len(merged)
            merged.append(job)              # 本次新抓到的岗，追加
    return merged


def save_item(name, item):
    con = sqlite3.connect(DB, timeout=20)
    try:
        row = con.execute("SELECT item_json FROM company_jobs WHERE name=?", (name,)).fetchone()
        old = json.loads(row[0]) if row else {}
        merged = dict(old)
        patch = dict(item)
        # 已经用大模型补全过属性的企业，不要拿采集时的猜测值覆盖（城市会被写成「深圳市」这种）
        if old.get("enriched") and not patch.get("enriched"):
            for k in ENRICH_KEYS:
                patch.pop(k, None)
        # 主营只会「补进来」，不会被一次没抓到的采集抹掉：模型这次没从页面找到业务介绍，
        # 不代表上次找到的那句失效了。空覆盖有值 = 平白丢一次人工/历史成果。
        if str(patch.get("biz_scope") or "").strip() in ("", "未知") and old.get("biz_scope"):
            patch.pop("biz_scope", None)
        # 企业属性同口径（2026-11-16）：本次没查到（空 / 「未知」）就保留旧值。
        # 旧值是上次真实查到的证据，本次失败不代表它失效——v3 重采曾把
        # city/nature/industry 的旧库已知值冲成未知（city 丢 24 家、nature 19 家、
        # industry 14 家，振华新云 / 电科55所 / 科伦药业都在名单里）。
        # 本次真查到了新值（非未知）仍照常覆盖，保留增量更新的能力。
        for k in ("industry", "nature", "city", "province", "segment", "job_brief"):
            if str(patch.get(k) or "").strip() in ("", "未知") \
                    and str(old.get(k) or "").strip() not in ("", "未知"):
                patch.pop(k, None)
        # 采集只做增量补全，不做删减：本次 MISS 时保住旧库岗位与来源/证据，
        # 本次抓到岗位时按名字合并（同名用带 URL 的新岗顶替，旧库独有保留）。
        if "jobs" in patch:
            old_jobs = old.get("jobs")
            new_jobs = patch.get("jobs")
            if not isinstance(old_jobs, list):
                old_jobs = []
            if not isinstance(new_jobs, list):
                new_jobs = []
            if not new_jobs and old_jobs:
                for k in ("jobs", "jobSource", "jobEvidence", "source"):
                    patch.pop(k, None)
            elif new_jobs and old_jobs:
                patch["jobs"] = merge_jobs(old_jobs, new_jobs)
        merged.update(patch)
        merged.pop("roster_only", None)
        con.execute("INSERT OR REPLACE INTO company_jobs(name,item_json,updated_at) VALUES (?,?,?)",
                    (name, json.dumps(merged, ensure_ascii=False),
                     time.strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    # 4 → 6：抓取是 I/O 等待，6 个线程对 bing / 企业官网都不算压；真正的并发上限
    # 由 portal 里的 HTTP 信号量（12）和浏览器信号量（2）卡住，不会把无头渲染挤爆。
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="已有岗位的也重采")
    ap.add_argument("--names", help="只采文件里列出的企业（每行一个名称），无视是否有岗位")
    ap.add_argument("--retry-miss", action="store_true",
                    help="只重跑上次没抓到岗位的企业（读 _roster_miss.json）")
    ap.add_argument("--scope-only", action="store_true",
                    help="只补主营业务：有岗位但主营为空的企业，走页面缓存跑一次模型")
    args = ap.parse_args()

    if not portal.setup_model():
        print("缺少 API key，只能走规则抽取", file=sys.stderr)
    else:
        print("模型：%s" % portal.MODEL_ON.get("model"))

    # 名单源头是展位表（booths.json），_roster_todo.json 只是它的派生快照。
    # 快照丢了就当场重建——别让整条流程因为少一个中间文件跑不起来。
    if os.path.isfile(TODO):
        todo = json.load(open(TODO, encoding="utf-8"))
    else:
        booths = json.load(open(os.path.join(DATA, "booths.json"), encoding="utf-8"))
        todo = [b["name"] for b in booths if b.get("name")]
        with open(TODO, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
        print("已从展位表重建名单：%d 家" % len(todo))
    scope_only = args.scope_only
    if scope_only:
        # 只补主营。页面正文有缓存（portal.pages_for），所以这一步不重抓，
        # 每家就是「读缓存 + 一次模型调用」。
        con = sqlite3.connect(DB)
        todo = []
        for n, ij in con.execute("SELECT name,item_json FROM company_jobs"):
            try:
                it = json.loads(ij)
            except Exception:
                continue
            if (it.get("jobs") or []) and str(it.get("biz_scope") or "").strip() in ("", "未知"):
                todo.append(n)
        con.close()
        print("主营为空且有岗位：%d 家" % len(todo))
    elif args.retry_miss and os.path.isfile(MISS):
        todo = json.load(open(MISS, encoding="utf-8"))
        print("上次未抓到岗位：%d 家" % len(todo))
    elif args.names:
        # 指定名单：只采这些，且不管库里有没有岗位（用于定向补漏）
        want = [ln.strip() for ln in open(args.names, encoding="utf-8") if ln.strip()]
        idx = {n: i for i, n in enumerate(todo)}
        todo = sorted([n for n in want if n in idx], key=lambda n: idx[n])
        unknown = [n for n in want if n not in idx]
        if unknown:
            print("名单里没有这些企业，已跳过：%s" % "、".join(unknown), file=sys.stderr)
    elif not args.force:
        con = sqlite3.connect(DB)
        have = set()
        for n, ij in con.execute("SELECT name,item_json FROM company_jobs"):
            try:
                if json.loads(ij).get("jobs"):
                    have.add(n)
            except Exception:
                pass
        con.close()
        todo = [n for n in todo if n not in have]
    if args.limit:
        todo = todo[: args.limit]
    print("待采集 %d 家，%d 线程" % (len(todo), args.workers))
    if not todo:
        return 0

    init_lock()
    t0 = time.time()
    ok = miss = 0
    lines = []
    results = []
    miss_names = []

    def work(name):
        st = time.time()
        if scope_only:
            try:
                got = portal.build(name)
            except Exception as exc:
                return name, {"biz_scope": "未知", "note": str(exc)[:60]}, time.time() - st
            # 只挑主营回写：这次抽到的岗位可能有出入，别拿它覆盖库里已经核对过的岗位
            scope = str(got.get("biz_scope") or "").strip() or "未知"
            patch = {"biz_scope": scope}
            if scope != "未知":
                save_item(name, patch)      # 其它分支在 work 末尾落库，这条是提前 return，必须自己写
            return name, patch, time.time() - st
        item = {}
        # 搜索这一步偶尔会瞬时失败（bing 返回空页）。只对「没抓到页面」立即再试一次，
        # 不 sleep、不循环：抓到了就没岗位说明确实没有，重试没意义。
        for attempt in (1, 2):
            try:
                item = portal.build(name)
            except Exception as exc:
                item = {"name": name, "jobs": [], "note": "采集异常 %s" % str(exc)[:60],
                        "nature": "未知", "city": "未知", "industry": "未知"}
            if item.get("jobs") or "没抓到页面" not in (item.get("note") or ""):
                break
        item.setdefault("id", "web-" + str(abs(hash(name)) % 10 ** 8))
        item.setdefault("name", name)
        save_item(name, item)
        return name, item, time.time() - st

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(work, n): n for n in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            name, item, dt = fut.result()
            results.append((name, item, dt))
            n_jobs = len(item.get("jobs") or [])
            if scope_only:
                print("[%d/%d] %-28s 主营=%s %.1fs" % (
                    i, len(todo), name[:26],
                    "有" if str(item.get("biz_scope") or "") not in ("", "未知") else "无", dt),
                    flush=True)
                continue
            if n_jobs:
                ok += 1
            else:
                miss += 1
                miss_names.append(name)
            line = "[%d/%d] %s %-28s jobs=%-3d %s %.0fs" % (
                i, len(todo), "OK " if n_jobs else "MISS", name[:26], n_jobs,
                item.get("city") or "?", dt)
            print(line, flush=True)
            lines.append(line)
            with LOCK:
                with open(PROGRESS, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    if not scope_only:
        try:
            with open(MISS, "w", encoding="utf-8") as f:
                json.dump(miss_names, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    dur = time.time() - t0
    if scope_only:
        filled = sum(1 for n, it, _ in results if str(it.get("biz_scope") or "") not in ("", "未知"))
        summary = "\n完成：主营补出 %d 家 / 仍为未知 %d 家，用时 %.1f 分钟\n" % (
            filled, len(results) - filled, dur / 60)
    else:
        summary = "\n完成：有岗位 %d 家 / 0 岗 %d 家，用时 %.1f 分钟\n" % (ok, miss, dur / 60)
        if miss_names:
            summary += "未抓到岗位的名单已写入 %s（可用 --retry-miss 重跑）\n" % os.path.basename(MISS)
    print(summary)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
