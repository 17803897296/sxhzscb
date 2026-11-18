# -*- coding: utf-8 -*-
"""虚拟用户端到端常态化测试（CI 必跑，离线、不依赖 DB / 服务 / LLM）。

包装 virtual_user_e2e 的核心两段（推荐全量翻页 + 常识复核），
16 个虚拟用户覆盖本次修的全部重灾区 + 防回归代表专业。
145 家匹配那段依赖 DB / 联网复核，不放进 CI（用
`python -m ent_tools.virtual_user_e2e --with-match` 手动跑）。
"""
import unittest

import virtual_user_e2e as E2E


class TestVirtualUserE2E(unittest.TestCase):
    def test_all_profiles_pass(self):
        for prof in E2E.PROFILES:
            rec = E2E.check_recommend(prof)
            with self.subTest(tag=prof["tag"], major=prof["major"]):
                self.assertTrue(rec["ok"], "%s: %s" % (
                    prof["tag"], "；".join(rec["problems"])))

    def test_every_profile_has_nonempty_l1(self):
        for prof in E2E.PROFILES:
            with self.subTest(tag=prof["tag"]):
                self.assertTrue(E2E.J.core_jobs(prof["major"]),
                                "%s 的 L1 为空" % prof["tag"])

    def test_l1_l2_no_duplicate(self):
        # 全量翻页不得在 L1/L2 重复展示同一个岗
        for prof in E2E.PROFILES:
            l1 = E2E.J.core_jobs(prof["major"])
            l2 = E2E.J.near_jobs(prof["major"])
            dup = [t for t in l1 if t in l2]
            with self.subTest(tag=prof["tag"], dup=dup):
                self.assertFalse(dup, "%s 的 L1/L2 重复: %s" % (
                    prof["tag"], "、".join(dup)))


if __name__ == "__main__":
    unittest.main()
