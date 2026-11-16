# -*- coding: utf-8 -*-
"""入库时 bizScope → biz_scope 的键名转换。

采集契约用驼峰（bizScope / jobSource / jobEvidence），而匹配端（job_direction、
fair_bridge）读的是 item_json 里的 biz_scope。这里不转，就会出现「采集明明抓到了
主营、报告里仍当这家没写」——两套键名各读各的，谁都不报错，最难查。
"""
import unittest

import load_to_db as L


class ItemMappingTests(unittest.TestCase):
    def test_biz_scope_mapped_to_snake_case(self):
        item = L.item_of({"name": "测试企业", "bizScope": "汽车空调压缩机研发制造"})
        self.assertEqual(item["biz_scope"], "汽车空调压缩机研发制造")
        self.assertNotIn("bizScope", item)

    def test_unknown_scope_not_written(self):
        for scope in ("未知", "", None):
            item = L.item_of({"name": "测试企业", "bizScope": scope})
            self.assertNotIn("biz_scope", item)

    def test_other_fields_untouched(self):
        item = L.item_of({"name": "测试企业", "nature": "民企",
                          "bizScope": "铜产品制造", "jobs": []})
        self.assertEqual(item["nature"], "民企")
        self.assertEqual(item["jobs"], [])


if __name__ == "__main__":
    unittest.main()
