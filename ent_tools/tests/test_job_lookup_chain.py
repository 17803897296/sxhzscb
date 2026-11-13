import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import job_lookup
import job_store


ITEM = {
    "name": "测试企业",
    "city": "南京",
    "nature": "国企",
    "industry": "IT",
    "jobs": [{"title": "数据分析师"}],
    "jobSource": "校招公告",
    "source": "https://example.com",
    "date": "2026-11-02",
    "note": "测试",
}


class MemoryStore(job_store.JobStore):
    def __init__(self):
        self.data = {}
        self.lookups = 0
        self.saves = 0

    def lookup(self, company):
        self.lookups += 1
        return self.data.get(company)

    def save(self, company, item):
        self.saves += 1
        self.data[company] = item


class ChainTests(unittest.TestCase):
    def tearDown(self):
        job_store.set_store(job_store.NullJobStore())

    def test_online_then_db_hit(self):
        store = MemoryStore()
        job_store.set_store(store)
        with patch.object(job_lookup.portal, "build", return_value=dict(ITEM)) as build:
            first = job_lookup.lookup_company("测试企业", "南京", 10)
            second = job_lookup.lookup_company("测试企业", "南京", 10)

        self.assertEqual(first["data_source"], "online")
        self.assertFalse(first["db_hit"])
        self.assertEqual(first["total"], 1)
        self.assertEqual(store.saves, 1)

        self.assertEqual(second["data_source"], "db")
        self.assertTrue(second["db_hit"])
        self.assertEqual(second["jobs"][0]["title"], "数据分析师")
        self.assertEqual(build.call_count, 1)  # 第二次没有再联网
        self.assertEqual(store.lookups, 2)

    def test_null_store_falls_back_online(self):
        job_store.set_store(job_store.NullJobStore())
        with patch.object(job_lookup.portal, "build", return_value=dict(ITEM)) as build:
            data = job_lookup.lookup_company("测试企业")
        self.assertEqual(data["data_source"], "online")
        self.assertFalse(data["db_hit"])
        self.assertEqual(build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
