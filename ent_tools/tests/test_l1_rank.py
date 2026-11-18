# -*- coding: utf-8 -*-
"""模型推荐的岗位顺序：切合本专业 L1 的要前置。

2026-11-18 用户实测：采集端填「化学」，推荐第一条是「材料研发工程师」
（模型返回的顺序原样上屏，而它在化学池子里只是 L2）。用户定的口径是
「模型返回的满足 L1 也应该放前面，而不是全指望 93 类专业池那张表」。

这里守三条：
  1. 专属岗 > L1 > 同族 > 无关，四级判据能区分；
  2. 重排只动顺序，一个岗位都不删；
  3. 表外专业（93 类池子没收的具体专业）也必须能判出切合岗。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import job_recommend as J  # noqa: E402


class L1RankTests(unittest.TestCase):
    def test_own_job_ranks_top(self):
        # 研发工程师是化学的专属岗
        self.assertEqual(J._l1_rank("化学", "研发工程师"), 3)

    def test_neighbor_major_job_is_not_top(self):
        # 材料研发工程师对化学只能算同族（共享「研发」），不能跟专属岗同分
        self.assertLess(J._l1_rank("化学", "材料研发工程师"),
                        J._l1_rank("化学", "研发工程师"))
        self.assertGreaterEqual(J._l1_rank("化学", "材料研发工程师"), 1)

    def test_unrelated_ranks_zero(self):
        self.assertEqual(J._l1_rank("化学", "算法工程师"), 0)
        self.assertEqual(J._l1_rank("化学", "管培生"), 0)

    def test_major_outside_93_pools(self):
        # 心理学不在 93 个专业类的岗位池文件里，仍要判得出切合岗
        self.assertGreater(J._l1_rank("心理学", "心理咨询助理"), 0)
        self.assertGreater(J._l1_rank("心理学", "心理测评专员"), 0)

    def test_sort_只动顺序不删岗位(self):
        model = ["材料研发工程师", "研发工程师", "合成研究员",
                 "化验/质检岗", "分析测试岗", "工艺工程师"]
        items = [{"title": t, "why": ""} for t in model]
        out = sorted(items, key=lambda c: -J._l1_rank("化学", c["title"]))
        self.assertEqual(sorted(c["title"] for c in out), sorted(model))
        # 材料研发不该再排第一
        self.assertNotEqual(out[0]["title"], "材料研发工程师")
        self.assertEqual(out[0]["title"], "研发工程师")


if __name__ == "__main__":
    unittest.main()
