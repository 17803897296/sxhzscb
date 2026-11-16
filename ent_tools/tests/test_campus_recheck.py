# -*- coding: utf-8 -*-
"""校招来源核实：新库判定 + read_db 覆盖逻辑回归测试。"""
import os
import sqlite3
import tempfile
import unittest

import fair_bridge as FB


class TestCampusRecheckDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        c = sqlite3.connect(self.tmp.name)
        c.executescript("""
          CREATE TABLE campus_company(name TEXT, old_tag TEXT, n INTEGER, is_campus INTEGER, channels TEXT, decision TEXT);
          CREATE TABLE campus_jobs(company TEXT, title TEXT, source_url TEXT, channel TEXT);
        """)
        # 校招企业2家：证据岗分别3个、2个（<3 的降级）
        c.execute("INSERT INTO campus_company VALUES('A公司','websearch',3,1,'高校就业网','校招来源')")
        c.execute("INSERT INTO campus_company VALUES('B公司','websearch',2,0,'公众号','降级websearch')")
        c.execute("INSERT INTO campus_company VALUES('C公司','校招公告',1,0,'','降级websearch')")
        for t in ["岗1", "岗2", "岗3"]:
            c.execute("INSERT INTO campus_jobs VALUES('A公司',?,'http://x.edu.cn/1','高校就业网')", (t,))
        for t in ["岗1", "岗2"]:
            c.execute("INSERT INTO campus_jobs VALUES('B公司',?,'http://mp.weixin.qq.com/1','公众号')", (t,))
        c.commit()
        c.close()
        self._orig_path = FB.CAMPUS_RECHECK
        FB.CAMPUS_RECHECK = self.tmp.name

    def tearDown(self):
        FB.CAMPUS_RECHECK = self._orig_path
        os.unlink(self.tmp.name)

    def test_campus_company_jobs_replaced(self):
        comps = {"A公司": {"jobs": [{"title": "推断岗"}], "jobSource": "websearch"}}
        FB.apply_campus_recheck(comps)
        self.assertEqual(comps["A公司"]["jobSource"], "校招公告")
        self.assertEqual([j["title"] for j in comps["A公司"]["jobs"]], ["岗1", "岗2", "岗3"])
        self.assertTrue(all(j.get("source_url") for j in comps["A公司"]["jobs"]))

    def test_demoted_company_keeps_jobs_but_tag_changes(self):
        comps = {"C公司": {"jobs": [{"title": "真岗"}], "jobSource": "校招公告"}}
        FB.apply_campus_recheck(comps)
        self.assertEqual(comps["C公司"]["jobSource"], "websearch")
        self.assertEqual(len(comps["C公司"]["jobs"]), 1)  # 降级≠删岗

    def test_untouched_company(self):
        comps = {"B公司": {"jobs": [{"title": "真岗"}], "jobSource": "社招信息"}}
        FB.apply_campus_recheck(comps)
        self.assertEqual(comps["B公司"]["jobSource"], "社招信息")

    def test_return_counts(self):
        comps = {
            "A公司": {"jobs": [{"title": "推断岗"}] * 5, "jobSource": "websearch"},
            "C公司": {"jobs": [], "jobSource": "校招公告"},
        }
        changed, removed, demoted = FB.apply_campus_recheck(comps)
        self.assertEqual((changed, removed, demoted), (1, 2, 1))

    def test_switch_off(self):
        old = os.environ.get("FAIR_CAMPUS_RECHECK")
        os.environ["FAIR_CAMPUS_RECHECK"] = "0"
        try:
            comps = {"A公司": {"jobs": [], "jobSource": "websearch"}}
            self.assertEqual(FB.apply_campus_recheck(comps), (0, 0, 0))
            self.assertEqual(comps["A公司"]["jobSource"], "websearch")
        finally:
            if old is None:
                os.environ.pop("FAIR_CAMPUS_RECHECK", None)
            else:
                os.environ["FAIR_CAMPUS_RECHECK"] = old


if __name__ == "__main__":
    unittest.main()
