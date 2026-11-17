# -*- coding: utf-8 -*-
"""名单→档案这两个口子的守卫。

两件事都有真实事故在后面：
  · 「成都中科集团股份有限公司」搜到了北京的中科实业集团，被当成这家企业，
    岗位（党委办公室主任/战略发展部总经理）全算到成都那家头上；
  · 模型把公告里的小标题当岗位名抄出来（「质量」「生产」），学生看不到投什么。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import job_recommend
import portal


class NameCityTest(unittest.TestCase):
    def test_city_prefixed_name(self):
        self.assertEqual(portal.name_city("成都中科集团股份有限公司"), "成都")
        self.assertEqual(portal.name_city("南京晨曦电子股份有限公司"), "南京")
        self.assertEqual(portal.name_city("北京晨曦科技有限公司"), "北京")

    def test_no_city_in_name(self):
        self.assertEqual(portal.name_city("华为技术有限公司"), "")
        self.assertEqual(portal.name_city("中国节能环保集团有限公司"), "")

    def test_branch_name_not_mistaken_for_hq(self):
        # 括号里的城市是分支机构，只有开头才算总部所在地
        self.assertEqual(portal.name_city("XX（上海）有限公司"), "")
        self.assertEqual(portal.name_city("上海XX（北京）有限公司"), "上海")


class ShortTitleTest(unittest.TestCase):
    def test_domain_word_is_not_a_job(self):
        for t in ("质量", "生产", "研发", "工艺", "销售", "采购", "行政"):
            self.assertTrue(job_recommend._bad_title(t), t)

    def test_real_two_char_jobs_kept(self):
        for t in ("会计", "审计", "法务", "出纳", "美工", "编辑", "客服"):
            self.assertFalse(job_recommend._bad_title(t), t)

    def test_full_titles_untouched(self):
        for t in ("质量管理专员", "材料工艺工程师", "供应链计划专员", "EHS 工程师"):
            self.assertFalse(job_recommend._bad_title(t), t)


if __name__ == "__main__":
    unittest.main()
