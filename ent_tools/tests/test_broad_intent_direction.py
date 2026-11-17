# -*- coding: utf-8 -*-
"""泛意向方向对齐（2026-11-16 用户拍板「宽泛选择的岗位也要附和方向」）。

背景：用户手测报「研发工程师 ↔ C++研发工程师 / 自动化工程师 / 电控硬件工程师」、
「人工智能算法工程师 ↔ 运动控制算法工程师」命中全有问题。根因与修法见
docs/修_泛意向方向对齐.md：
  R1  泛研发意向 × 带方向岗位 → ask（现场问），不再 ok 硬命中；
  R2  学生方向域（其它意向推的）与岗位方向相容 → 升回命中；
  R3  共享动作核（算法）+ 不同领域限定词 → no（并列领域）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import intent_semantics as S  # noqa: E402


class BroadIntentFlood(unittest.TestCase):
    """R1：泛研发意向不再无条件吃下带方向的岗位。"""

    def test_user_reported_pairs_no_longer_hit(self):
        for title, intent in [("C++研发工程师", "研发工程师"),
                              ("自动化工程师", "研发工程师"),
                              ("电控硬件工程师", "研发工程师"),
                              ("嵌入式软件工程师", "研发工程师"),
                              ("后端开发工程师", "研发工程师"),
                              ("前端开发工程师", "研发工程师"),
                              ("药品研发工程师", "研发工程师")]:
            self.assertEqual(S.pair_verdict(title, intent), "ask",
                             "%s × %s 应转现场问" % (title, intent))

    def test_broad_intent_still_hits_directionless_titles(self):
        # 同名互配 → 照旧 ok
        self.assertEqual(S.pair_verdict("研发工程师", "研发工程师"), "ok")
        # 2026-11-16 二次点名后口径收紧：带「软件」的是编程条线岗不是「同样泛」，
        # 泛研发/泛开发意向撞它们转现场问（见 SoftwareSoftlineGate）
        self.assertEqual(S.pair_verdict("软件研发工程师", "研发工程师"), "ask")
        self.assertEqual(S.pair_verdict("软件开发工程师", "开发工程师"), "ask")

    def test_cross_function_stays_no(self):
        # 泛意向明确跨职能仍判 no，不被 R1 软化成 ask
        self.assertEqual(S.pair_verdict("财务专员", "研发工程师"), "no")
        self.assertEqual(S.pair_verdict("UI设计师", "研发工程师"), "no")

    def test_specific_intent_untouched(self):
        # 意向本身带方向的原逻辑一概不动；「岗位更细」自 2026-11-17 起算对口
        self.assertEqual(S.pair_verdict("AI算法研究工程师", "算法工程师"), "ok")
        self.assertEqual(S.pair_verdict("算法工程师", "人工智能算法工程师"), "ok")
        self.assertEqual(S.pair_verdict("供应链管培生", "管培生"), "ok")


class AlgorithmDomainQualifiers(unittest.TestCase):
    """R3：共享「算法」动作核但领域限定不同 → 并列领域，no。"""

    def test_user_reported_pair(self):
        self.assertEqual(S.pair_verdict("运动控制算法工程师", "人工智能算法工程师"), "no")

    def test_parallel_algorithm_domains(self):
        self.assertEqual(S.pair_verdict("推荐算法工程师", "图像算法工程师"), "no")

    def test_ai_synonym_not_split(self):
        # ai 与人工智能是同一领域的两种写法，归一后不判 no
        self.assertNotEqual(S.pair_verdict("人工智能算法工程师", "ai算法工程师"), "no")

    def test_one_sided_qualifier_untouched(self):
        # 一侧无限定词（算法工程师）是「大类进小类」，不触发 R3
        v = S.pair_verdict("运动控制算法工程师", "算法工程师")
        self.assertEqual(v, "ok")


class UserDirectionRescue(unittest.TestCase):
    """R2：泛意向附和学生自己的方向域。"""

    MECH = ["机械设计工程师", "研发工程师"]
    CS = ["算法工程师", "研发工程师"]

    def test_mech_student_matches_mech_rd(self):
        sds = S._intent_dirs("研发工程师", self.MECH)
        self.assertTrue(S._broad_rd_follows_user(
            "机械研发工程师", "研发工程师", sds))

    def test_unknown_title_dir_stays_ask(self):
        # C++研发工程师方向认不出 → 不升，保持现场问
        sds = S._intent_dirs("研发工程师", self.MECH)
        self.assertFalse(S._broad_rd_follows_user(
            "C++研发工程师", "研发工程师", sds))

    def test_empty_user_direction_stays_ask(self):
        # 学生没勾任何带方向的意向 → 不替学生做主
        sds = S._intent_dirs("研发工程师", ["研发工程师"])
        self.assertEqual(sds, set())
        self.assertFalse(S._broad_rd_follows_user(
            "机械研发工程师", "研发工程师", sds))

    def test_specific_intent_never_uses_rescue(self):
        # R2 只服务泛研发意向；带方向的意向走原 same_dir 判据
        sds = S._intent_dirs("机械设计工程师", self.MECH)
        self.assertFalse(S._broad_rd_follows_user(
            "机械设计工程师", "机械设计工程师", sds))


class RotationTitlesNeverHardHit(unittest.TestCase):
    """轮岗泛岗（管培生 / 培训生 / 储备干部）一律不硬命中 —— 2026-11-16 用户拍板。

    背景：用户手测报科力普「管理培训生 ↔ 物流专员」被收进语义命中，理由写成
    「同类工作内容」。根因：大类闸被 biz_ok 短路——公司业务含采购/物流时，
    bare-generic 分支直接把轮岗泛岗当成对口证据。轮岗岗先轮岗后定岗，
    岗位名本身没有「招进来就是干这个」的证据；业务对口也只该升级问句。
    """

    KLP_BIZ = ("科力普科技集团股份有限公司 消费 为企业客户提供办公一站式、"
               "MRO工业品、营销礼品及员工福利的全场景数智化采购服务")
    DJS_BIZ = "东方嘉盛供应链股份有限公司 供应链管理 供应链服务、仓储物流"

    def test_biz_ok_does_not_rescue_rotation_title(self):
        # 业务对口（科力普含采购物流）也不收进 kept
        kept, dropped, _, asks = S.align_jobs_detail(
            ["物流专员"], ["管理培训生"], biz=self.KLP_BIZ)
        self.assertEqual(kept, [])
        self.assertIn("管理培训生", dropped)
        self.assertEqual([a["job"] for a in asks], ["管理培训生"])

    def test_hot_ask_upgrades_why_text(self):
        # 业务对口时问句升级为「主业对口，大概率往这个方向培养」
        _, _, _, asks = S.align_jobs_detail(
            ["物流专员"], ["管理培训生"], biz=self.KLP_BIZ)
        self.assertIn("大概率", asks[0]["why"])
        aj = S.ask_jobs(["物流专员"], ["管理培训生"], self.KLP_BIZ)
        self.assertTrue(aj and "大概率" in aj[0]["why"])

    def test_directional_trainee_still_hits(self):
        # 带方向的管培生（物流管培生 ↔ 物流专员）照常命中，不误伤
        kept, _, _, _ = S.align_jobs_detail(
            ["物流专员"], ["物流管培生"], biz=self.KLP_BIZ)
        self.assertEqual(kept, [("物流管培生", "物流专员")])

    def test_bare_generic_nonrotation_biz_ok_still_counts(self):
        # 非 rotational 的大类定向岗（业务对口）保持原 biz_ok 口径
        kept, _, _, _ = S.align_jobs_detail(
            ["物流专员"], ["专员"], biz=self.DJS_BIZ)
        self.assertEqual(kept, [("专员", "物流专员")])

    def test_biz_unrelated_rotation_stays_quiet_ask(self):
        # 业务毫不相关时问句用通用文案，不带「大概率」
        _, _, _, asks = S.align_jobs_detail(
            ["物流专员"], ["管理培训生"],
            biz="某某新材料科技有限公司 新材料研发生产")
        self.assertTrue(asks and "大概率" not in asks[0]["why"])


class SoftwareSoftlineGate(unittest.TestCase):
    """软件条线补闸：泛研发意向 × 编程条线岗 → ask（2026-11-16 二次点名）。

    「软件工程师 ↔ 研发工程师」漏网：「软件」在 DIR_NEUTRAL 里、方向核为空，
    ⓪ 闸的 `and b` 拦不住。补 DEV_SOFTLINE 词表闸；学生自己勾「软件工程师」
    的同条线互配不误伤。
    """

    def test_user_reported_pairs_now_ask(self):
        for title in ("软件工程师", "软件开发工程师", "软件研发工程师"):
            self.assertEqual(S.pair_verdict(title, "研发工程师"), "ask",
                             "%s × 研发工程师 应转现场问" % title)

    def test_software_line_mutual_match_untouched(self):
        # 学生勾的就是软件工程师：同条线互配照常命中
        self.assertEqual(S.pair_verdict("软件开发工程师", "软件工程师"), "ok")
        self.assertEqual(S.pair_verdict("软件工程师", "软件工程师"), "ok")

    def test_non_software_titles_untouched(self):
        # 「系统工程师」等不含软件条线词的泛岗维持原口径；前端岗已由方向核闸拦
        self.assertEqual(S.pair_verdict("系统工程师", "研发工程师"), "ok")
        self.assertEqual(S.pair_verdict("前端开发工程师", "软件工程师"), "ask")

    def test_software_sales_still_no(self):
        # 软件条线词不软化 NON_RD 族判定
        self.assertEqual(S.pair_verdict("软件销售", "研发工程师"), "no")

    def test_alignment_no_software_hits_for_pure_rd_intent(self):
        # 只勾研发工程师的学生：软件岗全部转现场问
        kept, _, _, asks = S.align_jobs_detail(
            ["研发工程师"],
            ["软件工程师", "软件开发工程师", "C++研发工程师", "机械研发工程师"])
        self.assertEqual(kept, [])
        self.assertEqual({a["job"] for a in asks},
                         {"软件工程师", "C++研发工程师", "机械研发工程师"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
