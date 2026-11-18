# -*- coding: utf-8 -*-
"""专业闸门：相邻专业核心岗不得混进本专业的放宽层。

2026-11-18 用户实测：材料专业第 3 层（tier2 相邻有门槛）被混进
“化工工艺工程师 / 化工设备工程师 / 中试放大 / 催化研发”等化学工程核心岗。
根因：student_domain 把材料/化学/化工并进同一 field（化学化工材料），
_major_ok 只看合并词表就放行——把隔壁专业的核心岗当“放宽”推给学生。
"""
import unittest

import job_recommend as J


class TestNeighborCoreGuard(unittest.TestCase):
    def test_material_blocks_chemical_engineering_core(self):
        for title in ("化工工艺工程师", "化工设备工程师", "化工设计工程师",
                      "中试/放大工程师", "中试放大工程师", "催化研发工程师",
                      "危化品安全工程师"):
            with self.subTest(title=title):
                self.assertTrue(J._neighbor_core_blocked(title, "材料"))
                self.assertFalse(J._major_ok(title, "材料", 2))

    def test_chemical_major_keeps_own_core(self):
        # 化工/化学工程专业的学生，这些是自己的核心岗，不能拦
        for major in ("化学工程与工艺", "化学"):
            for title in ("化工工艺工程师", "化工设备工程师", "中试/放大工程师"):
                with self.subTest(major=major, title=title):
                    self.assertFalse(J._neighbor_core_blocked(title, major))

    def test_material_keeps_own_legit_jobs(self):
        # 材料可投的岗：材料检测、金相、理化检验、复合材料工艺、配方都保留
        for title in ("材料检测工程师", "金相分析工程师", "理化检验员",
                      "复合材料工艺工程师", "配方工程师", "材料研发工程师",
                      "失效分析工程师", "化验/质检岗", "分析测试岗"):
            with self.subTest(title=title):
                self.assertFalse(J._neighbor_core_blocked(title, "材料"))
                self.assertTrue(J._major_ok(title, "材料", 1))

    def test_other_majors_unaffected(self):
        # 拦截只对材料专业生效，其他专业走原来的合并词表逻辑
        self.assertFalse(J._neighbor_core_blocked("化工工艺工程师", "计算机科学与技术"))
        self.assertFalse(J._neighbor_core_blocked("化工工艺工程师", "机械"))
        self.assertFalse(J._neighbor_core_blocked("软件工程师", "材料"))


if __name__ == "__main__":
    unittest.main()
