# -*- coding: utf-8 -*-
"""给 company_jobs 里还没有匹配结论的企业补 llm_match 记录。

fair_bridge 读 llm_match 表拿 state / matched_jobs / reason / verify_question。
换名单后新企业在这张表里没有记录，报告会统一显示「未匹配」，所以采完岗位要补一次。

用法：
    python -X utf8 match_fill.py            # 只补缺的
    python -X utf8 match_fill.py --force    # 全部重跑
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
sys.path.insert(0, HERE)

import llm_match  # noqa: E402


def current_profile():
    """取当前生效画像里能喂给 llm_match 的字段，缺的用默认值兜。"""
    base = dict(llm_match.DEFAULT_PROFILE)
    try:
        import fair_bridge
        companies, _llm, raw = fair_bridge.read_db()
        p = fair_bridge.profile_from(raw)
        base["major"] = p.get("major") or base["major"]
        base["experiences"] = p.get("experiences") or base["experiences"]
        base["intended_jobs"] = p.get("intended_jobs") or base["intended_jobs"]
        base["region"] = p.get("region") or base["region"]
        base["home_city"] = p.get("home_city") or base["home_city"]
    except Exception as exc:
        print("读生效画像失败，用默认画像：%s" % str(exc)[:80])
    return base


def fill(force=False, batch=8, workers=5, skip_sources=("未知1", "未知2"), profile=None):
    """重算 llm_match（hit / inferred / miss）并写库。

    被 fair_server 在「提交画像」时后台调用：换画像后让匹配结论随画像走，
    而不是停留在上次全量重算的旧画像上。

    skip_sources：无具体岗位的企业（未知1=有粗略业务描述 / 未知2=联网仍无岗）
    不进这里——它们的命中/推断由 fair_bridge.unknown_infer 按当前画像动态判定，
    这里重算会覆盖掉、且不带「未查到」语义，反而破坏设计。
    """
    cfg = llm_match.load_config()
    if not cfg["key"]:
        print("没有 API key，跳过匹配重算")
        return None

    con = sqlite3.connect(DB)
    all_rows = {json.loads(ij)["name"]: json.loads(ij)
                for (ij,) in con.execute("SELECT item_json FROM company_jobs")}
    skipped = sum(1 for d in all_rows.values() if d.get("jobSource") in skip_sources)
    companies = {n: d for n, d in all_rows.items() if d.get("jobSource") not in skip_sources}
    have = {r[0] for r in con.execute("SELECT name FROM llm_match")}
    todo = list(companies) if force else [n for n in companies if n not in have]
    print("待匹配 %d 家（跳过无具体岗 %d 家；库内共 %d 家）"
          % (len(todo), skipped, len(all_rows)))
    if not todo:
        con.close()
        return {"stats": {"total": 0}}

    prof = profile or current_profile()
    print("画像：%s / 意向 %s" % (prof.get("major"), "、".join(prof.get("intended_jobs") or [])))
    batch_list = [companies[n] for n in todo]
    out = llm_match.run(prof, batch_list, cfg, batch=batch, workers=workers)
    rows = out.get("rows") or []
    print("模型返回 %d 条，统计 %s" % (len(rows), json.dumps(out.get("stats", {}), ensure_ascii=False)))

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        con.execute("INSERT OR REPLACE INTO llm_match(name,state,priority,matched_jobs,reason,"
                    "gaps,verify_question,has_jd,generated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (r["name"], r.get("state", "miss"), int(r.get("priority", 3) or 3),
                     json.dumps(r.get("matched_jobs") or [], ensure_ascii=False),
                     r.get("reason", ""),
                     json.dumps(r.get("gaps") or [], ensure_ascii=False),
                     r.get("verify_question", ""), 1 if r.get("has_jd") else 0, now))
    con.commit()
    got = {r["name"] for r in rows}
    left = [n for n in todo if n not in got]
    con.close()
    print("写入 %d 条；模型未返回 %d 家：%s" % (len(rows), len(left), "、".join(left[:8])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    fill(force=args.force, batch=args.batch, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
