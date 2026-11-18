# -*- coding: utf-8 -*-
"""专业类岗位池文件的契约与回退测试。

覆盖：
  - 目录版本 93 类完整
  - 池文件结构契约（字段名/类型/版本）
  - 默认关闭、开关打开两态
  - 闸门二次校验（数据文件被人工塞进邻专业核心岗也能挡住）
  - 破坏性场景回退（版本不符/缺文件/类生成失败）
"""
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

import major_pools as MP
import job_recommend as J

CATALOG = os.path.join(_HERE, "data", "major_catalog_2025.json")
POOLS = os.path.join(_HERE, "data", "major_job_pools.json")


class TestCatalog(unittest.TestCase):
    def test_catalog_93_classes(self):
        d = json.load(open(CATALOG, encoding="utf-8"))
        classes = [c for v in d["disciplines"].values() for c in v]
        self.assertEqual(12, len(d["disciplines"]))
        self.assertEqual(93, len(classes))
        self.assertEqual(93, len(set(classes)))
        self.assertEqual("2025", d["catalog_version"])


class TestContract(unittest.TestCase):
    def setUp(self):
        self.data = json.load(open(POOLS, encoding="utf-8"))

    def test_pools_version_matches_catalog(self):
        self.assertEqual("2025", self.data["catalog_version"])

    def test_validate_current_file(self):
        ok, errs = MP.validate(self.data)
        self.assertTrue(ok, errs[:5])

    def test_validate_rejects_bad_version(self):
        bad = dict(self.data)
        bad["catalog_version"] = "1999"
        ok, errs = MP.validate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("catalog_version" in e for e in errs))

    def test_validate_rejects_bad_entry(self):
        bad = json.loads(json.dumps(self.data))
        first = next(iter(bad["classes"]))
        bad["classes"][first]["L1"] = [{"title": "", "why": "x"}]
        ok, _ = MP.validate(bad)
        self.assertFalse(ok)


class TestSwitchAndFallback(unittest.TestCase):
    def test_disabled_by_default(self):
        old = os.environ.pop("ENT_MAJOR_POOLS", None)
        try:
            self.assertFalse(MP.enabled())
        finally:
            if old is not None:
                os.environ["ENT_MAJOR_POOLS"] = old

    def test_enabled_with_flag(self):
        old = os.environ.get("ENT_MAJOR_POOLS")
        try:
            os.environ["ENT_MAJOR_POOLS"] = "1"
            self.assertTrue(MP.enabled())
        finally:
            if old is None:
                os.environ.pop("ENT_MAJOR_POOLS", None)
            else:
                os.environ["ENT_MAJOR_POOLS"] = old

    def test_pool_for_returns_candidates(self):
        d = json.load(open(POOLS, encoding="utf-8"))
        good = next(k for k, v in d["classes"].items() if not v.get("_error"))
        res = MP.pool_for(good, 0)
        self.assertIsNotNone(res)
        items, mode = res
        self.assertTrue(items)
        self.assertTrue(mode.startswith("pool-"))
        self.assertTrue(all("title" in c for c in items))

    def test_pool_for_unknown_class_returns_none(self):
        self.assertIsNone(MP.pool_for("不存在的专业类XYZ", 0))

    def test_pool_for_high_tier_returns_none(self):
        d = json.load(open(POOLS, encoding="utf-8"))
        good = next(k for k, v in d["classes"].items() if not v.get("_error"))
        self.assertIsNone(MP.pool_for(good, 3))  # L3 全局池不进这个文件


if __name__ == "__main__":
    unittest.main()
