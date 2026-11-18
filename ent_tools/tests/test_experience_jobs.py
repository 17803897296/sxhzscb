# -*- coding: utf-8 -*-
"""实习 / 竞赛 → L1 对应岗（2026-11-18 用户定口径）。

规则三条：
- 实习干啥，L1 就推啥；一段实习只给一个岗，其余仍按专业推；
- 一个竞赛给一个相关岗（只一个）；论文暂不参与；
- 这一层不过专业闸门——文科生做供应链实习，「供应链专员」本来会被
  `_major_ok` 判成跑题，可那是他真正走得通的路。
"""
import os
import tempfile
import unittest

import job_recommend as J

CATS = lambda intern=(), comp=(), papers=(), skills=(): {
    "internships": list(intern), "competitions": list(comp),
    "papers": list(papers), "skills": list(skills)}


class ExperienceJobsTest(unittest.TestCase):
    def setUp(self):
        self._saved_pool = J.POOL_FILE
        J.POOL_FILE = os.path.join(tempfile.mkdtemp(), "pool.json")

    def tearDown(self):
        J.POOL_FILE = self._saved_pool

    def _l1(self, major, **kw):
        p, _ = J.get_pool(major, CATS(**kw), 0, False, {"DEEPSEEK_API_KEY": "x"},
                          use_llm=False)
        return p

    def test_internship_goes_l1_first_even_if_off_major(self):
        # 化学 + 供应链实习：专业闸门明确判它跑题（化学的领域词里没有供应链），
        # 但经历岗不过闸门，仍要出现在 L1 第一条。
        self.assertFalse(J._major_ok("供应链专员", "化学", 0))
        pool = self._l1("化学", intern=["某电商公司 供应链实习生 2025.06-09"])
        self.assertEqual(pool[0]["title"], "供应链专员")
        self.assertEqual(pool[0]["_exp"], "intern")
        self.assertIn("实习", pool[0]["why"])

    def test_off_major_internship_for_wenke(self):
        # 文科生做供应链实习：闸门对「认不出领域」的专业是放行的，但模型自己
        # 不会想到给供应链岗——经历层兜住它。
        pool = self._l1("汉语言文学", intern=["某电商公司 供应链实习生 2025.06-09"])
        self.assertEqual(pool[0]["title"], "供应链专员")

    def test_one_internship_one_job(self):
        # 一段供应链实习只留一个岗：SIGNAL_RULES 那批（供应链/计划岗、采购专员、
        # 物流运营岗、供应链数据分析岗）不能再出现在同一层。
        pool = self._l1("汉语言文学", intern=["某电商公司 供应链实习生"])
        titles = [c["title"] for c in pool]
        self.assertEqual(titles.count("供应链专员"), 1)
        for extra in ("供应链/计划岗", "采购专员", "物流运营岗", "供应链数据分析岗"):
            self.assertNotIn(extra, titles)

    def test_two_internships_two_jobs(self):
        pool = self._l1("汉语言文学",
                          intern=["京东物流 供应链实习", "字节跳动 数据分析实习"])
        exp = [c["title"] for c in pool if c.get("_exp")]
        self.assertEqual(exp, ["供应链专员", "数据分析岗"])

    def test_same_function_twice_deduped(self):
        pool = self._l1("物流管理",
                          intern=["京东 供应链实习", "顺丰 供应链实习"])
        exp = [c["title"] for c in pool if c.get("_exp")]
        self.assertEqual(exp, ["供应链专员"])

    def test_competition_gives_exactly_one_job(self):
        pool = self._l1("汉语言文学", comp=["全国大学生数据分析竞赛"])
        exp = [c["title"] for c in pool if c.get("_exp")]
        self.assertEqual(exp, ["数据分析岗"])
        # 数据信号那一批不跟着来：一个竞赛一个岗
        for extra in ("BI/报表岗", "策略分析岗", "算法工程师", "数据运营岗"):
            self.assertNotIn(extra, [c["title"] for c in pool])

    def test_papers_do_not_produce_jobs(self):
        pool = self._l1("汉语言文学", papers=["《红楼梦》版本考证，核心期刊一篇"])
        self.assertEqual([c for c in pool if c.get("_exp")], [])

    def test_noise_experience_ignored(self):
        for s in ("没有", "无", "跳过"):
            pool = self._l1("化学", intern=[s])
            self.assertEqual([c for c in pool if c.get("_exp")], [])

    def test_tier2_has_no_experience_jobs(self):
        # 「相邻有门槛」那一层不该再出现经历岗：它在 L1 已经给过一次。
        p, _ = J.get_pool("汉语言文学",
                          CATS(intern=["某电商 供应链实习"]), 2, False,
                          {"DEEPSEEK_API_KEY": "x"}, use_llm=False)
        self.assertEqual([c for c in p if c.get("_exp")], [])

    def test_matcher_covers_real_resume_text(self):
        cases = [
            ("药明康德 有机合成研发实习 2025.07-2025.10", "研发工程师"),
            ("某汽车厂 焊装车间 PLC 调试实习", "电气工程师"),
            ("德勤 审计实习（年审）", "审计助理"),
            ("新东方 助教实习", "教师"),
            ("小红书 内容运营实习", "内容编辑"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(J.experience_jobs(CATS(intern=[text]))[0]["title"], want)


if __name__ == "__main__":
    unittest.main()
