# -*- coding: utf-8 -*-
"""把小号交付的 fair2026.db 岗位事实汇总到活库 careersafari.db。

只取 job_status=known 的 matched_jobs（inferred_jobs 是规则建议，绝不并入）。
每条岗位带原始 source_url；证据另存 job_evidence 表，便于回溯。

用法：
    set FAIR_HANDOFF_DB=<交付库路径>\fair2026.db   （不设则找 ../_handoff/fair2026.db）
    python -X utf8 merge_handoff_jobs.py --check   # 只预览差异
    python -X utf8 merge_handoff_jobs.py --apply   # 落库
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")

# 交付库位置。原来这里是写死的 C:\Users\Lenovo\Desktop\...，
# 在写它的那台机器上跑得通，换一台就报「找不到交付库」，而且看代码看不出
# 该改哪一行。改成：环境变量优先，其次按相对位置找。
#   set FAIR_HANDOFF_DB=D:\某处\fair2026.db
HANDOFF = (os.environ.get("FAIR_HANDOFF_DB")
           or os.path.join(os.path.dirname(HERE), "_handoff", "fair2026.db"))


def norm(t):
    """岗位名归一，用于去重：去空白、统一括号、去届别/编号尾巴。"""
    s = (t or "").strip()
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"20\d{2}届?(校园|秋招|春招)?", "", s)
    s = re.sub(r"\(MJ\d+\)", "", s, flags=re.I)
    return s.lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.apply):
        ap.print_help()
        return 1

    if not os.path.exists(HANDOFF):
        print("找不到交付库：%s" % HANDOFF, file=sys.stderr)
        return 1

    src = sqlite3.connect(HANDOFF)
    dst = sqlite3.connect(DB, timeout=20)

    # 小号岗位事实（只认 known，不认 inferred）
    facts = {}
    for name, status, mj in src.execute("SELECT name,job_status,matched_jobs FROM companies"):
        if status != "known":
            continue
        js = json.loads(mj or "[]")
        facts[name] = [j for j in js if (j.get("title") or "").strip()]

    # 证据表：company,title,source_url,source_type,confidence,claim
    ev = src.execute(
        "SELECT company,value,source_url,source_type,claim,confidence "
        "FROM evidence WHERE field='matched_jobs'").fetchall()

    # URL 索引：同企业同岗位名 -> URL
    url_idx = {}
    for c, v, u, st, cl, cf in ev:
        url_idx.setdefault((c, norm(v)), (u, st, cl, cf))

    mine = {n: json.loads(ij) for n, ij in
            dst.execute("SELECT name,item_json FROM company_jobs")}
    missing = sorted(set(facts) - set(mine))
    if missing:
        print("交付库里这些企业不在活库，已跳过：%s" % "、".join(missing), file=sys.stderr)

    add = dup = 0
    touched = []
    plans = {}
    for name, d in mine.items():
        old = d.get("jobs") or []
        seen = {norm(j.get("title") if isinstance(j, dict) else j) for j in old}
        patch = []
        for j in facts.get(name, []):
            title = (j.get("title") or "").strip()
            k = norm(title)
            if not k:
                continue
            if k in seen:
                dup += 1
                continue
            seen.add(k)
            u, st, cl, cf = url_idx.get((name, k), (j.get("source_url", ""), "", "", ""))
            patch.append({
                "title": title,
                "requirements": "",
                "headcount": "未知",
                "urgency": "未知",
                "group": "",
                "salary": "未知",
                "source_url": u or j.get("source_url", ""),
                "source_type": st or "official_or_relay_page",
                "evidence_confidence": cf or j.get("confidence", ""),
                "source": "handoff_20261114",
            })
            add += 1
        if patch:
            plans[name] = old + patch
            touched.append(name)

    zero_before = sum(1 for d in mine.values() if not (d.get("jobs") or []))
    fixed = [n for n in touched if not (mine[n].get("jobs") or [])]
    total_jobs = sum(len(v) for v in plans.values()) + sum(
        len(d.get("jobs") or []) for n, d in mine.items() if n not in plans)

    print("活库企业 %d 家；交付库 known %d 家；证据 %d 条" % (len(mine), len(facts), len(ev)))
    print("新增岗位 %d 条；与原有岗位重名去重 %d 条；涉及 %d 家" % (add, dup, len(touched)))
    print("0 岗企业 %d 家 -> %d 家（本次补上 %d 家）" %
          (zero_before, zero_before - len(fixed), len(fixed)))
    print("合并后岗位总数 %d 条" % total_jobs)
    print()
    print("=== 补上的 0 岗企业 ===")
    for n in fixed:
        print("  + %-30s %2d 岗" % (n[:30], len(plans[n])))

    if not args.apply:
        print("\n（预览模式，未写库。加 --apply 落库）")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(DATA, "careersafari.db.bak_" + stamp)
    shutil.copy2(DB, bak)
    print("\n已备份 -> %s" % bak)

    dst.execute("""CREATE TABLE IF NOT EXISTS job_evidence(
        id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT, title TEXT,
        source_url TEXT, source_type TEXT, claim TEXT, confidence TEXT,
        batch TEXT)""")
    dst.execute("DELETE FROM job_evidence WHERE batch='handoff_20261114'")
    dst.executemany(
        "INSERT INTO job_evidence(company,title,source_url,source_type,claim,confidence,batch) "
        "VALUES (?,?,?,?,?,?,?)",
        [(c, v, u, st, cl, cf, "handoff_20261114") for c, v, u, st, cl, cf in ev])

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for name, jobs in plans.items():
        d = dict(mine[name])
        d["jobs"] = jobs
        d["handoff_merged_at"] = now
        dst.execute("UPDATE company_jobs SET item_json=?,updated_at=? WHERE name=?",
                    (json.dumps(d, ensure_ascii=False), now, name))
    dst.commit()
    print("已写入：company_jobs 更新 %d 家，job_evidence 导入 %d 条" % (len(plans), len(ev)))

    check = {n: json.loads(ij) for n, ij in
             dst.execute("SELECT name,item_json FROM company_jobs")}
    print("复核：企业 %d，有岗 %d，0岗 %d，岗位总数 %d，带URL岗位 %d" % (
        len(check), sum(1 for d in check.values() if d.get("jobs")),
        sum(1 for d in check.values() if not d.get("jobs")),
        sum(len(d.get("jobs") or []) for d in check.values()),
        sum(1 for d in check.values() for j in (d.get("jobs") or [])
            if isinstance(j, dict) and j.get("source_url"))))
    dst.close()
    src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
