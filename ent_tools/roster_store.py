# -*- coding: utf-8 -*-
"""参会企业名单的落库（2026-11-18）。

**为什么需要它**：企业端导入名单后，那 132 个名字一直只活在浏览器的
`localStorage` 里（`match.js` 只 `setItem`、从来不读回来），服务端一无所知。
于是点「去匹配端看排行」，匹配端读的仍是上一份 payload（145 家全库的排行），
而不是「这场双选会名单里的企业」——「现在匹配池里共 132 家」那句话是许空头支票。

落库后：`fair_bridge.build()` 按名单过滤榜单，匹配端与报告页看到的就是
「这场会的企业」。没抓到岗位 / 没进榜的名单企业不凭空消失，进 payload 的
`roster.missing` 里逐条给原因。

表按 `fair_id` 存，不同画像/场次互不覆盖；空名单 = 不按名单过滤（回到全库口径），
所以冷克隆、CI 构建（种子里没有这张表）行为与改动前逐字节一致。
"""
from __future__ import annotations

import time

import repository

DDL = """
CREATE TABLE IF NOT EXISTS fair_roster (
    fair_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    note       TEXT,
    created_at TEXT,
    PRIMARY KEY (fair_id, name)
)
"""


def _ensure(con):
    con.execute(DDL)


def set_names(fair_id, names, notes=None):
    """整表替换某个画像的名单；返回写入条数。

    整表替换而不是增量：名单是「这一场的参会企业」，重新导一次就该是新的完整
    名单，增量会把上一场残留的名字留下来（与 `sync_profile_seed` 的清空同理）。
    """
    fair_id = str(fair_id or "").strip()
    if not fair_id:
        return 0
    notes = notes or {}
    clean, seen = [], set()
    for n in names or []:
        s = str(n or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        clean.append((fair_id, s, str(notes.get(s) or ""), time.strftime("%Y-%m-%d %H:%M:%S")))
    with repository.connect() as con:
        _ensure(con)
        con.execute("DELETE FROM fair_roster WHERE fair_id=?", (fair_id,))
        if clean:
            con.executemany(
                "INSERT OR REPLACE INTO fair_roster(fair_id,name,note,created_at) "
                "VALUES (?,?,?,?)", clean)
    return len(clean)


def get_names(fair_id):
    """名单里的企业名（按写入顺序）。表不存在 / 空名单 → 空列表。"""
    fair_id = str(fair_id or "").strip()
    if not fair_id:
        return []
    try:
        with repository.connect() as con:
            rows = con.execute(
                "SELECT name FROM fair_roster WHERE fair_id=? ORDER BY rowid",
                (fair_id,)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        # 种子里没有这张表（冷克隆 / CI）：当「没导入名单」，不报错、不过滤。
        return []


def get_notes(fair_id):
    fair_id = str(fair_id or "").strip()
    if not fair_id:
        return {}
    try:
        with repository.connect() as con:
            return {n: (note or "") for n, note in con.execute(
                "SELECT name, note FROM fair_roster WHERE fair_id=?", (fair_id,))}
    except Exception:
        return {}


def clear(fair_id):
    fair_id = str(fair_id or "").strip()
    if not fair_id:
        return 0
    try:
        with repository.connect() as con:
            _ensure(con)
            n = con.execute("DELETE FROM fair_roster WHERE fair_id=?", (fair_id,)).rowcount
        return n or 0
    except Exception:
        return 0
