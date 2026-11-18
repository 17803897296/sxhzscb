# -*- coding: utf-8 -*-
"""参会企业名单落库 + 榜单按名单收口（2026-11-18）。

背景：企业端导入 132 家，页面显示「现在匹配池里共 132 家」，但那 132 个名字
只活在浏览器 localStorage 里（`match.js` 只写不读），匹配端读的还是全库排行。
这里把名单落到库里，构建时按名单收口，没进榜的逐条给原因。
"""
import os
import tempfile
import unittest

import repository
import roster_store
import fair_bridge


class RosterStoreTest(unittest.TestCase):
    def setUp(self):
        self._db = repository.LIVE_DB
        self._ready = repository._live_ready
        tmp = tempfile.mkdtemp()
        repository.LIVE_DB = os.path.join(tmp, "t.db")
        repository._live_ready = True      # 别去找 .seed

    def tearDown(self):
        repository.LIVE_DB = self._db
        repository._live_ready = self._ready

    def test_roundtrip_and_dedupe(self):
        n = roster_store.set_names("FAIR-A", ["甲", "乙", "甲", "  "],
                                   {"乙": "没抓到岗位"})
        self.assertEqual(n, 2)
        self.assertEqual(roster_store.get_names("FAIR-A"), ["甲", "乙"])
        self.assertEqual(roster_store.get_notes("FAIR-A").get("乙"), "没抓到岗位")

    def test_replace_not_append(self):
        roster_store.set_names("FAIR-A", ["甲", "乙"])
        roster_store.set_names("FAIR-A", ["丙"])
        self.assertEqual(roster_store.get_names("FAIR-A"), ["丙"])

    def test_fair_id_isolated(self):
        roster_store.set_names("FAIR-A", ["甲"])
        roster_store.set_names("FAIR-B", ["乙"])
        self.assertEqual(roster_store.get_names("FAIR-A"), ["甲"])
        self.assertEqual(roster_store.get_names("FAIR-B"), ["乙"])

    def test_clear_and_unknown(self):
        roster_store.set_names("FAIR-A", ["甲"])
        self.assertEqual(roster_store.clear("FAIR-A"), 1)
        self.assertEqual(roster_store.get_names("FAIR-A"), [])
        self.assertEqual(roster_store.get_names("NOPE"), [])


class ApplyRosterTest(unittest.TestCase):
    def setUp(self):
        self._get = roster_store.get_names
        self._db = repository.LIVE_DB
        self._ready = repository._live_ready
        repository.LIVE_DB = os.path.join(tempfile.mkdtemp(), "t.db")
        repository._live_ready = True

    def tearDown(self):
        roster_store.get_names = self._get
        repository.LIVE_DB = self._db
        repository._live_ready = self._ready

    def _run(self, names, rows=None, dropped=None, bizfit=None, campus=None,
             campus_names=None, miss=None):
        roster_store.set_names("FAIR-A", names)
        return fair_bridge.apply_roster(
            "FAIR-A",
            [{"name": n} for n in (rows if rows is not None else ["甲", "乙", "丙"])],
            dropped or [], bizfit or [], campus or [], campus_names or [], miss or [])

    def test_empty_roster_leaves_everything_alone(self):
        rows, *_rest, block = self._run([])
        self.assertEqual(len(rows), 3)
        self.assertEqual(block["count"], 0)

    def test_filter_to_roster(self):
        rows, *_rest, block = self._run(["甲", "丙"])
        self.assertEqual([r["name"] for r in rows], ["甲", "丙"])
        self.assertEqual(block["count"], 2)
        self.assertEqual(block["in_rank"], 2)
        self.assertEqual(block["missing"], [])

    def test_missing_keeps_reason(self):
        rows, _b, _c, _cn, _mn, block = self._run(
            ["甲", "乙", "丁"], rows=["甲"],
            dropped=[{"name": "丁", "why": "教培类（学生未选择接受）"}],
            campus=[{"name": "乙", "why": "校招公告里 12 个岗位没有对口的"}])
        self.assertEqual([r["name"] for r in rows], ["甲"])
        why = {m["name"]: m["why"] for m in block["missing"]}
        self.assertIn("教培", why["丁"])
        self.assertIn("公告", why["乙"])      # 有具体原因，不是空话

    def test_missing_falls_back_to_import_note(self):
        # 企业端搜索时已经知道「为什么没抓到」（城市对不上 / 没抓到可信页面），
        # 这个理由跟着名单一起落库，报告里直接用，不再是一句笼统的「没匹配上」。
        roster_store.set_names("FAIR-A", ["甲", "某公司"],
                               {"某公司": "搜到的公司在深圳，与名单对不上，按没查到处理"})
        _r, _b, _c, _cn, _mn, block = fair_bridge.apply_roster(
            "FAIR-A", [{"name": "甲"}], [], [], [], [], [])
        self.assertEqual(block["missing"][0]["why"],
                         "搜到的公司在深圳，与名单对不上，按没查到处理")

    def test_exclusion_lists_also_filtered(self):
        # 名单外的企业不该出现在「为什么没进榜」里
        _r, bizfit, campus, campus_names, miss, _b = self._run(
            ["甲"],
            bizfit=[{"name": "乙", "why": "主营不交叉"},
                    {"name": "甲", "why": "x"}],
            campus=[{"name": "丙", "why": "公告无对口"}],
            campus_names=["乙", "丙"], miss=["甲", "丙"])
        self.assertEqual([x["name"] for x in bizfit], ["甲"])
        self.assertEqual(campus, [])
        self.assertEqual(campus_names, [])
        self.assertEqual(miss, ["甲"])


if __name__ == "__main__":
    unittest.main()
