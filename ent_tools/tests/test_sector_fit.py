# -*- coding: utf-8 -*-
"""医药细分产业带：生物药 / 化学药 / 中药 / 器械诊断 / 流通 / 非医药。

2026-11-18 用户实测生物专业看到的榜单：
  科伦（大输液）· 久安（注射剂）· 普洛（原料药）排在前三，
  做疫苗 / 细胞基因治疗的中科开浦挤到第 3，多恩（纺织印染助剂）、
  功成（卫生杀虫剂）凭名字里的「生物」两个字也在榜上。
用户口径：「药企和化学明显对胃，生物的话应该是生物制药更对胃吧」。

锁住四件事：
1. 分带只看**主营描述**，企业名不算证据（多恩 / 功成就是名字骗人）；
2. 只对「医药」行业码生效，别的行业一律返回 None（不越权重判行业）；
3. 生物类专业的档位是 生物药 0 < 器械诊断 1 < 化学药/中药/流通 2 < 非医药 3；
   化学 / 药学不登记档位 —— 它们的榜单本来就是化学药打头，不动；
4. 前端 report.js 两处重排都带 sector.rank（页面会自己再排一遍）。
"""
import os
import re
import unittest

import fair_bridge as F
import sector_fit as S

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_JS = os.path.join(os.path.dirname(HERE), "js", "report.js")


class TestClassify(unittest.TestCase):
    """主营描述 → 产业带。用例都取自 145 家真实库里那 19 家「医药」。"""

    def _band(self, seg, scope="", industry="医药"):
        return S.classify(industry, seg, scope)["band"]

    def test_大输液与仿制药_化学药(self):
        self.assertEqual(self._band(
            "大输液（输液）产品的研发、生产和销售；并布局抗生素、仿制药与创新药（ADC研发平台）",
            "大容量注射剂、抗生素中间体及仿制药的研发生产与销售"), S.BAND_CHEMICAL)

    def test_原料药与中间体_化学药(self):
        self.assertEqual(self._band(
            "原料药(API)、CDMO、制剂(FDF)三大业务；产品聚焦心脑血管、抗感染",
            "致力于成为全球科技型医药制造服务领军企业"), S.BAND_CHEMICAL)

    def test_仿制药与生物药并列_按主营开头的仿制药(self):
        """华海两种都做：主营开头写的是仿制药，就按化学药排——生物药只是它的一个事业部。"""
        self.assertEqual(self._band(
            "多剂型的仿制药、生物药、创新药及特色原料药的研发、生产和销售"), S.BAND_CHEMICAL)

    def test_疫苗与细胞基因治疗_生物药(self):
        self.assertEqual(self._band(
            "聚焦新发突发传染病防控，提供抗病毒/抗细菌等传染性病原疫苗与药物及细胞基因治疗"),
            S.BAND_BIOLOGIC)

    def test_检测试剂_器械诊断(self):
        """上成主营开头写的是「食品安全」，但它真正在卖的是检测试剂。"""
        self.assertEqual(self._band(
            "食品安全快速检测技术及相关产品的研发、生产、销售，涵盖农药残留、兽药残留",
            "主营食品安全快速检测试剂与仪器（胶体金试纸条、ELISA试剂盒）"), S.BAND_DEVICE)

    def test_体外诊断_器械诊断(self):
        self.assertEqual(self._band("集医疗器械研发、生产、营销与服务于一体的健康管理综合解决方案提供商；以临床IVD检测"),
                         S.BAND_DEVICE)

    def test_中药配方颗粒_中药(self):
        self.assertEqual(self._band(
            "“999感冒灵”等OTC及处方药的全", "主营OTC和中药处方药，覆盖感冒、胃肠及中药配方颗粒"), S.BAND_TCM)

    def test_连锁药房_医药流通(self):
        """益丰主营里也写「医疗器械」，但主业是药品零售。"""
        self.assertEqual(self._band(
            "药品零售连锁（直营+加盟门店），经营处方药、OTC、保健品、医疗器械等健康商品"),
            S.BAND_CHANNEL)

    def test_名字带生物但做纺织助剂_非医药(self):
        """多恩生物科技：名字里的「生物」是工商名，主营是印染助剂。"""
        self.assertEqual(self._band(
            "研发、生产、销售中高端纺织化学品（印染助剂），产品覆盖纺丝、织造、染整前处理",
            "专注纺织印染行业的中高端印染助剂及整体解决方案高新技术企业"), S.BAND_OTHER)

    def test_名字带生物但做杀虫剂_非医药(self):
        """江苏功成：有害生物防治 = 杀虫剂，不是医药。"""
        self.assertEqual(self._band(
            "专业从事有害生物防治产品研发、生产及销售，产品覆盖卫生杀虫剂、白蚁防治产品",
            "国家定点农药原药及制剂生产企业，主营卫生杀虫剂、白蚁/红火蚁防治"), S.BAND_OTHER)

    def test_非医药行业不参与(self):
        """行业码不是医药就不判：不越权重判行业（否则食品厂、化工厂全被拖进来）。"""
        for ind in ("制造", "建筑", "IT", "教育", ""):
            with self.subTest(industry=ind):
                self.assertIsNone(S.classify(ind, "疫苗与细胞治疗", "")["band"])

    def test_主营没采到_未细分(self):
        self.assertEqual(self._band("", ""), S.BAND_UNKNOWN)


