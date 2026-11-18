# -*- coding: utf-8 -*-
"""画像推断岗补库的回归测试。

守住三条线：
  1. 推断岗带标注、source_url 为空、不冒充真实 JD；
  2. 判定链把推断岗从真实岗里排除（_clean_jobs），不会把 inferred 误升成明确命中；
  3. 补库只看来源：校招公告来源不补，websearch / 社招 / 未知一律补（2026-11-18
     用户定口径，校招核实库同日停用，不再按名单挡）。
"""
import json
import os
import sqlite3
import tempfile
import unittest

import fair_bridge as F
import fill_inferred_jobs as X

KIND = "大模型推断"


def real(title):
    return {"title": title, "source_url": "https://hr.example.com/job/1"}


def inferred(title):
    return {"title": title, "source_url": "", "job_source_kind": KIND,
            "job_profile_basis": "主营汽车零部件，需要工艺与质量工程师"}


class CleanJobsTests(unittest.TestCase):
    def test_inferred_jobs_excluded_from_real(self):
        jobs = [real("质量工程师"), inferred("数据分析师")]
        self.assertEqual([j["title"] for j in F._clean_jobs(jobs)], ["质量工程师"])
        self.assertEqual([j["title"] for j in F._inferred_jobs(jobs)], ["数据分析师"])

    def test_all_inferred_means_no_real_jd(self):
        jobs = [inferred("工艺工程师"), inferred("设备维护工程师")]
        self.assertEqual(F._clean_jobs(jobs), [])
        self.assertEqual(len(F._inferred_jobs(jobs)), 2)

    def test_no_annotation_still_real(self):
        self.assertEqual([j["title"] for j in F._clean_jobs([{"title": "采购专员"}])],
                         ["采购专员"])


class ToJobsTests(unittest.TestCase):
    def test_profile_to_tagged_jobs(self):
        prof = {"chain": "汽车零部件：冲压、焊接、涂装、总装",
                "jobs": [{"title": "工艺工程师", "strength": "strong", "stage": "生产"},
                         {"title": "拼造的岗位", "suspect": True, "strength": "medium"}]}
        out = X.to_jobs("某部件公司", prof)
        self.assertEqual(len(out), 1)                     # suspect 被丢
        self.assertEqual(out[0]["title"], "工艺工程师")
        self.assertEqual(out[0]["job_source_kind"], KIND)
        self.assertEqual(out[0]["source_url"], "")
        self.assertTrue(out[0]["job_profile_basis"].startswith("汽车零部件"))

    def test_merge_keeps_real_and_skips_duplicate(self):
        old = [real("质量工程师")]
        add = [inferred("质量工程师"), inferred("数据分析师")]
        merged, n = X._merge(old, add)
        self.assertEqual(n, 1)                            # 同名跳过
        self.assertEqual([j["title"] for j in merged], ["质量工程师", "数据分析师"])
        self.assertEqual(merged[0]["source_url"], "https://hr.example.com/job/1")  # 真实岗没被改


class SourceBoundaryTests(unittest.TestCase):
    """补库口径：**只看 jobSource**，不再看校招名单。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE company_jobs(name TEXT PRIMARY KEY, item_json TEXT, updated_at TEXT)")
        for n, src, jobs in [("校招企业", "校招公告", [real("质量工程师")]),
                             ("校招0岗", "校招公告", []),
                             ("非校招0岗", "未知", []),
                             ("非校招有岗", "websearch", [real("质量工程师")]),
                             ("社招有岗", "社招信息", [real("工艺工程师")]),
                             ("已补过", "websearch", [real("质检员"), inferred("工艺工程师")])]:
            con.execute("INSERT INTO company_jobs VALUES(?,?,?)",
                        (n, json.dumps({"name": n, "jobSource": src, "jobs": jobs}), ""))
        con.commit(); con.close()
        self.old_db = X.DB
        X.DB = self.db

    def tearDown(self):
        X.DB = self.old_db
        self.tmp.cleanup()

    def test_only_non_campus_sources(self):
        con = sqlite3.connect(self.db); con.row_factory = sqlite3.Row
        todo = dict((n, o) for n, o in X.targets(con))
        con.close()
        # 校招公告来源一律不补，不论有几个岗
        self.assertNotIn("校招企业", todo)
        self.assertNotIn("校招0岗", todo)
        # 非校招来源全补，哪怕已经有真实岗
        self.assertIn("非校招0岗", todo)
        self.assertIn("非校招有岗", todo)
        self.assertIn("社招有岗", todo)
        # 已补过的不重复补
        self.assertNotIn("已补过", todo)


if __name__ == "__main__":
    unittest.main()


class HitUpgradeTests(unittest.TestCase):
    """补过推断岗、且真实岗为空的企业，不能凭对齐结果升成明确命中。"""

    def test_infer_only_company_has_no_real_evidence(self):
        # 复刻 _hits_and_misses 之前的 real 判定口径
        jobs = [inferred("工艺工程师"), inferred("食品研发工程师")]
        jd_titles = {j.get("title", "") for j in F._clean_jobs(jobs)}
        aligned = [("工艺工程师", "工艺工程师"), ("食品研发工程师", "食品研发工程师")]
        has_infer = bool(F._inferred_jobs(jobs))
        real = [t for t, _ in aligned
                if ((not jd_titles) and not has_infer)
                or any(F._same_title(t, x) for x in jd_titles)]
        self.assertEqual(jd_titles, set())
        self.assertEqual(real, [])   # 修复前这里会把两条推断岗当成 real → hit

    def test_real_job_still_counts_as_evidence(self):
        jobs = [real("质量工程师"), inferred("工艺工程师")]
        jd_titles = {j.get("title", "") for j in F._clean_jobs(jobs)}
        aligned = [("质量工程师", "质量工程师")]
        real_evidence = [t for t, _ in aligned
                         if ((not jd_titles) and not bool(F._inferred_jobs(jobs)))
                         or any(F._same_title(t, x) for x in jd_titles)]
        self.assertEqual(real_evidence, ["质量工程师"])
