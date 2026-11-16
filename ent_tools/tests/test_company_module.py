import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import company_module as m


def cache_record():
    # 人工构造的测试数据，绝不写入产品 data/。
    return {
        "name": "测试企业", "nature": "民企", "industry": "IT",
        "jobSource": "主营业务推测", "jobEvidence": "从事软件开发",
        "source": "测试资料标识", "text": "测试原文：本单位从事软件开发。",
        "jobs": [{"title": "软件开发", "urgency": "未知"}],
    }


class CompanyContractTests(unittest.TestCase):
    def test_unknown_complete_contract(self):
        result = m.build_companies(["甲企业"], [])
        self.assertEqual(set(result[0]), set(m.COMPANY_KEYS))
        self.assertEqual(result[0]["nature"], "未知")
        self.assertEqual(result[0]["industry"], "未知")
        self.assertEqual(result[0]["jobs"], [])
        m.reject_missing(result)

    def test_known_inference_and_job_defaults(self):
        c = m.build_companies(["测试企业"], [cache_record()])[0]
        self.assertEqual(set(c["jobs"][0]), set(m.JOB_KEYS))
        self.assertEqual(c["jobs"][0]["headcount"], "未知")
        self.assertEqual(c["jobSource"], "主营业务推测")

    def test_announced_jobs_and_soft_preferences_are_not_filters(self):
        raw = cache_record()
        raw["nature"] = "国企"
        raw["jobSource"] = "校招公告"
        raw["jobs"] = [{"title": "研发岗位"}, {"title": "运营岗位"}]
        result = m.build_companies(["测试企业"], [raw])
        self.assertEqual([j["title"] for j in result[0]["jobs"]], ["研发岗位", "运营岗位"])
        self.assertEqual(result[0]["nature"], "国企")

    def test_biz_scope_carried_from_cache(self):
        """主营要跟着采集结果进契约：没有这一列，匹配端就判不出岗位方向。"""
        raw = cache_record()
        raw["biz_scope"] = "汽车空调压缩机及热管理系统的研发生产"
        c = m.build_companies(["测试企业"], [raw])[0]
        self.assertEqual(c["biz_scope"], "汽车空调压缩机及热管理系统的研发生产")
        m.validate_companies([c])

    def test_biz_scope_defaults_to_unknown(self):
        """老缓存 / 抓不到业务介绍的企业：写「未知」，别写空串也别编。"""
        c = m.build_companies(["测试企业"], [cache_record()])[0]
        self.assertEqual(c["biz_scope"], "未知")

    def test_biz_scope_rejects_non_string(self):
        raw = cache_record()
        raw["biz_scope"] = ["做压缩机"]
        with self.assertRaises(m.ContractError):
            m.build_companies(["测试企业"], [raw])

    def test_saved_enum_whitespace_is_invalid(self):
        c = m.build_companies(["甲企业"], [])[0]
        c["nature"] = " 民企 "
        with self.assertRaises(m.ContractError):
            m.validate_companies([c])

    def test_inference_evidence_is_exact_quote(self):
        raw = cache_record()
        for quote in ["虚构片段", "", None, "未知"]:
            raw["jobEvidence"] = quote
            with self.assertRaises(m.ContractError):
                m.build_companies(["测试企业"], [raw])

    def test_unknown_source_cannot_have_jobs(self):
        raw = cache_record()
        raw["jobSource"] = "未知"
        with self.assertRaises(m.ContractError):
            m.build_companies(["测试企业"], [raw])

    def test_bad_enums_and_keys_rejected(self):
        for field, value in [("nature", "民营企业"), ("industry", "软件"), ("jobSource", "校招")]:
            raw = cache_record()
            raw[field] = value
            with self.assertRaises(m.ContractError):
                m.build_companies(["测试企业"], [raw])
        raw = cache_record()
        raw["companyName"] = "错误字段"
        with self.assertRaises(m.ContractError):
            m.build_companies(["测试企业"], [raw])

    def test_zero_headcount_preserved_but_bool_rejected(self):
        raw = cache_record()
        raw["jobs"][0]["headcount"] = 0
        self.assertEqual(m.build_companies(["测试企业"], [raw])[0]["jobs"][0]["headcount"], 0)
        for value in [True, -1, "3", 1.5]:
            raw["jobs"][0]["headcount"] = value
            with self.assertRaises(m.ContractError):
                m.build_companies(["测试企业"], [raw])

    def test_input_missing_values_normalized(self):
        raw = {"name": "甲企业", "nature": None, "city": "  ", "industry": ""}
        m.reject_missing(m.build_companies(["甲企业"], [raw]))

    def test_final_missing_or_extra_fields_rejected(self):
        c = m.build_companies(["甲企业"], [])[0]
        for key in m.COMPANY_KEYS:
            invalid = copy.deepcopy(c)
            del invalid[key]
            with self.assertRaises(m.ContractError):
                m.validate_companies([invalid])
        for bad in [None, "", " "]:
            invalid = copy.deepcopy(c)
            invalid["city"] = bad
            with self.assertRaises(m.ContractError):
                m.validate_companies([invalid])

    def test_stable_ids_and_exact_name_deduplication(self):
        a = m.build_companies(["甲企业", "乙企业", "甲企业"], [])
        b = m.build_companies(["乙企业", "甲企业"], [])
        self.assertEqual(len(a), 2)
        self.assertEqual(a[0]["id"], b[1]["id"])

    def test_path_boundary(self):
        for value in ["output.json", "data/../bad.json", str(Path.cwd() / "data/a.json")]:
            with self.assertRaises(m.ContractError):
                m.data_path(value)

    def test_offline_execution_and_atomic_failure(self):
        import os
        before = Path.cwd()
        Path("data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="data") as folder:
            os.chdir(folder)
            try:
                m.write_json("data/company_names.json", ["甲企业"])
                m.write_json("data/research_cache.json", [])
                # 即使禁止 socket 创建，整条生成链路也必须可用。
                with patch("socket.socket", side_effect=AssertionError("network forbidden")):
                    self.assertEqual(m.main([]), 0)
                saved = Path("data/companies.json").read_bytes()
                m.write_json("data/research_cache.json", [{"name": "甲企业", "nature": "错误枚举"}])
                self.assertEqual(m.main([]), 1)
                self.assertEqual(saved, Path("data/companies.json").read_bytes())
            finally:
                os.chdir(before)

    def test_invalid_date_and_duplicate_cache(self):
        for date in ["2026-02-30", "2026/10/29"]:
            with self.assertRaises(m.ContractError):
                m.build_companies(["甲企业"], [{"name": "甲企业", "date": date}])
        with self.assertRaises(m.ContractError):
            m.build_companies(["测试企业"], [cache_record(), cache_record()])


if __name__ == "__main__":
    unittest.main()
