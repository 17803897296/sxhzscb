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
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("FAIR_JS_RENDER", "1")   # JS 招聘页用无头浏览器渲染

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
TODO = os.path.join(DATA, "_roster_todo.json")
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
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="已有岗位的也重采")
    ap.add_argument("--names", help="只采文件里列出的企业（每行一个名称），无视是否有岗位")
    args = ap.parse_args()

    if not portal.setup_model():
        print("缺少 API key，只能走规则抽取", file=sys.stderr)
    else:
        print("模型：%s" % portal.MODEL_ON.get("model"))

    todo = json.load(open(TODO, encoding="utf-8"))
    if args.names:
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

    def work(name):
        st = time.time()
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
            n_jobs = len(item.get("jobs") or [])
            if n_jobs:
                ok += 1
            else:
                miss += 1
            line = "[%d/%d] %s %-28s jobs=%-3d %s %.0fs" % (
                i, len(todo), "OK " if n_jobs else "MISS", name[:26], n_jobs,
                item.get("city") or "?", dt)
            print(line, flush=True)
            lines.append(line)
            with LOCK:
                with open(PROGRESS, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    dur = time.time() - t0
    summary = "\n完成：有岗位 %d 家 / 0 岗 %d 家，用时 %.1f 分钟\n" % (ok, miss, dur / 60)
    print(summary)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
