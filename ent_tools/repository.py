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

# 活库就绪标记：确认过一次就不再重复 stat（连接是高频路径）。
_live_ready = False


def ensure_live_db(path=None, quiet=True):
    """活库缺席时，从只读种子复制一份出来。

    为什么这件事必须在这里做（2026-11-18 冷克隆实测）：仓库只跟踪
    `careersafari.db.seed`，活库不入库。原本「缺就复制」只写在
    `fair_server` 的启动钩子里，于是只有**跑服务**这条路能自愈：
    新同学 clone 下来直接 `pytest`，`fair_bridge` 走 `repository.connect(LIVE_DB)`
    → 文件不存在 → sqlite 顺手建一个 0 字节空库 → `no such table: company_jobs`
    → 两个复检展示行测试直接红，报错还指不到真正的原因。
    `build_fair_page` 另有一套「按顺序探测含 company_jobs 的候选」，那是第三份
    实现。现在统一到这里：谁走唯一出口，谁就自动拿到可用的库。

    只认两种情况需要重建：文件不存在，或残留 0 字节空文件（历史探测留下的）。
    正常活库（有内容）一律不动——这是冷克隆自愈，不是兜底覆盖。

    `path` 可以传别的库（测试用）；只有活库会记忆「已就绪」，避免高频 stat。
    """
    global _live_ready
    # path 可能是 str 也可能是 Path（sync_repo 传的就是 Path）。统一成 str 再拼
    # ".seed"：Path + str 会直接抛 TypeError，而这里在 try 外层、报错只会变成
    # 一句「种子同步失败」，看不出真因（2026-11-18 就是这么卡住的）。
    p = str(path or LIVE_DB)
    is_live = (p == str(LIVE_DB))
    if is_live and _live_ready:
        return False
    seed = p + ".seed"
    if os.path.isfile(p) and os.path.getsize(p) > 0:
        if is_live:
            _live_ready = True
        return False
    if not os.path.isfile(seed):
        return False
    import shutil
    shutil.copy2(seed, p)
    if is_live:
        _live_ready = True
    if not quiet:
        print("  已从只读种子初始化本地库 %s" % os.path.basename(p))
    return True


@contextmanager
def connect(path=None, row_factory=True):
    """唯一的连接入口。

    正常退出提交、抛异常回滚、无论如何关闭。读操作走这里也不会多花什么
    （提交空事务是 no-op），所以调用方不必区分读写。

    连活库前先 `ensure_live_db()`：冷克隆第一次连接会自动从种子复制出库，
    不会再出现「sqlite 建了 0 字节空库、下一条 SELECT 报 no such table」。
    其他库（增补档案 / 校招核验）没有 `.seed` 伴随文件，这一步对它们是 no-op。
    """
    p = path or LIVE_DB
    ensure_live_db(p)
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
