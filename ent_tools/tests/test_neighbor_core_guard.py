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


class TestCoreJobsUsesMajorBase(unittest.TestCase):
    """L1 切合层必须优先用 MAJOR_BASE 的专业专属岗。

    2026-11-18 用户（工业工程本科）实测：工业工程 L1 全是「管培生/运营/
    商业分析」泛管理岗，最对口的「工业工程师/质量工程师/精益改善」一个没有。
    根因 core_jobs 只读大类表，从不读 MAJOR_BASE。
    """

    def test_all_registered_majors_keep_their_base_jobs(self):
        # 所有登记专业的专属岗都必须出现在 L1（这是「切合」的本意）
        for major, base in J.MAJOR_BASE.items():
            core = J.core_jobs(major)
            for title in base:
                with self.subTest(major=major, title=title):
                    self.assertIn(title, core)

    def test_major_specific_jobs_come_first(self):
        # 专属岗排在泛大类岗之前——用户一眼看到的就是最对口的
        for major, base in J.MAJOR_BASE.items():
            core = J.core_jobs(major)
            positions = [core.index(t) for t in base if t in core]
            if positions:
                with self.subTest(major=major):
                    self.assertEqual(min(positions), 0)

    def test_industrial_engineering_not_flooded_by_generic(self):
        # 工业工程专属岗在前 5，泛管理岗不许把对口岗挤出 L1
        core = J.core_jobs("工业工程")
        for title in ("工业工程师", "质量工程师", "精益改善岗", "生产计划岗"):
            with self.subTest(title=title):
                self.assertLess(core.index(title), 5)

    def test_guard_still_blocks_after_base_merge(self):
        # 合入专属岗后，邻专业核心岗闸门仍有效（材料不拿回化工岗）
        for title in ("化工工艺工程师", "化工设备工程师", "中试/放大工程师"):
            with self.subTest(title=title):
                self.assertNotIn(title, J.core_jobs("材料"))

    def test_generic_mgmt_demoted_out_of_l1_for_specialized_majors(self):
        # 用户拍板：泛管理岗工业工程能干，但不是 L1「切合」的定义，最低 L2。
        # 有专属对口岗的经管专业，L1 必须干净（只剩专业对口岗）。
        generic = ("管培生", "职能管培生", "储备干部", "运营培训生",
                   "业务运营岗", "商业分析岗", "数据分析岗")
        for major in ("工业工程", "运营与供应链管理"):
            core = J.core_jobs(major)
            for title in generic:
                with self.subTest(major=major, title=title):
                    self.assertNotIn(title, core)

    def test_demoted_generic_mgmt_kept_in_l2(self):
        # 降级不等于丢弃：泛管理岗必须能在 L2 找到
        l2 = J.near_jobs("工业工程")
        for title in ("管培生", "商业分析岗", "数据分析岗", "运营培训生"):
            with self.subTest(title=title):
                self.assertIn(title, l2)

    def test_real_biz_majors_keep_generic_as_l1(self):
        # 没有明确职业出口的经管专业（工商管理），L1 就是泛管理岗，
        # 不能被「专属岗前置」的降级逻辑误伤。
        # 会计学/市场营销 2026-11-18 起登记了专属基线岗（财务会计岗、市场专员…），
        # 按同一条规则，它们的泛管理岗应该降到 L2 —— 见 test_biz_majors_with_exit_*。
        for major in ("工商管理",):
            core = J.core_jobs(major)
            with self.subTest(major=major):
                self.assertIn("管培生", core)
                self.assertIn("商业分析岗", core)

    def test_biz_majors_with_exit_put_own_jobs_in_l1(self):
        # 会计学/市场营销有职业出口，L1 必须是本职岗，泛管理岗降 L2。
        for major, own in (("会计学", "财务会计岗"), ("市场营销", "市场专员")):
            core = J.core_jobs(major)
            with self.subTest(major=major):
                self.assertIn(own, core)
                self.assertNotIn("管培生", core)
                self.assertIn("管培生", J.near_jobs(major) or [])


if __name__ == "__main__":
    unittest.main()
