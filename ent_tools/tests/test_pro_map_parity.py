# -*- coding: utf-8 -*-
"""专业族 × 行业码：表本身（ind_code.fit）+ 前后端表一致性（MI_PRO_MAP ↔ PRO_MAP）。

为什么单独立一条：这张表 2026-11-17 起是匹配端的第一判据，改一个格就等于
改一整类学生的匹配结果。表里最怕两种错：
  ① 前端 MI_PRO_MAP 与后端 PRO_MAP 漂移 —— 同一家企业两个结论（词表时代
     已经翻过一次车，见 test_biz_table_parity）；
  ② 表写漏了码 —— 20 个码只要有一个不在任何族里，那一类企业就永远被判
     「不是这个方向」，而且不报错、静默错。
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import biz_recheck as B  # noqa: E402
import fair_report_js as J  # noqa: E402
import ind_code as I  # noqa: E402


def js_pro_map():
    s = J.REPORT_JS
    a = s.index("const MI_PRO_MAP = ")
    b = s.index("};", a) + 2
    blob = s[a:b].split("=", 1)[1].strip().rstrip(";")
    # JS 字面量是 {fam:{code:'core'}}：键不带引号，值单引号 → 补成 JSON 再解析
    blob = re.sub(r"([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', blob)
    return json.loads(blob.replace("'", '"'))


class TestProMapParity(unittest.TestCase):
    def test_js_table_matches_python(self):
        js, py = js_pro_map(), I.PRO_MAP
        self.assertEqual(sorted(js), sorted(py), "族名不一致")
        for fam in py:
            with self.subTest(family=fam):
                self.assertEqual(js[fam], py[fam], "族 %s 的码表不一致" % fam)

    def test_every_code_covered(self):
        """20 个码必须至少被一个族收录，否则那类企业永远判「不是这方向」。"""
        codes = {c[0] for c in I.CODES}
        used = {code for m in I.PRO_MAP.values() for code in m}
        self.assertEqual(codes - used, set(), "有码没进任何族")

    def test_every_family_has_core(self):
        """每个族至少要有一个 core，否则这个专业的学生一个「直放」都没有。"""
        for fam, m in I.PRO_MAP.items():
            with self.subTest(family=fam):
                self.assertIn("core", m.values(), "族 %s 没有 core" % fam)

    def test_values_are_valid(self):
        for fam, m in I.PRO_MAP.items():
            for code, v in m.items():
                with self.subTest(family=fam, code=code):
                    self.assertIn(v, ("core", "edge"))
                    self.assertIn(code, {c[0] for c in I.CODES})


class TestFit(unittest.TestCase):
    def test_core_edge_out(self):
        self.assertEqual(I.fit(["化学化工材料"], "chem"), "core")
        self.assertEqual(I.fit(["化学化工材料"], "elec"), "edge")
        self.assertEqual(I.fit(["化学化工材料"], "logistics"), "out")
        self.assertEqual(I.fit(["土木建筑"], "mech"), "out")

    def test_multi_family_takes_loosest(self):
        self.assertEqual(I.fit(["土木建筑", "化学化工材料"], "chem"), "core")
        self.assertEqual(I.fit(["土木建筑", "化学化工材料"], "elec"), "edge")

    def test_unknown_returns_none(self):
        """判不了必须返回 None（退回关键词），不能返回 out 把人毙掉。"""
        self.assertIsNone(I.fit([], "chem"))
        self.assertIsNone(I.fit(["化学化工材料"], ""))
        self.assertIsNone(I.fit(["化学化工材料"], "unknown"))
        self.assertIsNone(I.fit(["化学化工材料"], "不存在的码"))


class TestTableVerdict(unittest.TestCase):
    """真实翻车案例：土木靠「工程」二字全线放行、化学被窄词表卡死。"""

    def test_civil_does_not_leak(self):
        self.assertEqual(B.table_verdict("土木工程", "施工员", "mech"), "out")
        self.assertEqual(B.table_verdict("土木工程", "施工员", "it"), "out")
        self.assertEqual(B.table_verdict("土木工程", "施工员", "build"), "core")

    def test_chem_to_logistics_not_core(self):
        """物流的宽词表（制造/生产）曾把化学学生放进物流公司做研发。"""
        self.assertEqual(B.table_verdict("化学工程与工艺", "化学分析工程师", "logistics"),
                         "out")

    def test_major_unknown_falls_back_to_intent(self):
        self.assertEqual(B.table_verdict("", "物流专员", "logistics"), "core")

    def test_nothing_known(self):
        self.assertIsNone(B.table_verdict("", "管培生", "logistics"))


class TestRecheckWithCode(unittest.TestCase):
    """有码走表、无码走词：两条通路互不污染。"""

    # 机械装备厂：化学的强词一个都不该命中（没有材料/化工/电解这类词）
    BIZ = "数控机床、钣金机柜、精密传动部件的研发与制造"

    def test_out_blocks_directionless_job(self):
        r = B.recheck_real([("工艺工程师", "工艺工程师")], ("工艺",), {"化学化工材料"},
                           self.BIZ, "化学工程与工艺", code="mech")
        self.assertEqual(len(r["kept"]), 0)
        self.assertEqual(r["diag"][0]["code"], "code_out")

    def test_out_blocks_named_direction_too(self):
        """2026-11-18 收紧（用户拍板「都得复检」）：岗位名自带方向不再比码硬。

        半导体公司的「研发工程师」对生物学生，公告里有、意向也有，主营对不上
        就是匹配不上——机械码对化学判 out，「材料研发工程师」也拦。
        """
        r = B.recheck_real([("材料研发工程师", "材料研发工程师")], ("材料",),
                           {"化学化工材料"}, self.BIZ, "化学工程与工艺", code="mech")
        self.assertEqual(len(r["kept"]), 0)
        self.assertEqual(r["blocked"], [("材料研发工程师", "材料研发工程师")])

    def test_generic_job_out_code_also_blocked(self):
        """通用职能岗白名单同样不再是免检金牌：out 码下拦（都得复检）。"""
        r = B.recheck_real([("采购专员", "采购专员")], ("采购",), {"化学化工材料"},
                           self.BIZ, "化学工程与工艺", code="mech")
        self.assertEqual(len(r["kept"]), 0)

    def test_generic_job_no_code_is_weak(self):
        """没打码时通用职能岗放行但算有限对口（没验过主营，不装通过）。"""
        r = B.recheck_real([("采购专员", "采购专员")], ("采购",), {"化学化工材料"},
                           self.BIZ, "化学工程与工艺", code=None)
        self.assertEqual(r["kept"], [])
        self.assertEqual(r["weak"], [("采购专员", "采购专员")])

    def test_no_code_uses_keywords(self):
        """企业没打码 → 退回关键词口径，不能当成「不放」。"""
        r = B.recheck_real([("工艺工程师", "工艺工程师")], ("工艺",), {"化学化工材料"},
                           "化工新材料研发生产", "化学工程与工艺", code=None)
        self.assertEqual(len(r["kept"]), 1)

    def test_edge_kept_as_weak(self):
        """edge：2026-11-18 起一律算有限对口（weak）——命中保留显示、沉队尾，
        不再因为「岗位名/主营带了方向词」就升成确定对口。"""
        biz = "床上用品、家纺面料的设计与销售"
        r = B.recheck_real([("研发工程师", "研发工程师")], ("研发",),
                           {"化学化工材料"}, biz, "化学工程与工艺", code="textile")
        self.assertEqual(r["weak"], [("研发工程师", "研发工程师")])
        self.assertEqual(r["kept"], [])
        r2 = B.recheck_real([("研发工程师", "材料研发工程师")], ("研发",),
                            {"化学化工材料"}, biz, "化学工程与工艺", code="textile")
        self.assertEqual(r2["weak"], [("研发工程师", "材料研发工程师")])
        # 主营里出现本领域词（印染）也只算有限对口：码是 textile，行业只沾边
        r3 = B.recheck_real([("研发工程师", "研发工程师")], ("研发",),
                            {"化学化工材料"}, "家纺面料印染与后整理",
                            "化学工程与工艺", code="textile")
        self.assertEqual(r3["weak"], [("研发工程师", "研发工程师")])


if __name__ == "__main__":
    unittest.main()
