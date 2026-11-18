# -*- coding: utf-8 -*-
"""权威对口岗表守卫：防「专业 L1 被大类泛岗盖掉」回归。

2026-11-18 用户实测公共卫生 L1 是「研发工程师/实验技术员/注册专员/医药代表」
一堆泛医药岗，最对口的公卫医师/流调一个没有。用户要求「全部搞完，不能
一个个找」——本测试锁住：
  1. 用户实测过的重灾区专业（公共卫生/法学/建筑/核工程/艺术…）L1 必含对口岗。
  2. 名单里的权威表专业 L1 不得整段等于所属大类泛岗表。
  3. 真经管（会计/营销）不被权威表误伤，仍以泛管理岗为 L1。
  4. 单向匹配：输入「生物」不得被权威表的「生物医学」词根误配成医疗器械岗。
"""
import json
import os
import unittest

import job_recommend as J
import major_l1_authority as AUTH

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CATALOG = os.path.join(_HERE, "data", "major_catalog_2025.json")

# 用户实测 + 全量诊断暴露的重灾区：专业 -> 必须出现在 L1 的对口岗
MUST_HAVE = {
    "公共卫生": ["公卫医师", "流行病学调查员"],
    "公共卫生与预防医学类": ["公卫医师", "疾病预防控制专员"],
    "基础医学": ["基础医学研究员"],
    "医学技术": ["医学检验技师", "医学影像技师"],
    "法学": ["法务专员", "律师助理"],
    "中国语言文学": ["语文教师"],
    "中文": ["语文教师"],
    "心理学": ["心理咨询助理"],
    "地理科学": ["GIS工程师"],
    "大气科学": ["气象预报员"],
    "建筑学": ["建筑设计助理"],
    "建筑": ["建筑设计助理"],
    "水利": ["水利工程师"],
    "测绘": ["测绘工程师"],
    "核工程": ["核工程师", "辐射防护工程师"],
    "公安技术": ["警务技术员"],
    "动物医学": ["宠物医生", "兽医"],
    "音乐与舞蹈学": ["音乐教师", "舞蹈教师"],
    "设计学": ["平面设计师"],
}


def all_93_classes():
    cat = json.load(open(_CATALOG, encoding="utf-8"))
    out = []
    for cs in cat["disciplines"].values():
        for c in cs:
            out.append(c)
            out.append(c.replace("类", ""))
    return out


class TestMajorL1Authority(unittest.TestCase):
    def test_reported_majors_have_own_jobs_in_l1(self):
        for major, must in MUST_HAVE.items():
            l1 = J.core_jobs(major)
            for title in must:
                with self.subTest(major=major, title=title):
                    self.assertIn(title, l1, "%s 的 L1 缺对口岗 %s" % (major, title))

    def test_no_pure_sales_admin_fallback_across_93(self):
        # 93 专业类无一掉进「销售/行政」兜底
        unknown = set(J.CATEGORY_JOBS_MAP.get(J.UNKNOWN_CAT, []))
        for major in all_93_classes():
            l1 = J.core_jobs(major)
            with self.subTest(major=major):
                self.assertTrue(l1, "%s 的 L1 为空" % major)
                self.assertFalse(
                    all(j in unknown for j in l1),
                    "%s 的 L1 是销售/行政兜底（大类识别或对口岗缺失）" % major)

    def test_humanities_med_not_pure_generic(self):
        # 人文社科 + 医学大类的专业，L1 不得整段等于大类泛岗表
        for cat_name in ("人文社科大类", "理学 / 医学大类"):
            generic = set(J.CATEGORY_JOBS_MAP.get(cat_name, []))
            for key, _jobs in AUTH.AUTHORITY.items():
                if J.major_category(key) != cat_name:
                    continue
                l1 = J.core_jobs(key)
                with self.subTest(major=key, cat=cat_name):
                    self.assertFalse(
                        l1 and all(j in generic for j in l1),
                        "%s 的 L1 整段是大类泛岗表" % key)

    def test_real_biz_majors_not_hijacked(self):
        # 真经管不被权威表误伤，仍以泛管理岗为 L1
        for major in ("会计学", "市场营销", "工商管理"):
            l1 = J.core_jobs(major)
            with self.subTest(major=major):
                self.assertIn("管培生", l1)
                self.assertIn("商业分析岗", l1)

    def test_one_way_match_no_false_hit(self):
        # 输入「生物」是 MAJOR_BASE 既有专业，不得被权威表「生物医学」词根劫走
        l1 = J.core_jobs("生物")
        with self.subTest(major="生物"):
            self.assertIn("生物研发工程师", l1)
            self.assertNotIn("医疗器械研发工程师", l1)
        # 输入「机械设计制造及其自动化」不得被艺术表「设计」词根劫走成平面设计师
        # （2026-11-18 虚拟用户 E2E 抓到的真实 bug）
        mech = J.core_jobs("机械设计制造及其自动化")
        with self.subTest(major="机械设计制造及其自动化"):
            self.assertIn("机械设计工程师", mech)
            self.assertNotIn("平面设计师", mech)
            self.assertNotIn("UI设计师", mech)

    def test_design_roots_not_overbroad(self):
        # 真设计类仍能被权威表命中（用具体词根，不是泛化的「设计」）
        for major, must in (("工业设计", "工业设计师"),
                            ("设计学", "平面设计师"),
                            ("艺术设计", "UI设计师")):
            with self.subTest(major=major):
                self.assertIn(must, J.core_jobs(major))

    def test_public_health_generic_demoted_not_lost(self):
        # 公共卫生的泛医药岗降级到 L2，不丢失
        l2 = J.near_jobs("公共卫生")
        for title in ("研发工程师", "注册专员", "医药代表", "临床研究助理"):
            with self.subTest(title=title):
                self.assertIn(title, l2)


if __name__ == "__main__":
    unittest.main()
