"""岗位数据库接入点。

历史：这里原来只有接口 + NullJobStore（lookup 永远未命中、save 空操作），
所以「先查库再联网」的链条虽然接上了，但库里从来没有数据。

现在：默认挂 SqliteJobStore，读 ent_tools/data/careersafari.db，
该文件由 load_to_db.py 按 _handoff/.../careersafari/db.py 的原 SCHEMA_SQL 建立并灌入
118 家企业 / 277 条岗位 / 118 条模型匹配结果。

仍可替换：
    job_store.set_store(MyJobStore())     # 换 Postgres / 现有服务

链条只依赖两个方法：
- lookup(company) -> dict | None  命中返回企业岗位记录，未命中返回 None
- save(company, item) -> None     把在线搜索结果写回数据库

记录格式沿用 /api/lookup 的企业记录。
注意：portal.py 的 /api/lookup 会并发调用，这里每次新建连接 + 加锁。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get(
    "FAIR_JOB_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "careersafari.db"),
)


class JobStore:
    """岗位数据库接口。"""

    def lookup(self, company: str):
        raise NotImplementedError

    def save(self, company: str, item: dict):
        raise NotImplementedError


class NullJobStore(JobStore):
    """占位实现：不读、不写，所有请求都回退到在线搜索。"""

    def lookup(self, company):
        return None

    def save(self, company, item):
        return None


class SqliteJobStore(JobStore):
    """真实实现：company_jobs 表，name 为主键，item_json 存整条企业记录。"""

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.lock = threading.Lock()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def lookup(self, company: str):
        if not company or not os.path.isfile(self.path):
            return None
        try:
            with self.lock, self._connect() as con:
                row = con.execute(
                    "SELECT item_json FROM company_jobs WHERE name=?", (company,)).fetchone()
            return json.loads(row["item_json"]) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            return None

    def save(self, company: str, item: dict):
        if not company or not item or not os.path.isfile(self.path):
            return None
        try:
            with self.lock, self._connect() as con:
                con.execute(
                    "INSERT OR REPLACE INTO company_jobs(name,item_json,updated_at) VALUES (?,?,?)",
                    (company, json.dumps(item, ensure_ascii=False),
                     time.strftime("%Y-%m-%d %H:%M:%S")))
        except (sqlite3.Error, ValueError, TypeError):
            return None
        return None


_STORE = None


def get_store() -> JobStore:
    global _STORE
    if _STORE is None:
        _STORE = SqliteJobStore() if os.path.isfile(DB_PATH) else NullJobStore()
    return _STORE


def set_store(store: JobStore):
    """给组员/测试替换数据库实现用。"""
    global _STORE
    _STORE = store
    return _STORE