class TestPreference(unittest.TestCase):
    """专业 → 档位。只有生物类登记了档位。"""

    def test_生物类专业(self):
        for m in ("生物", "生物技术", "生物工程", "生物科学", "微生物学", "生物制药"):
            with self.subTest(major=m):
                self.assertEqual(S.family_of(m), "生物药方向")

    def test_生物医学工程先于生物(self):
        """「生物医学工程」含「生物」，但方向是器械；顺序写反就会被判成生物药。"""
        self.assertEqual(S.family_of("生物医学工程"), "器械方向")
        self.assertEqual(S.annotate("生物医学工程", {"band": S.BAND_DEVICE})["rank"], 0)

    def test_化学与药学不分带(self):
        """化学 / 药学不登记档位：现有榜单本来就是化学药打头，不动。"""
        for m in ("化学", "应用化学", "化学工程与工艺", "药学", "会计学", ""):
            with self.subTest(major=m):
                self.assertIsNone(S.family_of(m))
                self.assertIsNone(
                    S.annotate(m, {"band": S.BAND_CHEMICAL})["rank"])

    def test_生物专业档位(self):
        want = {S.BAND_BIOLOGIC: 0, S.BAND_DEVICE: 1, S.BAND_CHEMICAL: 2,
                S.BAND_TCM: 2, S.BAND_CHANNEL: 2, S.BAND_OTHER: 3}
        for band, rank in want.items():
            with self.subTest(band=band):
                self.assertEqual(S.annotate("生物", {"band": band})["rank"], rank)

    def test_理由能念出来(self):
        a = S.annotate("生物", S.classify("医药", "原料药、中间体的研发生产"))
        self.assertEqual(a["band"], S.BAND_CHEMICAL)
        self.assertIn("隔一层", a["why"])
        self.assertIn("原料药", a["why"])


def _row(name, score=70.0, state="hit", sector_rank=None, band=None):
    r = {"name": name, "score": score, "state": state, "booth": "001",
         "matched_jobs": ["生物研发工程师"], "biz_recheck": None,
         "feedback": None, "fit": "ask", "biz_fit": None,
         "ask_jobs": [], "infer_matches_backend": []}
    if sector_rank is not None:
        r["sector"] = {"band": band or "", "label": band or "",
                       "rank": sector_rank, "why": band or ""}
    return r


class TestSortKey(unittest.TestCase):
    """排序键：细分带在同层内先聚在一起，带内才比分数。"""

    def test_生物药压过同分的化学药(self):
        bio = _row("中科开浦", score=73.5, sector_rank=0, band=S.BAND_BIOLOGIC)
        chem = _row("科伦药业", score=73.5, sector_rank=2, band=S.BAND_CHEMICAL)
        self.assertEqual(sorted([chem, bio], key=F.rank_sort_key)[0]["name"], "中科开浦")

    def test_细分能压过分数(self):
        """73.5 分的化学药排在 63.6 分的器械诊断之后——这就是用户要的「更对胃」。"""
        chem = _row("科伦药业", score=73.5, sector_rank=2, band=S.BAND_CHEMICAL)
        dev = _row("诺尔曼", score=63.6, state="inferred", sector_rank=1,
                   band=S.BAND_DEVICE)
        self.assertEqual(sorted([chem, dev], key=F.rank_sort_key)[0]["name"], "诺尔曼")

    def test_不分带的专业不受影响(self):
        """没有 sector 的行（化学 / 会计 / 材料…）全是 0，规则与改动前一致。"""
        hi = _row("A", score=80.0)
        lo = _row("B", score=50.0)
        self.assertEqual(sorted([lo, hi], key=F.rank_sort_key)[0]["name"], "A")
        self.assertEqual(F.sector_rank(hi), 0)

    def test_带内仍按分数(self):
        a = _row("A", score=70.0, sector_rank=2, band=S.BAND_CHEMICAL)
        b = _row("B", score=80.0, sector_rank=2, band=S.BAND_CHEMICAL)
        self.assertEqual(sorted([a, b], key=F.rank_sort_key)[0]["name"], "B")


class TestFrontendSameRule(unittest.TestCase):
    """页面会自己再排一遍：两处重排都必须带 sector.rank。"""

    def test_两处排序都带细分档(self):
        js = open(REPORT_JS, encoding="utf-8").read()
        self.assertGreaterEqual(len(re.findall(r"a\.sector&&a\.sector\.rank", js)), 2,
                                "report.js 的默认队列序与「只看命中」序都要带 sector.rank")

    def test_名次理由带细分(self):
        js = open(REPORT_JS, encoding="utf-8").read()
        self.assertIn("r.rank_reason+='；细分 '", js,
                      "前端名次理由要与后端同口径写出细分结论")


if __name__ == "__main__":
    unittest.main()
