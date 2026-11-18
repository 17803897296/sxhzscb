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
        # 拦截只对列出的专业生效，其他专业走原来的合并词表逻辑
        self.assertFalse(J._neighbor_core_blocked("化工工艺工程师", "计算机科学与技术"))
        self.assertFalse(J._neighbor_core_blocked("化工工艺工程师", "机械"))
        self.assertFalse(J._neighbor_core_blocked("软件工程师", "材料"))

    # ── 2026-11-18 全量大类审计发现的三处同构漏网 ──────────────────────

    def test_metallurgy_blocks_chemical_core(self):
        # 冶金和材料同用化工池，同样不能拿化工设备/中试/催化核心岗
        for title in ("化工设备工程师", "中试/放大工程师", "催化研发工程师"):
            with self.subTest(title=title):
                self.assertTrue(J._neighbor_core_blocked(title, "冶金工程"))
                self.assertFalse(J._major_ok(title, "冶金工程", 2))
                self.assertNotIn(title, J.near_jobs("冶金工程"))

    def test_weak_electric_blocks_strong_electric_core(self):
        # 通信 / 微电子（弱电）不得拿电机设计 / 配电设计 / 电梯暖通（强电核心岗）
        for major in ("通信工程", "微电子科学与工程", "电子信息工程"):
            for title in ("电机设计工程师", "配电设计工程师", "电梯/暖通电气工程师"):
                with self.subTest(major=major, title=title):
                    self.assertTrue(J._neighbor_core_blocked(title, major))
                    self.assertNotIn(title, J.near_jobs(major))

    def test_electric_major_keeps_strong_electric_core(self):
        # 电气专业自己就是强电，这些岗必须保留
        for title in ("电机设计工程师", "配电设计工程师", "电气设计工程师"):
            with self.subTest(title=title):
                self.assertFalse(J._neighbor_core_blocked(
                    title, "电气工程及其自动化"))
                self.assertIn(title, J.near_jobs("电气工程及其自动化"))

    def test_animal_husbandry_blocks_planting_core(self):
        # 动物医学 / 动物科学（养殖侧）不得拿农艺师 / 育种 / 植保（种植核心岗）
        for major in ("动物医学", "动物科学", "畜牧学", "水产养殖学"):
            for title in ("农艺师", "育种技术员", "植保技术员", "测土配方师"):
                with self.subTest(major=major, title=title):
                    self.assertTrue(J._neighbor_core_blocked(title, major))
                    self.assertNotIn(title, J.core_jobs(major))

    def test_plant_major_keeps_own_core(self):
        # 农学 / 植物保护自己的核心岗必须保留
        for major in ("农学", "植物保护"):
            for title in ("农艺师", "育种技术员", "植保技术员"):
                with self.subTest(major=major, title=title):
                    self.assertFalse(J._neighbor_core_blocked(title, major))
                    self.assertIn(title, J.core_jobs(major))

    def test_plant_side_blocks_animal_core(self):
        # 对称：种植侧（农学/园艺/植保）不得拿兽医 / 饲料 / 养殖核心岗
        for major in ("农学", "园艺", "植物保护"):
            for title in ("兽医", "兽医助理", "饲料配方师", "养殖技术员"):
                with self.subTest(major=major, title=title):
                    self.assertTrue(J._neighbor_core_blocked(title, major))
        # 动物医学自己不受这条影响
        self.assertFalse(J._neighbor_core_blocked("兽医助理", "动物医学"))

    def test_legit_neighbor_jobs_kept(self):
        # 本专业能投的相邻岗不受影响：化学的配方/实验室、通信的仪表/调试、
        # 动物医学的养殖/饲料/兽医助理
        for major, titles in (
            ("冶金工程", ("配方工程师", "实验室技术员")),
            ("通信工程", ("仪表工程师", "调试工程师")),
            ("动物医学", ("养殖技术员", "饲料配方师", "兽医助理")),
        ):
            for title in titles:
                with self.subTest(major=major, title=title):
                    self.assertFalse(J._neighbor_core_blocked(title, major))


if __name__ == "__main__":
    unittest.main()
