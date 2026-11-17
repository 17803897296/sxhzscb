# -*- coding: utf-8 -*-
"""前后端词表一致性：biz_recheck.FIELDS ↔ 页面 BIZ_FIELDS。

这条测试是补一次真实翻车：给后端补了细分词（FIELD_EXTRA），前端表里删了
「薄膜/镀膜/涂层」（设备语境会误判），两边就对不上了 —— 后端判对口、页面
判不对口，同一家企业两处结论相反。词表是两份手写副本，只能靠测试钉住。

约定：前端 BIZ_FIELDS 的第 4 列 = 后端强词 + FIELD_EXTRA[领域]（顺序也要一致，
否则 parity 的逐对比较虽然过，展示层取「命中词」会取到不同的词）。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import biz_recheck as B  # noqa: E402
import fair_report_js as J  # noqa: E402


def js_table():
    s = J.REPORT_JS
    a = s.index("const BIZ_FIELDS=[")
    b = s.index("];", a) + 1
    blob = s[a:b].split("=", 1)[1].strip().rstrip(";")
    return json.loads(blob.replace("'", '"'))


class TestBizTableParity(unittest.TestCase):
    def test_same_fields_and_order(self):
        js = js_table()
        py = [f[0] for f in B.FIELDS]
        self.assertEqual([j[0] for j in js], py)

    def test_same_words_each_column(self):
        js = {j[0]: j for j in js_table()}
        for f in B.FIELDS:
            name = f[0]
            with self.subTest(field=name):
                self.assertEqual(list(js[name][1]), list(f[1]), "专业标记不一致")
                self.assertEqual(list(js[name][2]), list(f[2]), "意向标记不一致")
                self.assertEqual(list(js[name][3]),
                                 list(f[3]) + list(B.FIELD_EXTRA.get(name, ())),
                                 "强词+细分词不一致")
                self.assertEqual(list(js[name][4]), list(f[4]), "弱词不一致")

    def test_extra_words_are_not_in_strong(self):
        """细分词与强词不能重复：重复会让「别的行业」判定把自家词算进去。"""
        for field, extra in B.FIELD_EXTRA.items():
            dup = set(extra) & set(B._STRONG_BY_FIELD.get(field) or ())
            self.assertFalse(dup, "%s 细分词与强词重复：%s" % (field, dup))


if __name__ == "__main__":
    unittest.main()
