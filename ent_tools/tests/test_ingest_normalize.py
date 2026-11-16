# -*- coding: utf-8 -*-
"""画像入口清洗：脏意向不能把重算 worker 打挂。"""
import unittest
import fair_ingest


class TestNormalizeIntents(unittest.TestCase):
    def _doc(self, jobs):
        return {"answers": {"intended_jobs": jobs}, "identity": {"major": "测试"}}

    def test_filters_none_empty_and_blank(self):
        d = fair_ingest.normalize(self._doc(["工程师", None, "", "  ", 123]))
        self.assertEqual(d["answers"]["intended_jobs"], ["工程师", "123"])

    def test_all_dirty_becomes_empty(self):
        d = fair_ingest.normalize(self._doc([None, "", "  "]))
        self.assertEqual(d["answers"]["intended_jobs"], [])

    def test_keeps_valid_order(self):
        d = fair_ingest.normalize(self._doc(["A", "B", "C"]))
        self.assertEqual(d["answers"]["intended_jobs"], ["A", "B", "C"])

    def test_non_list_becomes_empty(self):
        d = fair_ingest.normalize(self._doc("工程师"))
        self.assertEqual(d["answers"]["intended_jobs"], [])

    def test_missing_key_untouched(self):
        d = fair_ingest.normalize({"answers": {}, "identity": {}})
        self.assertNotIn("intended_jobs", d["answers"])

    def test_join_is_safe(self):
        d = fair_ingest.normalize(self._doc(["A", None, "B"]))
        self.assertEqual("、".join(d["answers"]["intended_jobs"]), "A、B")


if __name__ == "__main__":
    unittest.main()
