# -*- coding: utf-8 -*-
"""采集落库回归：只做增量补全，不做删减。

以前 MISS 用空 jobs 冲掉旧库已核对的岗位，HIT 又用本次少量岗位整体覆盖，
一轮全量重采让 1139 岗掉到 458 岗。这里把两种破坏都钉住。
"""
import json
import os
import sqlite3
import tempfile
import unittest

import collect_roster as C

NAME = "某公司"


def job(title, url=""):
    return {"title": title, "source_url": url, "requirements": "本科"}


class IncrementalSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE company_jobs(name TEXT PRIMARY KEY, item_json TEXT, updated_at TEXT)")
        con.commit()
        con.close()
        self.old_db = C.DB
        C.DB = self.db

    def tearDown(self):
        C.DB = self.old_db
        self.tmp.cleanup()

    def _seed(self, item):
        con = sqlite3.connect(self.db)
        con.execute("INSERT OR REPLACE INTO company_jobs VALUES(?,?,?)",
                    (NAME, json.dumps(item, ensure_ascii=False), ""))
        con.commit()
        con.close()

    def _load(self):
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT item_json FROM company_jobs WHERE name=?", (NAME,)).fetchone()
        con.close()
        return json.loads(row[0]) if row else {}

    def test_miss_keeps_old_jobs_and_source(self):
        self._seed({"name": NAME, "jobs": [job("结构工程师"), job("工艺工程师")],
                    "jobSource": "校招公告", "jobEvidence": "校园招聘原文",
                    "source": "https://old.example.com", "note": "大模型抽取"})
        C.save_item(NAME, {"jobs": [], "jobSource": "未知", "jobEvidence": "未知",
                           "source": "未知", "note": "没抓到页面"})
        got = self._load()
        self.assertEqual([j["title"] for j in got["jobs"]], ["结构工程师", "工艺工程师"])
        self.assertEqual(got["jobSource"], "校招公告")
        self.assertEqual(got["jobEvidence"], "校园招聘原文")
        self.assertEqual(got["source"], "https://old.example.com")
        self.assertEqual(got["note"], "没抓到页面")   # 状态照常更新

    def test_hit_merges_same_title_and_keeps_old_only_jobs(self):
        self._seed({"name": NAME, "jobs": [job("结构工程师"), job("工艺工程师", "https://old/b")],
                    "jobSource": "校招公告", "source": "https://old/a"})
        C.save_item(NAME, {"jobs": [job("结构工程师", "https://new/campus"), job("算法工程师", "https://new/campus")],
                           "jobSource": "校招公告", "source": "https://new/campus"})
        got = self._load()
        self.assertEqual([j["title"] for j in got["jobs"]],
                         ["结构工程师", "工艺工程师", "算法工程师"])
        self.assertEqual(got["jobs"][0]["source_url"], "https://new/campus")  # 同名用新岗
        self.assertEqual(got["jobs"][1]["source_url"], "https://old/b")       # 旧独有保留
        self.assertEqual(got["source"], "https://new/campus")

    def test_scope_only_never_touches_jobs(self):
        self._seed({"name": NAME, "jobs": [job("结构工程师")], "biz_scope": "旧主营"})
        C.save_item(NAME, {"biz_scope": "新主营"})
        got = self._load()
        self.assertEqual(len(got["jobs"]), 1)
        self.assertEqual(got["biz_scope"], "新主营")

    def test_new_company_miss_writes_empty(self):
        C.save_item(NAME, {"name": NAME, "jobs": [], "note": "没抓到页面"})
        self.assertEqual(self._load()["jobs"], [])

    def test_new_company_hit_writes_jobs(self):
        C.save_item(NAME, {"name": NAME, "jobs": [job("结构工程师", "https://new/j")]})
        got = self._load()
        self.assertEqual(len(got["jobs"]), 1)
        self.assertEqual(got["jobs"][0]["source_url"], "https://new/j")

    def test_title_normalization(self):
        merged = C.merge_jobs([job("结构工程师（合肥）")], [job("结构工程师(合肥)", "https://new")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_url"], "https://new")

    def test_unknown_attrs_never_erase_old_values(self):
        """企业属性「未知不覆盖有值」——v3 重采曾把旧库 city/nature/industry 冲成未知
        （city 丢 24 家、nature 19、industry 14）。本次没查到就保留旧值；
        本次真查到新值（非未知）仍照常覆盖。"""
        self._seed({"name": NAME, "jobs": [job("结构工程师")],
                    "city": "南京", "nature": "国企", "industry": "制造"})
        C.save_item(NAME, {"name": NAME, "jobs": [job("结构工程师", "https://new")],
                           "city": "未知", "nature": "", "industry": "未知"})
        got = self._load()
        self.assertEqual(got["city"], "南京")
        self.assertEqual(got["nature"], "国企")
        self.assertEqual(got["industry"], "制造")

    def test_real_new_value_still_overwrites(self):
        self._seed({"name": NAME, "jobs": [job("结构工程师")], "city": "南京"})
        C.save_item(NAME, {"name": NAME, "jobs": [], "city": "苏州"})
        self.assertEqual(self._load()["city"], "苏州")


if __name__ == "__main__":
    unittest.main()
