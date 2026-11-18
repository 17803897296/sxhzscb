# -*- coding: utf-8 -*-
"""复检展示行回归守卫（2026-11-18 薄化回归）。

正本页面（FULL_ENGINE=false）已剥离本地兜底 miBizKw/miCodeFit，
report.js 的复检展示行只吃 payload 每行的 biz_recheck 字段。历史 bug：
fair_bridge 只在「被问」的对上生成 biz_rk，FIT_CORE 直放 / 通用职能岗放行
都不写 diag → 48 行里 46 行 biz_recheck=None → 前端整段不显示复检行。

这条测试锁住不变量：真实构建出来的 payload，每一行都必须带非空 biz_recheck，
且 code 落在前端认识的枚举里。等价于把「复检行消失」这个回归钉死。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fair_bridge  # noqa: E402

# 前端 report.js 的 _rk() 认识的 code；多一个不认识的就等于白渲染。
KNOWN_CODES = {"name_dir", "biz_missing", "biz_no_word", "biz_weak", "biz_other",
               "code_core", "code_edge", "code_out"}


def _official_profile():
    """从只读种子库取正式生效画像，避免测试依赖活库的临时测试画像。"""
    import json
    import sqlite3
    seed = os.path.join(os.path.dirname(__file__), "..", "data", "careersafari.db.seed")
    con = sqlite3.connect("file:%s?mode=ro" % seed.replace("\\", "/"), uri=True)
    try:
        fid = con.execute("select fair_id from fair_active where id='current'").fetchone()[0]
        doc = json.loads(con.execute(
            "select document_json from fair_profiles where fair_id=?", (fid,)).fetchone()[0])
    finally:
        con.close()
    return doc


class TestRecheckDisplayRow(unittest.TestCase):
    def test_every_row_has_visible_recheck_verdict(self):
        out = fair_bridge.build_preview(_official_profile())
        rows = out["rows"]
        self.assertTrue(rows, "payload 不该是空的")
        missing = [r.get("name") for r in rows if not r.get("biz_recheck")]
        self.assertEqual(missing, [],
                         "薄变体前端只认 biz_recheck，这些行的复检展示会消失: %s" % missing)
        bad_code = [(r.get("name"), r["biz_recheck"].get("code")) for r in rows
                    if r["biz_recheck"].get("code") not in KNOWN_CODES]
        self.assertEqual(bad_code, [],
                         "前端不认识的复检 code（渲染会落空）: %s" % bad_code)

    def test_core_direct_pass_gets_a_verdict(self):
        """FIT_CORE 直放行也必须给出 code_core，这正是历史漏掉的场景。"""
        out = fair_bridge.build_preview(_official_profile())
        codes = [r["biz_recheck"].get("code") for r in out["rows"]]
        # 正式画像下至少有一家是行业码 core 直放，否则这个 case 失去意义
        self.assertIn("code_core", codes,
                      "正式画像应有 core 直放的行业；若全没有，测试需换画像")


if __name__ == "__main__":
    unittest.main()
