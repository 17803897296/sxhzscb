# -*- coding: utf-8 -*-
"""逐岗 URL 回归：采集抽岗位时必须把命中页的地址贴到每个岗位上。

以前 URL 只挂企业顶层 source，岗位对象里没有 source_url，
导致全量重采的 458 岗全部无 URL。模型路径和规则路径都要钉住。
"""
import unittest

import enrich_by_llm as E
import portal

NAME = "某装备股份有限公司"
PAGE = ("某装备股份有限公司 2026 校园招聘\n"
        "岗位名称：结构工程师\n职责：负责结构设计\n"
        "工作地点：北京\n学历：本科\n")
URL = "https://hr.example.com/campus/2026"

RAW = {
    "nature": "国企", "city": "北京", "industry": "制造",
    "jobSource": "校招公告", "evidence": "校园招聘",
    "jobs": [{"title": "结构工程师", "duty": "负责结构设计", "place": "北京",
              "stage": "校招", "requirements": "本科"}],
    "biz_scope": "未知", "bizEvidence": "",
}


class JobSourceUrlTests(unittest.TestCase):
    def test_model_path_fills_each_job(self):
        entry = E.build_entry(NAME, RAW, PAGE, URL)
        self.assertEqual(len(entry["jobs"]), 1)
        self.assertEqual(entry["jobs"][0]["source_url"], URL)
        self.assertEqual(entry["source"], URL)

    def test_model_path_never_fakes_url(self):
        for bad in ("未知", "", None):
            with self.subTest(source=bad):
                entry = E.build_entry(NAME, RAW, PAGE, bad)
                self.assertEqual(entry["jobs"][0]["source_url"], "")

    def test_model_path_keeps_all_jobs_tagged(self):
        raw = dict(RAW, jobs=[{"title": "结构工程师"},
                              {"title": "工艺工程师"}])
        entry = E.build_entry(NAME, raw, PAGE, URL)
        self.assertEqual(len(entry["jobs"]), 2)
        self.assertEqual([j["source_url"] for j in entry["jobs"]], [URL, URL])

    def test_rule_path_fills_each_job(self):
        old_pages, old_model, old_scope = portal.pages_for, portal.MODEL_ON, portal.guess_scope
        try:
            portal.pages_for = lambda name: (PAGE, URL)
            portal.MODEL_ON = False
            portal.guess_scope = lambda text: "装备制造"
            entry = portal.build(NAME)
        finally:
            portal.pages_for, portal.MODEL_ON, portal.guess_scope = old_pages, old_model, old_scope
        self.assertTrue(entry["jobs"])
        self.assertEqual(entry["jobs"][0]["source_url"], URL)

    def test_rule_path_never_fakes_url(self):
        old_pages, old_model, old_scope = portal.pages_for, portal.MODEL_ON, portal.guess_scope
        try:
            portal.pages_for = lambda name: (PAGE, "未知")
            portal.MODEL_ON = False
            portal.guess_scope = lambda text: "装备制造"
            entry = portal.build(NAME)
        finally:
            portal.pages_for, portal.MODEL_ON, portal.guess_scope = old_pages, old_model, old_scope
        self.assertTrue(entry["jobs"])
        self.assertEqual(entry["jobs"][0]["source_url"], "")


if __name__ == "__main__":
    unittest.main()
