# -*- coding: utf-8 -*-
"""
把已经跑出来的数据真正写进「原代码里的数据库」。

现状（本脚本存在的原因）：
  - ent_tools/job_store.py 默认 NullJobStore，lookup 永远 None、save 空操作；
  - _handoff/shuangxuan/app/careersafari/db.py 有完整 SQLite schema，但库文件从未创建；
  - 采集页离线垫片 saved:false，只写 localStorage。
  => 之前所有数据都躺在 JSON 里，没有一条进 SQLite。

本脚本：
  1. 复用 careersafari/db.py 里的 SCHEMA_SQL（原代码原表结构，不另造 schema）；
  2. 建库 ent_tools/data/careersafari.db（放在会进 Git 的目录，不放被忽略的 _handoff）；
  3. 补两张原 schema 没有的表：company_jobs（给 JobStore 用）、llm_match（模型结果）；
  4. 灌入 118 家企业、277 条岗位、118 条模型匹配结果、1 份示例画像。

用法：
    python -X utf8 load_to_db.py            # 建库 + 灌入（可重复运行，INSERT OR REPLACE）
    python -X utf8 load_to_db.py --stats    # 只看统计
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA, "careersafari.db")
HANDOFF_DB = os.path.join(ROOT, "_handoff", "shuangxuan", "app", "careersafari", "db.py")

EXTRA_SQL = """
CREATE TABLE IF NOT EXISTS company_jobs (
    name        TEXT PRIMARY KEY,
    item_json   TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_match (
    name            TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 3,
    matched_jobs    TEXT NOT NULL DEFAULT '[]',
    reason          TEXT NOT NULL DEFAULT '',
    gaps            TEXT NOT NULL DEFAULT '[]',
    verify_question TEXT NOT NULL DEFAULT '',
    has_jd          INTEGER NOT NULL DEFAULT 0,
    generated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_state ON llm_match(state, priority);
"""


def original_schema() -> str:
    """从原代码里取 SCHEMA_SQL，不 import 那个包（它依赖 pydantic，本机没装）。"""
    with open(HANDOFF_DB, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'SCHEMA_SQL\s*=\s*"""([\s\S]*?)"""', src)
    if not m:
        raise RuntimeError("没能从 careersafari/db.py 提取 SCHEMA_SQL")
    return m.group(1)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load():
    companies = json.load(open(os.path.join(DATA, "companies.json"), encoding="utf-8"))
    cache = json.load(open(os.path.join(DATA, "lookup_cache.json"), encoding="utf-8"))
    match = json.load(open(os.path.join(DATA, "match_llm.json"), encoding="utf-8"))
    return companies, cache, match


def build(con, companies, cache, match):
    ts = now()
    # 1) 企业岗位主表（给 JobStore.lookup 用）
    for c in companies:
        con.execute("INSERT OR REPLACE INTO company_jobs(name,item_json,updated_at) VALUES (?,?,?)",
                    (c["name"], json.dumps(c, ensure_ascii=False), ts))

    # 2) 原表 jd_records：每家一条，正文是岗位标题 + 要求原文
    for c in companies:
        jobs = c.get("jobs") or []
        text = "\n".join("%s | %s" % (j.get("title", ""), j.get("requirements", ""))
                         for j in jobs) or "（未查到岗位记录）"
        jd_id = "JD-" + (c.get("id") or uuid.uuid4().hex[:8])
        con.execute(
            "INSERT OR REPLACE INTO jd_records"
            "(jd_id,raw_text,source_type,source_url,captured_date,ingested_by,status,credibility,parse_error,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jd_id, text, "company_list", c.get("source", ""), c.get("date", "") or "未知",
             "load_to_db.py", "parsed" if jobs else "no_jobs",
             c.get("jobSource", "未知"), "", ts))

        # 3) 原表 positions：每个岗位一条
        for k, j in enumerate(jobs):
            pid = "%s-P%03d" % (jd_id, k)
            con.execute(
                "INSERT OR REPLACE INTO positions"
                "(position_id,canonical_name,industry,variant_names,work_scenarios,stressors,source_jd_ids,schema_version,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, j.get("title", ""), c.get("industry", "未知"), "[]", "[]", "[]",
                 json.dumps([jd_id], ensure_ascii=False), "v2", ts))

    # 4) 原表 fair_profiles：一份示例学生画像
    profile = {
        "document_type": "fair_profile", "document_version": "1.1.0",
        "fair_id": "FAIR-DEMO-0001", "generated_at": ts,
        "identity": {"school": "南京大学", "major": "管理科学与工程", "degree": "硕士"},
        "answers": {"intended_jobs": ["供应链计划", "数据分析师", "商业分析"]},
        "quality": {"score": 85, "level": "可直接进入匹配"},
        "privacy": {"contact_masked": True, "storage": "sqlite:fair_profiles"},
    }
    con.execute(
        "INSERT OR REPLACE INTO fair_profiles(fair_id,created_at,persona,completeness,document_json)"
        " VALUES (?,?,?,?,?)",
        (profile["fair_id"], ts, "", 85, json.dumps(profile, ensure_ascii=False)))

    # 5) 模型匹配结果
    for r in match["rows"]:
        con.execute(
            "INSERT OR REPLACE INTO llm_match"
            "(name,state,priority,matched_jobs,reason,gaps,verify_question,has_jd,generated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (r["name"], r["state"], int(r["priority"]),
             json.dumps(r.get("matched_jobs", []), ensure_ascii=False),
             r.get("reason", ""),
             json.dumps(r.get("gaps", []), ensure_ascii=False),
             r.get("verify_question", ""), 1 if r.get("has_jd") else 0,
             match.get("generated_at", ts)))

    # lookup_cache 也记一条来源快照，便于追溯
    if isinstance(cache, (list, dict)):
        con.execute(
            "INSERT OR REPLACE INTO source_snapshots"
            "(jd_id,content_hash,source_url,method,captured_at) VALUES (?,?,?,?,?)",
            ("JD-ALL", "lookup_cache:%d" % len(cache),
             "file://data/lookup_cache.json", "json", ts))


def stats(con):
    tables = ["users", "responses", "profiles", "jd_records", "positions",
              "requirements", "fair_profiles", "company_jobs", "llm_match", "source_snapshots"]
    for t in tables:
        try:
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("  %-18s %4d 行" % (t, n))
        except sqlite3.Error as e:
            print("  %-18s (无此表: %s)" % (t, e))
    try:
        print("\n  llm_match 状态分布:",
              dict(con.execute("SELECT state,COUNT(*) FROM llm_match GROUP BY state").fetchall()))
    except sqlite3.Error:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        if not a.stats:
            con.executescript(original_schema())
            con.executescript(EXTRA_SQL)
            companies, cache, match = load()
            build(con, companies, cache, match)
            con.commit()
            print("已建库并灌入：%s" % DB_PATH)
            print("  企业 %d 家 / 岗位 %d 条 / 模型结果 %d 行" % (
                len(companies), sum(len(c.get("jobs") or []) for c in companies),
                len(match["rows"])))
            print()
        print("=== 数据库统计 ===")
        stats(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
