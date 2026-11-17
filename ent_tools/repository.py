# -*- coding: utf-8 -*-
"""sqlite 连接的唯一出口（2026-11-17 收口）。

**为什么要这个文件**：收口时全仓 46 处 `sqlite3.connect` 散在 24 个脚本里，各自
设 `row_factory`、各自 `try/finally close`。重复样板只是表面问题，真正踩过的是这四类：

  ① 漏 close → 文件被占，下一步重建报 `database is locked`；
  ② 忘了 commit → 写库「成功」了但重跑发现没变（.db 里没有）；
  ③ 路径各拼各的 → 同一个库在三个文件里有三种拼法，改目录得改三处；
  ④ 只做 `connect()` 探测 → 在路径上留下 0 字节文件，之后 `SELECT` 抛
     `no such table`，整个流水线在第一步崩掉（fair_bridge.load_enrich 的注释里
     记着这次翻车）。

这里只做三件事：给库路径起名字、给连接做成上下文管理器（正常提交 / 异常回滚 /
一定关闭）、给读写提供短路径。**不封装 sqlite 的能力**：要游标、要 PRAGMA、
要事务里做十件事，就 `with connect() as con:` 自己写。

约定：新代码不要直接 `sqlite3.connect`。tests/test_no_raw_sqlite.py 会扫活代码，
剩下没迁完的文件列在 ALLOWLIST 里，迁完一个删一条。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ── 库路径：只有这几个库，别再在每个脚本里拼 ──────────────────────────
LIVE_DB = os.path.join(DATA, "careersafari.db")          # 活库（岗位、画像、命中）
ENRICH_DB = os.path.join(DATA, "fair2026_enrich.db")     # 145 家增补档案（含行业码）
V42_DB = os.path.join(DATA, "fair2026_v42.db")           # v4.2 档案
CAMPUS_DB = os.path.join(DATA, "campus_recheck.db")      # 校招来源核验
CODEBOOK_DB = os.path.join(DATA, "codebook.db")          # 岗位层级/职能码表


@contextmanager
def connect(path=None, row_factory=True):
    """唯一的连接入口。

    正常退出提交、抛异常回滚、无论如何关闭。读操作走这里也不会多花什么
    （提交空事务是 no-op），所以调用方不必区分读写。
    """
    p = path or LIVE_DB
    con = sqlite3.connect(p)
    if row_factory:
        con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def rows(path, sql, params=()):
    """查询 → list[dict]。页面/报告要的是能直接 json 化的字典。"""
    with connect(path) as con:
        return [dict(r) for r in con.execute(sql, params)]


def one(path, sql, params=()):
    """查询 → 首行 dict，没有则 None。"""
    with connect(path) as con:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r is not None else None


def scalar(path, sql, params=(), default=None):
    """查询 → 第一行第一列（count / max / PRAGMA 这类）。"""
    with connect(path, row_factory=False) as con:
        r = con.execute(sql, params).fetchone()
        return r[0] if r else default


def execute(path, sql, params=()):
    """写一行（或 DDL）→ 影响行数。"""
    with connect(path) as con:
        return con.execute(sql, params).rowcount


def execute_many(path, sql, seq):
    """批量写 → 影响行数。"""
    with connect(path) as con:
        return con.executemany(sql, seq).rowcount


def table_exists(path, name):
    """表在不在。空库/只做过探测的库也会走到这里，别让 `no such table` 冒到上层。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    return scalar(path, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                  (name,)) is not None


def columns(path, table):
    """表字段名列表（缺列补列的迁移脚本要用）。"""
    return [r["name"] for r in rows(path, "PRAGMA table_info(%s)" % table)]
