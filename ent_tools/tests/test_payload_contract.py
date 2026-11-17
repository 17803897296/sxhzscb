# -*- coding: utf-8 -*-
"""payload 契约测试（评审 2026-11-18 Step 1 验收标准）。

验收：
- 提交版 payload 通过校验；
- 故意删掉 row["score"] / 顶层 weights → 必须失败；
- 类型改错（score 给字符串）→ 必须失败；
- schema_version 缺失/不匹配 → 必须失败。
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import payload_contract  # noqa: E402

PAYLOAD = os.path.join(HERE, "data", "fair_payload.json")


def _load_committed():
    if not os.path.exists(PAYLOAD):
        return None
    with open(PAYLOAD, encoding="utf-8") as f:
        return json.load(f)


class TestPayloadContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = _load_committed()
        if cls.payload is None:
            raise unittest.SkipTest("fair_payload.json 缺席（干净克隆）")

    def test_committed_payload_passes(self):
        """提交版 payload 必须通过契约校验（包括 schema_version 一致）。"""
        errors = payload_contract.validate(self.payload)
        self.assertEqual(errors, [], "违约点:\n  " + "\n  ".join(errors[:10]))

    def test_schema_version_matches_contract(self):
        schema = payload_contract.load_schema()
        self.assertEqual(self.payload.get("schema_version"), schema["version"])

    def test_missing_row_score_fails(self):
        """故意删 row["score"] → 必须红（验收标准原文）。"""
        bad = copy.deepcopy(self.payload)
        del bad["rows"][0]["score"]
        self.assertNotEqual(payload_contract.validate(bad), [])

    def test_missing_top_weights_fails(self):
        bad = copy.deepcopy(self.payload)
        del bad["weights"]
        errs = payload_contract.validate(bad)
        self.assertTrue(any("weights" in e for e in errs))

    def test_wrong_type_fails(self):
        bad = copy.deepcopy(self.payload)
        bad["rows"][0]["score"] = "92.5"
        errs = payload_contract.validate(bad)
        self.assertTrue(any("rows[0].score" in e for e in errs))

    def test_missing_schema_version_fails(self):
        bad = copy.deepcopy(self.payload)
        del bad["schema_version"]
        errs = payload_contract.validate(bad)
        self.assertTrue(any("schema_version" in e for e in errs))

    def test_wrong_schema_version_fails(self):
        bad = copy.deepcopy(self.payload)
        bad["schema_version"] = 999
        errs = payload_contract.validate(bad)
        self.assertTrue(any("schema_version" in e for e in errs))

    def test_state_domain_enforced(self):
        bad = copy.deepcopy(self.payload)
        bad["rows"][0]["state"] = "HIT"   # 大写：三态分级是精确匹配
        errs = payload_contract.validate(bad)
        self.assertTrue(any("state" in e for e in errs))

    def test_extra_field_allowed(self):
        """加字段不违约（向后兼容：前端忽略未知键）。"""
        ok = copy.deepcopy(self.payload)
        ok["rows"][0]["_future_note"] = "新字段"
        self.assertEqual(payload_contract.validate(ok), [])

    def test_ensure_valid_raises(self):
        bad = copy.deepcopy(self.payload)
        del bad["rows"][0]["name"]
        with self.assertRaises(payload_contract.PayloadContractError):
            payload_contract.ensure_valid(bad)


if __name__ == "__main__":
    unittest.main()
