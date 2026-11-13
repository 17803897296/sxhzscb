# -*- coding: utf-8 -*-
"""
v6 · 采集端 -> 数据库 -> 匹配端 的写库闭环。

之前 fair_profiles 里只有 load_to_db.py 塞的一条示例画像，页面导出的画像
只写进浏览器 localStorage（saved:false）。本模块提供两条真实入库通道：

  1) 离线双击页面：下载画像 JSON 后
     python -X utf8 fair_ingest.py --file 画像.json
  2) 同源服务模式：fair_server.py 的 POST /api/fair/profile 自动调用本模块

写库后写 fair_active 表标记「当前生效画像」，fair_bridge.read_db() 优先读它，
再重新生成 fair_payload.json —— 匹配端报告因此读到的确实是采集端最新画像，
而不是生成时冻结的快照。

运行：
    python -X utf8 fair_ingest.py --demo           # 造一个不同画像验证闭环
    python -X utf8 fair_ingest.py --file xx.json   # 导入页面导出的画像
    python -X utf8 fair_ingest.py --stats          # 查看库里画像与当前生效项
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

PROFILE_DDL = """
CREATE TABLE IF NOT EXISTS fair_profiles (
    fair_id         TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    persona         TEXT NOT NULL DEFAULT '',
    completeness    INTEGER NOT NULL DEFAULT 0,
    document_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fair_created ON fair_profiles(created_at);
"""
ACTIVE_DDL = """
CREATE TABLE IF NOT EXISTS fair_active (
    id          TEXT PRIMARY KEY,
    fair_id     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""
# v7：现场核实结果回流。学生点「招 / 不招」后写这里，
# fair_bridge 重建 payload 时读它，反向修正企业端证据等级。
FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS fair_feedback (
    name        TEXT PRIMARY KEY,
    result      TEXT NOT NULL,          -- yes / no
    note        TEXT NOT NULL DEFAULT '',
    fair_id     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
"""


def connect(db=DB):
    con = sqlite3.connect(db)
    con.executescript(PROFILE_DDL)
    con.executescript(ACTIVE_DDL)
    con.executescript(FEEDBACK_DDL)
    return con


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def normalize(doc):
    """兼容页面导出的三种形态：直接 doc / {document: doc} / {answers: ...}。"""
    if not isinstance(doc, dict):
        raise ValueError("画像必须是 JSON 对象")
    if isinstance(doc.get("document"), dict):
        doc = doc["document"]
    if "answers" not in doc and "identity" not in doc:
        raise ValueError("不像一份 fair_profile（缺 answers / identity）")
    doc.setdefault("document_type", "fair_profile")
    doc.setdefault("fair_id", "FAIR-" + time.strftime("%Y%m%d%H%M%S"))
    doc.setdefault("generated_at", _now())
    doc.setdefault("quality", {})
    doc.setdefault("privacy", {})
    doc["privacy"]["storage"] = "sqlite:fair_profiles"
    return doc


def save_profile(doc, db=DB, rebuild=True):
    """写库 + 设为当前生效画像 +（可选）重建三端 payload。"""
    doc = normalize(doc)
    con = connect(db)
    try:
        score = int(doc.get("quality", {}).get("score") or 0)
        person = (doc.get("identity") or {}).get("major", "")
        con.execute(
            "INSERT OR REPLACE INTO fair_profiles"
            "(fair_id,created_at,persona,completeness,document_json) VALUES (?,?,?,?,?)",
            (doc["fair_id"], _now(), person, score,
             json.dumps(doc, ensure_ascii=False)))
        con.execute(
            "INSERT OR REPLACE INTO fair_active(id,fair_id,updated_at) VALUES ('current',?,?)",
            (doc["fair_id"], _now()))
        con.commit()
    finally:
        con.close()

    result = {"ok": True, "saved": True, "fair_id": doc["fair_id"],
              "storage": "sqlite:fair_profiles", "active": True,
              "major": person, "completeness": score}
    if rebuild:
        try:
            import fair_bridge
            payload = fair_bridge.build()
            result["payload_rebuilt"] = True
            result["weights"] = payload["weights"]
            result["profile"] = payload["profile"]
        except Exception as exc:                       # 写库成功即可，重建失败不吞掉结果
            result["payload_rebuilt"] = False
            result["rebuild_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return result


def save_feedback(name, result, note="", fair_id="", db=DB, rebuild=True):
    """现场核实结果回流：yes -> 升级为明确命中；no -> 排到队列尾并写明已顺延。"""
    if result not in ("yes", "no"):
        raise ValueError("result 只能是 yes / no")
    con = connect(db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO fair_feedback(name,result,note,fair_id,updated_at)"
            " VALUES (?,?,?,?,?)",
            (name, result, note or "", fair_id or "", _now()))
        con.commit()
    finally:
        con.close()
    out = {"ok": True, "name": name, "result": result, "storage": "sqlite:fair_feedback"}
    if rebuild:
        try:
            import fair_bridge
            p = fair_bridge.build()
            out["payload_rebuilt"] = True
            out["feedback_stats"] = p["stats"].get("feedback")
        except Exception as exc:
            out["payload_rebuilt"] = False
            out["rebuild_error"] = str(exc)[:160]
    return out


def read_feedback(db=DB):
    con = connect(db)
    try:
        return {r[0]: {"result": r[1], "note": r[2], "fair_id": r[3],
                       "updated_at": r[4]}
                for r in con.execute(
                    "SELECT name,result,note,fair_id,updated_at FROM fair_feedback")}
    finally:
        con.close()


def stats(db=DB):
    con = connect(db)
    try:
        cur = con.execute("SELECT fair_id,created_at,persona,completeness"
                          " FROM fair_profiles ORDER BY created_at DESC")
        rows = [{"fair_id": r[0], "created_at": r[1], "persona": r[2],
                 "completeness": r[3]} for r in cur.fetchall()]
        act = con.execute("SELECT fair_id,updated_at FROM fair_active WHERE id='current'").fetchone()
        fb = con.execute("SELECT result,COUNT(*) FROM fair_feedback GROUP BY result").fetchall()
        return {"profiles": rows, "active": {"fair_id": act[0], "updated_at": act[1]} if act else None,
                "feedback": {r[0]: r[1] for r in fb}}
    finally:
        con.close()


def demo_doc():
    """一份与默认示例明显不同的画像，用于验证「改画像 -> 报告真的变」。"""
    return {
        "document_type": "fair_profile", "document_version": "1.1.0",
        "fair_id": "FAIR-V6-DEMO", "generated_at": _now(),
        "identity": {"school": "南京大学", "major": "金融工程", "degree": "硕士"},
        "fair": {"home_city": "杭州", "stage_detail": "秋招校招",
                 "target_company_types": ["央企", "国企"]},
        "answers": {
            "intended_jobs": ["量化研究", "行业研究", "数据分析师"],
            "region": "上海", "direction_interest": "量化与投研",
            "tri_weights": {"city": 40, "home": 10, "company_type": 20,
                            "industry": 20, "scale": 10},
            "values": {"city": 40, "home": 10, "company_type": 20,
                       "industry": 20, "scale": 10},
        },
        "resume": {"extraction": {"experiences": [
            {"text": "某券商研究所实习：用 Python 做多因子回测与行业景气度跟踪"}]}},
        "quality": {"score": 88, "level": "可直接进入匹配"},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="页面导出的画像 JSON 文件")
    ap.add_argument("--json", help="直接传画像 JSON 字符串")
    ap.add_argument("--demo", action="store_true", help="用一份不同画像做闭环演示")
    ap.add_argument("--stats", action="store_true", help="只看库里状态")
    ap.add_argument("--no-rebuild", action="store_true")
    a = ap.parse_args()

    if a.stats:
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
        return
    if a.demo:
        doc = demo_doc()
    elif a.file:
        with open(a.file, encoding="utf-8") as f:
            doc = json.load(f)
    elif a.json:
        doc = json.loads(a.json)
    else:
        ap.error("需要 --file / --json / --demo / --stats 之一")
        return

    out = save_profile(doc, rebuild=not a.no_rebuild)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
