# -*- coding: utf-8 -*-
"""主榜分层：复检判「有限对口」的往后排，管培生垫底。

2026-11-18 用户定的规矩。起因是材料专业的榜单第 1 名是一家半导体企业
（睿创微纳）：它按行业推断出来的「对口」，复检判的却是「行业码有限对口，
只放通用职能岗」——等于复检自己都没确认，却凭城市 / 性质 / 行业地位攒的
85.2 分，压过了一堆有真实岗位名单的企业。

用户口径原文：
  「推断有岗命中的正常排，而是复检判断有限对口的这个，复检失效了，他不确定
   的往后排，正常的没岗位推断的，和我的方向一致，岗位一致，那就正常排」

所以锁住三件事：
1. `rank_tier` 的依据是**复检结论**，不是「有没有岗位名」——
   `code_core` / `name_dir` 正常排（推断来的也算），`code_edge` / `code_out` 下沉。
2. 分层优先于分数：85.2 分的 `code_edge` 不许压过 53.7 分的 `code_core`。
3. 前端 report.js 的 `fitTier` 与后端同规则（页面会自己再排一遍，
   两端不同源的话同一家企业前后端名次会打架）。
"""
import os
import re
import unittest

import fair_bridge as F

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_JS = os.path.join(os.path.dirname(HERE), "js", "report.js")


def row(name, score=80.0, state="hit", jobs=None, code="", booth="001",
        feedback=None, fit=None, biz_fit=None, ask_jobs=None, infer_matches=None):
    """造一条最小可用的 row（只带排序会用到的字段）。"""
    return {
        "name": name,
        "score": score,
        "state": state,
        "booth": booth,
        "matched_jobs": jobs or [],
        "biz_recheck": ({"code": code, "label": code} if code else None),
        "feedback": feedback,
        "fit": fit,
        "biz_fit": biz_fit,
        "ask_jobs": ask_jobs or [],
        "infer_matches_backend": infer_matches or [],
    }


class TestRankTier(unittest.TestCase):
    """分层本身：0 正常 / 1 复检判不确定 / 2 只看得见管培生。"""

    def test_行业码对口_正常(self):
        self.assertEqual(F.rank_tier(row("A", jobs=["材料研发工程师"], code="code_core")), 0)

    def test_岗位名自带方向_正常(self):
        self.assertEqual(F.rank_tier(row("A", jobs=["材料研发"], code="name_dir")), 0)

    def test_推断但方向一致_不下沉(self):
        """用户明确说过：正常的没岗位推断的、方向一致，就正常排。"""
        self.assertEqual(F.rank_tier(row("A", state="inferred", code="code_core")), 0)

    def test_没有复检结论_不下沉(self):
        self.assertEqual(F.rank_tier(row("A", jobs=["材料研发工程师"])), 0)

    def test_复检判有限对口_下沉(self):
        self.assertEqual(F.rank_tier(row("A", jobs=["失效分析工程师"], code="code_edge")), 1)

    def test_复检判不是这个方向_下沉(self):
        self.assertEqual(F.rank_tier(row("A", jobs=["材料研发工程师"], code="code_out")), 1)

    def test_复检判主营是别的行业_下沉(self):
        """`biz_other` 在 biz_recheck 里本来就算 drop，只是行没真撤下。

        2026-11-18 实测：法学、新闻两个用户的榜首就是它（半导体企业 x 法学），
        学生一眼就知道不对，所以同样按「复检没确认对口」下沉。
        """
        self.assertEqual(F.rank_tier(row("A", jobs=["法务专员"], code="biz_other")), 1)

    def test_只有管培生_垫底(self):
        self.assertEqual(
            F.rank_tier(row("A", jobs=["生产管培生", "质量管培生"], code="code_core")), 2)

    def test_管培生旁边有实岗_不算垫底(self):
        self.assertEqual(
            F.rank_tier(row("A", jobs=["生产管培生", "材料研发工程师"], code="code_core")), 0)


class TestRankOrder(unittest.TestCase):
    """排序键：分层在前，分数只决定层内先后。"""

    def test_有限对口的再高分也排在确定对口后面(self):
        """核心规制：85.2 分的 code_edge 不许压过 53.7 分的 code_core。

        这条就是用户看到的现象——半导体企业排材料专业第 1。改成先分层后，
        它必须落到所有「复检确认对口」的企业之后。
        """
        hi = row("睿创微纳", score=85.2, state="inferred", code="code_edge")
        lo = row("金田铜业", score=53.7, jobs=["材料研发"], code="code_core")
        self.assertEqual(sorted([hi, lo], key=F.rank_sort_key)[0]["name"], "金田铜业")

    def test_管培生排在有限对口之后(self):
        """用户原话：不能确定对口的「只放在管培生不确定的前面」。"""
        g = row("科伦药业", score=76.8, jobs=["生产管培生"], code="code_core")
        e = row("沛顿科技", score=47.2, jobs=["失效分析工程师"], code="code_edge")
        self.assertEqual(sorted([g, e], key=F.rank_sort_key)[0]["name"], "沛顿科技")

    def test_同层内仍按分数(self):
        a = row("A", score=70.0, jobs=["材料研发"], code="code_core")
        b = row("B", score=80.0, jobs=["材料研发"], code="code_core")
        self.assertEqual(sorted([a, b], key=F.rank_sort_key)[0]["name"], "B")

    def test_现场确认不招的照样垫底(self):
        no = row("N", score=90.0, jobs=["材料研发"], code="code_core",
                 feedback={"result": "no"})
        ok = row("Y", score=10.0, jobs=["材料研发"], code="code_core")
        self.assertEqual(sorted([no, ok], key=F.rank_sort_key)[0]["name"], "Y")

    def test_主营方向判死的照样垫底(self):
        bad = row("B", score=90.0, state="inferred", code="code_core",
                  fit="no", biz_fit={"via": "llm"})
        ok = row("Y", score=10.0, state="inferred", code="code_core")
        self.assertEqual(sorted([bad, ok], key=F.rank_sort_key)[0]["name"], "Y")


class TestFrontendSameRule(unittest.TestCase):
    """前端 fitTier 与后端 rank_tier 必须同规则（页面会自己再排一遍）。"""

    def test_两端泛岗词与不确定码一致(self):
        js = open(REPORT_JS, encoding="utf-8").read()
        m = re.search(r"GENERIC_ROLE=\[([^\]]+)\]", js)
        self.assertIsNotNone(m, "report.js 里找不到 GENERIC_ROLE")
        self.assertEqual(tuple(x.strip().strip("'\"") for x in m.group(1).split(",")),
                         F._GENERIC_ROLE,
                         "前端泛岗词表与后端 _GENERIC_ROLE 不一致")
        m2 = re.search(r"UNCERTAIN_RECHECK=\[([^\]]+)\]", js)
        self.assertIsNotNone(m2, "report.js 里找不到 UNCERTAIN_RECHECK")
        self.assertEqual(frozenset(x.strip().strip("'\"") for x in m2.group(1).split(",")),
                         F._UNCERTAIN_CODES,
                         "前端不确定结论码与后端 _UNCERTAIN_CODES 不一致")

    def test_前端排序键含分层(self):
        """两处重排（默认队列序 / 只看命中）都必须带上 fitTier。"""
        js = open(REPORT_JS, encoding="utf-8").read()
        self.assertGreaterEqual(js.count("fitTier(a)"), 2,
                                "report.js 至少有两处排序要用 fitTier 分层")


if __name__ == "__main__":
    unittest.main()
