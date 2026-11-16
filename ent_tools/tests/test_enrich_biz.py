# -*- coding: utf-8 -*-
"""采集链路里的「主营」必须能在网页原文找到出处。

背景：主营（biz_scope）是匹配端判「岗位方向」的唯一证据源。产线上让模型只凭企业名
补主营时，它会写出「专注于高端装备制造」这种对谁都成立、又完全不对的说法；方向判错
的代价是误杀真匹配，比空着更糟。所以 build_entry 里做一道硬校验：bizEvidence 必须
逐字出现在 page_text 里，否则整条主营退成「未知」。
"""
import unittest

import enrich_by_llm as E

PAGE = ("【企业信息页面】\n某某精工股份有限公司成立于 2003 年，专注于汽车空调压缩机"
        "及整车热管理系统的研发与制造，产品配套多家新能源整车厂。")


def raw_of(**kw):
    base = {"nature": "民企", "city": "上海", "industry": "制造",
            "biz_scope": "未知", "bizEvidence": "",
            "jobSource": "未知", "jobs": []}
    base.update(kw)
    return base


class BizScopeQuoteTests(unittest.TestCase):
    def test_quoted_biz_is_kept(self):
        e = E.build_entry("测试企业", raw_of(
            biz_scope="汽车空调压缩机及整车热管理系统的研发制造",
            bizEvidence="专注于汽车空调压缩机及整车热管理系统的研发与制造"), PAGE, "测试来源")
        self.assertEqual(e["biz_scope"], "汽车空调压缩机及整车热管理系统的研发制造")

    def test_invented_biz_is_dropped(self):
        e = E.build_entry("测试企业", raw_of(
            biz_scope="高端装备制造与工业自动化整体解决方案",
            bizEvidence="公司专注于高端装备制造"), PAGE, "测试来源")
        self.assertEqual(e["biz_scope"], "未知")

    def test_biz_without_quote_is_dropped(self):
        e = E.build_entry("测试企业", raw_of(biz_scope="汽车零部件"), PAGE, "测试来源")
        self.assertEqual(e["biz_scope"], "未知")

    def test_biz_survives_when_no_jobs(self):
        """只有业务介绍、没有岗位公告的企业——主营恰恰是它唯一的价值，不能被清空。"""
        e = E.build_entry("测试企业", raw_of(
            biz_scope="汽车空调压缩机研发制造",
            bizEvidence="专注于汽车空调压缩机"), PAGE, "测试来源")
        self.assertEqual(e["jobs"], [])
        self.assertEqual(e["jobSource"], "未知")
        self.assertEqual(e["biz_scope"], "汽车空调压缩机研发制造")

    def test_unknown_literal_normalized(self):
        e = E.build_entry("测试企业", raw_of(biz_scope="未知", bizEvidence="不存在的句子"),
                          PAGE, "测试来源")
        self.assertEqual(e["biz_scope"], "未知")


if __name__ == "__main__":
    unittest.main()
