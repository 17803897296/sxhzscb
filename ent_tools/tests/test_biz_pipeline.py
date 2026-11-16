# -*- coding: utf-8 -*-
"""端到端：搜索 → 提取主营 → 契约 → 入库映射 → 岗位方向。

这条链以前断在第一步（采集不产主营），于是下游所有方向判定对新企业全部失效，
而每一段自己都不报错。这个测试把整条链钉住：任何一段改了字段名或丢了主营，立刻红。
"""
import unittest

import company_module as M
import enrich_by_llm as E
import job_direction as D
import load_to_db as L

NAME = "某铜业股份有限公司"
PAGE = ("【企业信息页面】\n某铜业股份有限公司主营铜产品及先进材料制造，"
        "为新能源汽车、消费电子等行业提供材料解决方案。")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        raw = {
            "nature": "民企", "city": "宁波", "industry": "制造",
            "biz_scope": "铜产品及先进材料制造，为新能源汽车、消费电子提供材料",
            "bizEvidence": "主营铜产品及先进材料制造",
            "jobSource": "未知", "jobs": [],
        }
        self.entry = E.build_entry(NAME, raw, PAGE, "测试来源")

    def test_scope_reaches_db_item(self):
        c = M.build_companies([NAME], [self.entry])[0]
        self.assertIn("铜产品", c["biz_scope"])
        item = L.item_of(c)
        self.assertIn("铜产品", item["biz_scope"])

    def test_scope_drives_job_direction(self):
        c = M.build_companies([NAME], [self.entry])[0]
        item = L.item_of(c)
        biz = D.biz_text(item.get("industry"), item.get("segment"), item.get("biz_scope"))
        dirs = [x for x, _k in D.resolve("工艺工程师", biz)["dirs"]]
        self.assertIn("metal", dirs, "铜产品制造的企业，工艺岗应判金属材料方向")

    def test_empty_scope_degrades_to_no_direction(self):
        """采集没抓到主营时，方向判定必须空着（→ 现场问），不能猜。"""
        raw = {"nature": "民企", "city": "宁波", "industry": "制造",
               "biz_scope": "未知", "bizEvidence": "", "jobSource": "未知", "jobs": []}
        entry = E.build_entry(NAME, raw, PAGE, "测试来源")
        c = M.build_companies([NAME], [entry])[0]
        item = L.item_of(c)
        self.assertNotIn("biz_scope", item)
        biz = D.biz_text(item.get("industry"), None, None)
        self.assertEqual(D.resolve("工艺工程师", biz)["dirs"], [])


if __name__ == "__main__":
    unittest.main()
