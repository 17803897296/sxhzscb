# -*- coding: utf-8 -*-
"""
测试数据库（2026-11-13 新增）：记录「学生填了什么」和「系统排出了什么」。

用途是给自己的实验留证据，不是给学生看的功能。
现场跑双选会时，每个有效 session 落一条快照：
  - 输入侧：完整画像 JSON（含硬约束原话、教培接受度、七维权重、意向岗位）
  - 输出侧：排名前 N 的企业、分数、名次，以及被硬约束剔除的名单
之后做分析时，直接用 SQL 就能算出「换了硬约束，前十名变了多少」。

与 fair_profiles 的区别：
  fair_profiles 只保留一份「当前生效画像」，会被覆盖；
  test_sessions 是只追加的实验日志，用来做前后对比。
  允许按 id 删除单条（测试跑出来的脏数据要能清掉，只留真人记录），
  但不提供「一键清空」，避免误删。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "careersafari.db")

DDL = """
CREATE TABLE IF NOT EXISTS test_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    fair_id        TEXT NOT NULL DEFAULT '',
    tag            TEXT NOT NULL DEFAULT '',   -- 自填标签：会场名 / 组别
    sid            TEXT NOT NULL DEFAULT '',   -- 问卷星编号，用于 join 前后测
    major          TEXT NOT NULL DEFAULT '',
    hard_filter    TEXT NOT NULL DEFAULT '',
    accept_no_major INTEGER NOT NULL DEFAULT 0,
    weights_json   TEXT NOT NULL DEFAULT '{}',
    intended_json  TEXT NOT NULL DEFAULT '[]',
    profile_json   TEXT NOT NULL DEFAULT '{}',
    top_json       TEXT NOT NULL DEFAULT '[]',
    dropped_json   TEXT NOT NULL DEFAULT '[]',
    n_rows         INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_test_created ON test_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_test_sid ON test_sessions(sid);
"""


def connect(db=DB):
    con = sqlite3.connect(db, timeout=10)
    con.executescript(DDL)
    return con


def save(payload: dict, profile_doc: dict = None, tag: str = "", sid: str = "",
         note: str = "", top_n: int = 12, db=DB) -> dict:
    """落一条测试记录。payload 是 fair_bridge.build() 的输出。"""
    payload = payload or {}
    prof = (payload.get("profile") or {})
    hf = payload.get("hard_filter") or {}
    rows = payload.get("rows") or []

    top = [{"rank": r.get("rank"), "name": r.get("name"), "score": r.get("score"),
            "city": r.get("city"), "industry": r.get("industry"),
            "state": r.get("state")}
           for r in rows[:top_n]]

    con = connect(db)
    try:
        cur = con.execute(
            "INSERT INTO test_sessions(created_at,fair_id,tag,sid,major,hard_filter,"
            "accept_no_major,weights_json,intended_json,profile_json,top_json,"
            "dropped_json,n_rows,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"),
             payload.get("active_fair_id", ""), tag or "", sid or "",
             prof.get("major", ""), hf.get("raw_text", ""),
             1 if hf.get("accept_no_major") else 0,
             json.dumps(payload.get("weights") or {}, ensure_ascii=False),
             json.dumps(prof.get("intended_jobs") or [], ensure_ascii=False),
             json.dumps(profile_doc or {}, ensure_ascii=False),
             json.dumps(top, ensure_ascii=False),
             json.dumps(hf.get("dropped") or [], ensure_ascii=False),
             len(rows), note or ""))
        con.commit()
        return {"ok": True, "id": cur.lastrowid, "n_rows": len(rows)}
    finally:
        con.close()


def listing(limit: int = 50, db=DB) -> list:
    """列出最近的测试记录（不含大字段，方便页面/命令行查看）。"""
    con = connect(db)
    try:
        rows = con.execute(
            "SELECT id,created_at,fair_id,tag,sid,major,hard_filter,accept_no_major,"
            "n_rows,note FROM test_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "created_at": r[1], "fair_id": r[2], "tag": r[3],
                 "sid": r[4], "major": r[5], "hard_filter": r[6],
                 "accept_no_major": bool(r[7]), "n_rows": r[8], "note": r[9]}
                for r in rows]
    finally:
        con.close()


def detail(sid_id: int, db=DB) -> dict:
    con = connect(db)
    try:
        r = con.execute(
            "SELECT id,created_at,fair_id,tag,sid,major,hard_filter,accept_no_major,"
            "weights_json,intended_json,profile_json,top_json,dropped_json,n_rows,note"
            " FROM test_sessions WHERE id=?", (sid_id,)).fetchone()
        if not r:
            return {}
        keys = ["id", "created_at", "fair_id", "tag", "sid", "major", "hard_filter",
                "accept_no_major", "weights", "intended", "profile", "top",
                "dropped", "n_rows", "note"]
        out = dict(zip(keys, r))
        for k in ("weights", "intended", "profile", "top", "dropped"):
            try:
                out[k] = json.loads(out[k])
            except Exception:
                out[k] = None
        return out
    finally:
        con.close()


def delete(session_id: int, db=DB) -> dict:
    """按 id 删除一条测试记录。只删指定 id，绝不批量清空。"""
    try:
        rid = int(session_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "记录 id 不合法"}
    con = connect(db)
    try:
        cur = con.execute("DELETE FROM test_sessions WHERE id=?", (rid,))
        con.commit()
        return {"ok": True, "deleted": cur.rowcount, "id": rid}
    finally:
        con.close()
