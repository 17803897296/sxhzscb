# -*- coding: utf-8 -*-
"""岗位展示名后缀（v18）：跨行业通用岗要说清「在哪类企业里做」。

只测「展示名」这一层：title（提交给匹配端的取值）必须保持不变，否则匹配端
拿「研发工程师（化工/医药）」去比企业库里的「研发工程师」会对不上。
"""

import unittest

import job_recommend as J


class IndustryTagTests(unittest.TestCase):
    def test_tag_by_major_and_category(self):
        self.assertEqual(J.industry_tag("化学"), "化工/医药")
        self.assertEqual(J.industry_tag("化学工程与工艺"), "化工/医药")
        self.assertEqual(J.industry_tag("材料科学与工程"), "材料/新能源")
        self.assertEqual(J.industry_tag("机械设计制造及其自动化"), "机械/汽车")
        self.assertEqual(J.industry_tag("计算机科学与技术"), "互联网/软件")
        self.assertEqual(J.industry_tag("食品科学与工程"), "食品/快消")
        # 判不出大类的专业不加后缀，不要瞎猜
        self.assertEqual(J.industry_tag("玄学"), "")


class NeedsTagTests(unittest.TestCase):
    def test_cross_industry_func_job_gets_tag(self):
        for t in ("研发工程师", "工艺工程师", "质量工程师"):
            self.assertTrue(J._needs_tag(t, "化学"), t)

    def test_specific_job_gets_no_tag(self):
        # 名字里已经写明方向的，后缀是废话
        for t in ("化学分析工程师", "电气工程师", "食品研发工程师", "软件开发工程师"):
            self.assertFalse(J._needs_tag(t, "化学"), t)

    def test_job_outside_major_pool_gets_no_tag(self):
        # 计算机专业不该出现「工艺工程师（互联网/软件）」这种胡说
        self.assertFalse(J._needs_tag("工艺工程师", "计算机科学与技术"))

    def test_display_keeps_title_untouched(self):
        items = [{"title": "研发工程师", "why": "x"},
                 {"title": "合成研究员", "why": "x"}]
        out = J.with_display(items, "化学")
        self.assertEqual(out[0]["display"], "研发工程师（化工/医药）")
        self.assertEqual(out[0]["title"], "研发工程师")       # 提交值不变
        self.assertEqual(out[1]["display"], "合成研究员")      # 本身够具体
        self.assertEqual(out[1]["title"], "合成研究员")

    def test_does_not_mutate_pool_items(self):
        items = [{"title": "研发工程师", "why": "x"}]
        J.with_display(items, "化学")
        self.assertNotIn("display", items[0])                 # 缓存对象不被写脏


if __name__ == "__main__":
    unittest.main()
