# -*- coding: utf-8 -*-
"""语义层的两道闸门：候选初筛、职能族。

2026-11-18 接上真模型（DeepSeek）后实测出来的两处漏判，都在这一层：

1. `_candidate_titles` 对每个岗位名按「core 词 + 意向相邻二字」打分，0 分直接丢。
   于是「公共卫生」的候选只剩 5 个，`公卫医师`（不含 core 词，`公卫` 又跨字对不上
   「公共卫生」的相邻二字）被丢掉，模型连看都没看到。活库 1276 个岗位名里有 467 个
   属于这种「专业职称写法」，全被 limit=240 挤掉。
2. `_share_core` 是字面闸门，模型判对的岗被它否掉：模型说「疾病预防控制专员」是
   公共卫生核心职能，「结构工程师」和「机械设计工程师」是同一批人，字面一个词都
   不重合 → 全被拦。修法是补职能族（struct / ehs / medical），不是把闸门放开——
   实测无条件放开会吃进「医学专员 × 市场专员」「结构工程师 × 工业设计师」。

这两条都是「模型对了、本地规则把它否了」的结构性问题，一旦退回字面匹配就复发，
所以锁在测试里。
"""
import unittest

import intent_semantics as S


class TestCandidateTitles(unittest.TestCase):
    def test_small_pool_kept_whole(self):
        """量装得下就全给：8 个候选不能只喂 5 个。"""
        titles = ["公卫医师", "疾病预防控制专员", "流行病学调查员", "药品研发工程师",
                  "食品研发工程师", "机械设计工程师", "临床监查员", "卫生检验技师"]
        out = S._candidate_titles(titles, ["公共卫生"])
        self.assertIn("公卫医师", out)
        self.assertIn("流行病学调查员", out)
        self.assertEqual(len(out), len(titles))

    def test_large_pool_prefers_exact_intent(self):
        """池子超过 limit 时，岗位名直含意向全名的排最前。"""
        filler = ["工程师%d" % i for i in range(400)] + ["专员%d" % i for i in range(400)]
        titles = filler + ["公共卫生管理", "公卫医师"]
        out = S._candidate_titles(titles, ["公共卫生"], limit=30)
        self.assertIn("公共卫生管理", out)

    def test_large_pool_default_limit_covers_live_db(self):
        """活库 1276 个岗位名要能整池进 prompt（不再被 240 截断）。"""
        titles = ["岗位名%d" % i for i in range(1276)]
        out = S._candidate_titles(titles, ["结构工程师"])
        self.assertEqual(len(out), 1276)


class TestStructFamily(unittest.TestCase):
    """结构 / 机械设计：同一批人，字面零重合。"""

    def _keep(self, intent, cands):
        return S._sanitize(intent, cands, set(cands), use_seed=False)

    def test_structural_keeps_mechanical_design(self):
        for t in ("机械设计工程师", "CAE工程师", "仿真工程师", "力学分析工程师",
                  "结构设计工程师"):
            with self.subTest(title=t):
                self.assertIn(t, self._keep("结构工程师", [t]))

    def test_structural_blocks_other_design_and_test(self):
        # 工业设计 / 包装 / 测试 / 项目经理 都不是结构设计岗
        for t in ("机械测试工程师", "工业设计工程师", "包装工程师",
                  "机械开发项目经理"):
            with self.subTest(title=t):
                self.assertNotIn(t, self._keep("结构工程师", [t]))


class TestEhsFamily(unittest.TestCase):
    """EHS：企业里叫「安环」「环保」「消防」。"""

    def _keep(self, intent, cands):
        return S._sanitize(intent, cands, set(cands), use_seed=False)

    def test_ehs_keeps_anhuan_and_fire(self):
        for t in ("安环工程师", "消防工程师", "环保工程师", "环境检测工程师"):
            with self.subTest(title=t):
                self.assertIn(t, self._keep("EHS安全工程师", [t]))

    def test_ehs_blocks_design_and_rd(self):
        # 带「设备 / 研发」的是设计研发岗，TECH_WORDS 仍要拦住
        for t in ("环保设备设计工程师", "环保技术研发工程师", "安全研发岗",
                  "体系工程师"):
            with self.subTest(title=t):
                self.assertNotIn(t, self._keep("EHS安全工程师", [t]))


class TestMedicalFamily(unittest.TestCase):
    """医疗 / 公卫：这一族原来是空的，导致公卫对口岗全军覆没。"""

    def _keep(self, intent, cands):
        return S._sanitize(intent, cands, set(cands), use_seed=False)

    def test_public_health_keeps_core_posts(self):
        for t in ("疾病预防控制专员", "公卫医师", "流行病学调查员",
                  "卫生检验技师"):
            with self.subTest(title=t):
                self.assertIn(t, self._keep("公共卫生", [t]))

    def test_public_health_blocks_other_majors(self):
        for t in ("机械设计工程师", "质量检验员", "食品研发工程师"):
            with self.subTest(title=t):
                self.assertNotIn(t, self._keep("公共卫生", [t]))

    def test_medical_specialist_does_not_eat_marketing(self):
        """防过度放开：医学专员绝不能吃进市场岗、产品管培。"""
        for t in ("市场专员", "市场营销专员", "产品管培生", "质量检验员"):
            with self.subTest(title=t):
                self.assertNotIn(t, self._keep("医学专员", [t]))

    def test_medical_specialist_keeps_clinical(self):
        for t in ("临床研究专员", "医学信息沟通专员"):
            with self.subTest(title=t):
                self.assertIn(t, self._keep("医学专员", [t]))

    def test_drug_analysis_keeps_api_quality(self):
        """药物分析 ↔ 原料药质量研究：药企分析岗的核心入口。"""
        self.assertIn("原料药质量研究员",
                      self._keep("药物分析研究员", ["原料药质量研究员"]))


class TestFamilyTableShape(unittest.TestCase):
    def test_new_families_have_labels(self):
        for fam in ("struct", "ehs", "medical"):
            with self.subTest(family=fam):
                self.assertIn(fam, S.FUNC_FAMILY)
                self.assertIn(fam, S.FAMILY_LABEL)
                self.assertTrue(S.FUNC_FAMILY[fam])

    def test_no_bare_inspection_word(self):
        """裸「检验」不能进医疗族，否则「质量检验员」会被吃进公共卫生。"""
        self.assertNotIn("检验", S.FUNC_FAMILY["medical"])


if __name__ == "__main__":
    unittest.main()
