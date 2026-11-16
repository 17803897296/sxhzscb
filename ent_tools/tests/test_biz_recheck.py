# -*- coding: utf-8 -*-
"""画像×主营复检（biz_recheck）单测 —— 2026-11-17 用户定口径。

核心断言：命中不能只看「岗位名 ↔ 意向」字面，还要看「企业主营业务 + 岗位名」
是否对得上「意向岗位 + 专业」。所有案例都来自真实库内企业与真实翻车记录。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import biz_recheck as B  # noqa: E402

LOG_KW, LOG_FIELDS = B.student_domain(
    ["管培生", "物流专员", "供应链专员", "仓储管理员"], "物流管理")
CHEM_KW, CHEM_FIELDS = B.student_domain(
    ["化学分析工程师", "工艺工程师", "研发工程师", "检测工程师", "实验室技术员",
     "化工销售工程师", "环境监测工程师", "采购专员"], "化学工程与工艺")


class TestStudentDomain(unittest.TestCase):
    def test_logistics_domain(self):
        self.assertIn("物流供应链", LOG_FIELDS)
        self.assertIn("物流", LOG_KW)
        self.assertIn("供应链", LOG_KW)
        self.assertIn("仓储", LOG_KW)

    def test_chem_domain(self):
        self.assertIn("化学化工材料", CHEM_FIELDS)
        self.assertIn("化学", CHEM_KW)

    def test_generic_intent_leaves_no_core(self):
        # 意向只有「管培生」且专业为空 → 方向词为空 → 闸门放行（不咬人）
        kw, fields = B.student_domain(["管培生"], "")
        self.assertEqual(kw, ())
        self.assertEqual(fields, set())

    def test_major_alone_gives_field(self):
        kw, fields = B.student_domain([], "物流管理")
        self.assertIn("物流供应链", fields)
        self.assertTrue(kw)

    def test_strip_generic_longest_first(self):
        # 「管理培训生」不能被「培训生」吃一半剩个「管理」
        self.assertEqual(B._strip_generic("管理培训生"), "")
        self.assertEqual(B._strip_generic("供应链管培生"), "供应链")
        self.assertEqual(B._strip_generic("物流专员"), "物流")


class TestRealPairs(unittest.TestCase):
    """明确命中复检：岗位名自带方向词放行；否则主营必须容纳方向。"""

    def test_finance_manager_trainee_blocked(self):
        # 反面案例（用户原话）：金融公司管培生 × 物流专业选的管培生
        kept, blocked = B.filter_real(
            [("管培生", "管培生")], LOG_KW, LOG_FIELDS,
            "全国性商业银行，主营公司金融、零售金融与财富管理业务")
        self.assertEqual(kept, [])
        self.assertEqual(blocked, [("管培生", "管培生")])

    def test_logistics_company_trainee_kept(self):
        kept, blocked = B.filter_real(
            [("管培生", "管培生")], LOG_KW, LOG_FIELDS,
            "综合物流服务提供商，主营仓配一体、快递快运与供应链管理")
        self.assertEqual(kept, [("管培生", "管培生")])
        self.assertEqual(blocked, [])

    def test_title_with_direction_word_needs_no_biz(self):
        # 本家公告自带方向词（仓储物流 / 供应链管理储备干部）比主营更硬
        kept, blocked = B.filter_real(
            [("仓储物流", "物流专员"), ("供应链管理储备干部", "供应链专员")],
            LOG_KW, LOG_FIELDS, "专用集成电路、感知芯片与AI智能整机研发")
        self.assertEqual(len(kept), 2)
        self.assertEqual(blocked, [])

    def test_empty_domain_words_pass_all(self):
        # 方向词为空（无法判断）→ 全部放行
        kept, blocked = B.filter_real(
            [("任意岗", "任意意向")], (), set(), "随便什么主营")
        self.assertEqual(len(kept), 1)
        self.assertEqual(blocked, [])


class TestInferPairs(unittest.TestCase):
    """推断命中复检：岗位名是猜的不能自证，主营必须容纳方向。"""

    def _infer(self, biz, fields=None):
        return B.filter_infer([("物流专员", "物流专员")],
                              fields if fields is not None else LOG_FIELDS, biz)

    def test_edu_publisher_blocked(self):
        # 江苏乔木教育（图书出版）推断「物流专员」—— 主营与物流对不上
        self.assertEqual(self._infer(
            "教育图书出版策划公司，以高考研究统领教育图书研发与高端提优产品"), [])

    def test_it_cloud_blocked(self):
        # 南京曙光新材料（实为 IT 云计算）推断「物流专员」
        self.assertEqual(self._infer(
            "高性能计算机、存储产品、云计算平台、人工智能服务及网络安全产品"), [])

    def test_real_estate_blocked(self):
        self.assertEqual(self._infer("房地产开发经营、环保、投资与资产管理、物业管理"), [])

    def test_empty_biz_blocked(self):
        # 山爻科技主营为空：没有业务依据就不该推断
        self.assertEqual(self._infer(""), [])

    def test_junk_nav_text_blocked(self):
        # 中国节能环保集团：主营是网页导航垃圾，「安全生产」是栏目标题不是业务词
        self.assertEqual(self._infer(
            "主营业务 安全生产 节能期刊 节能风采 社会责任 时政要闻 未知"), [])

    def test_cross_border_ecommerce_kept(self):
        # 通达致远（跨境电商）：物流是主业的一环
        self.assertTrue(self._infer("跨境电商B2C运营（Amazon、eBay、Walmart）"))

    def test_manufacturer_kept(self):
        # 天山铝业（大宗制造）：生产强词一票通过
        self.assertTrue(self._infer(
            "铝土矿、氧化铝、发电、电解铝、电池铝箔及其他铝深加工等生产环节"))

    def test_synonym_weak_words_do_not_stack(self):
        # 奕兴格商贸：merge 后 industry=贸易、主营含「商贸」—— 同族弱词
        # 只算一族，凑不满两族（2026-11-17 翻车案例）
        self.assertFalse(B.biz_hit(LOG_FIELDS, B.biz_text_of("武汉奕兴格商贸有限公司", {
            "industry": "贸易",
            "biz_scope": "小微商贸公司，主营家用电器、五金、电子产品等销售，及家用电器安装、修理与保洁服务。",
        })))

    def test_weak_words_do_not_pass_biz_check(self):
        # 弱词（商贸/贸易/超市/出口…）不参与主营判定（强词一票制）：
        # 奕兴格商贸曾靠「贸易+商贸」同族词误过闸（2026-11-17）
        self.assertFalse(B.biz_hit(LOG_FIELDS, B.biz_text_of("武汉奕兴格商贸有限公司", {
            "industry": "贸易",
            "biz_scope": "小微商贸公司，主营家用电器、五金、电子产品等销售，及家用电器安装、修理与保洁服务。",
        })))

    def test_weak_words_still_match_titles(self):
        # 弱词只参与岗位名/意向名的方向词匹配：物流专员核心词在 kw 里
        self.assertIn("物流", LOG_KW)

    def test_empty_fields_pass_all(self):
        self.assertEqual(B.filter_infer([("任意岗", "任意意向")], set(), "随便"), [("任意岗", "任意意向")])


class TestChemProfileNoRegression(unittest.TestCase):
    """化工画像回归：复检不得误伤原本合理的命中。"""

    def test_pharma_analysis_kept(self):
        kept, blocked = B.filter_real(
            [("药物分析研究员", "化学分析工程师")], CHEM_KW, CHEM_FIELDS,
            "医药产品研发、生产、销售及相关健康服务")
        self.assertEqual(kept, [("药物分析研究员", "化学分析工程师")])
        self.assertEqual(blocked, [])

    def test_copper_factory_qc_kept(self):
        kept, blocked = B.filter_real(
            [("质量工程师", "检测工程师")], CHEM_KW, CHEM_FIELDS,
            "专注于铜产品及先进材料制造，为新能源汽车、消费电子等供货")
        self.assertEqual(kept, [("质量工程师", "检测工程师")])
        self.assertEqual(blocked, [])

    def test_blocked_why_mentions_biz(self):
        why = B.blocked_why("管培生", "管培生",
                            "全国性商业银行，主营公司金融、零售金融与财富管理业务",
                            "物流管理", ["管培生", "物流专员"])
        self.assertIn("主营", why)
        self.assertIn("物流管理", why)


if __name__ == "__main__":
    unittest.main()
