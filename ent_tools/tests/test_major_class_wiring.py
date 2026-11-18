# -*- coding: utf-8 -*-
"""93 类专业池接线 + 切合层归位（2026-11-18 用户实测后立项）。

两件事各一组测试：

A. 93 表接线（此前一次都没被采集端调用）
   · 专业名能归到 93 个类名（化学→化学类、化工→化工与制药类、机械设计→机械类）；
   · 表里的 L1 能取到，且算作 L1 判据（rank=2）；
   · alias 里写错的类名要能被测出来，不能静默失效。

B. 切合层归位
   · tier0/tier1 只放 L1 及以上：模型给的「材料研发工程师」（化学的 L2）
     必须从这一层消失，而不是「排在最后」；
   · 学生点「放宽一档」进 tier2 时，这条岗要重新出现（不能白丢）。
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import job_recommend as J  # noqa: E402

CATS = {"internships": [], "competitions": [], "papers": [], "skills": []}


class MajorClassTests(unittest.TestCase):
    def test_fine_major_maps_to_class(self):
        cases = {
            "化学": "化学类",
            "化学类": "化学类",
            "化学工程与工艺": "化工与制药类",
            "材料科学与工程": "材料类",
            "机械设计制造及其自动化": "机械类",
            "电气工程及其自动化": "电气类",
            "法学": "法学类",
            "计算机科学与技术": "计算机类",
            "心理学": "心理学类",
            "中药学": "中药学类",
            "生物医学工程": "生物医学工程类",
        }
        for major, cls in cases.items():
            self.assertEqual(J.major_class(major), cls, major)

    def test_alias_targets_exist(self):
        cs = J._class_table()
        if not cs:
            self.skipTest("93 表缺失")
        bad = [(k, v) for k, v in J._CLASS_ALIAS if v not in cs]
        self.assertEqual(bad, [], "alias 指向了不存在的类名，接线会静默失效")

    def test_table_l1_skips_majors_with_own_base(self):
        """方案②：base 齐全的专业不接表（表只会补更细的企业岗）。"""
        self.assertEqual(J.table_l1("化学"), [])
        self.assertEqual(J.table_l1("法学"), [])
        # 也就不用再给这类岗 L1 分
        self.assertNotEqual(J._l1_rank("化学", "寡核苷酸化学合成研发岗"), 2)

    def test_table_l1_feeds_majors_without_base(self):
        """base 缺失的专业类才是表的主场。"""
        titles = [c["title"] for c in J.table_l1("经济学类")]
        if not titles:
            self.skipTest("93 表缺失")
        self.assertIn("投资管理岗", titles)

    def test_generalize_strips_product_line(self):
        """方案①：产品线前缀剥回通用岗名；专业前缀/职能词起头的不动。"""
        self.assertEqual(J._generalize("寡核苷酸化学合成研发岗"), "研发工程师")
        self.assertEqual(J._generalize("多肽化学合成研发岗"), "研发工程师")
        self.assertEqual(J._generalize("电池材料研发工程师"), "研发工程师")
        self.assertEqual(J._generalize("机械设计工程师"), "机械设计工程师")
        self.assertEqual(J._generalize("数据分析师"), "数据分析师")

    def test_unknown_major_returns_none(self):
        self.assertIsNone(J.major_class(""))
        self.assertIsNone(J.major_class("玄学"))

    def test_table_trust_only_accepts_campus_notices(self):
        self.assertTrue(J._table_trust(
            "广州虹科电子2027年校园招聘（西安交通大学就业信息网）设法务专员岗"))
        self.assertTrue(J._table_trust(
            "国浩律师（武汉）事务所宣讲会明确招聘律师助理"))
        # 媒体文章里的一次性新岗
        self.assertFalse(J._table_trust(
            "光明日报文章提到AI企业新设「人机交互伦理顾问」等面向文科生的岗位"))
        self.assertFalse(J._table_trust("C114报道字节校招设「大模型法律垂搜数据运营」"))
        # 代码兜底文案，没有证据
        self.assertFalse(J._table_trust("专业「法学类」的主流入口，与你上面的经历同方向"))

    def test_table_l1_drops_offtopic_legal_jobs(self):
        # 法学 base 齐全 → 不接表，脏条目自然进不来（证据闸门仍有单测覆盖）
        self.assertEqual(J.table_l1("法学"), [])
        self.assertFalse(J._table_trust(
            "C114报道字节校招设「大模型法律垂搜数据运营」"))


class CutLayerDemoteTests(unittest.TestCase):
    """用临时池文件 + 假模型，避免碰真实缓存、不联网。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_pool = J.POOL_FILE
        self._old_llm = J.llm_pool
        J.POOL_FILE = os.path.join(self.tmp, "pool.json")

        def fake(major, cats, tier, accept, key, base_url, model, shown):
            if tier >= 2:          # 放宽层不喂模型，走规则池，方便断言归位效果
                return None
            return ([{"title": "材料研发工程师", "why": "x"},
                     {"title": "研发工程师", "why": "x"}], "web")

        J.llm_pool = fake

    def tearDown(self):
        J.POOL_FILE = self._old_pool
        J.llm_pool = self._old_llm

    def _cfg(self):
        return {"DEEPSEEK_API_KEY": "test-key"}

    def test_cut_layer_drops_neighbor_job(self):
        items, mode = J.get_pool("化学", CATS, 0, False, self._cfg())
        titles = [c["title"] for c in items]
        self.assertEqual(mode, "llm-web")
        self.assertNotIn("材料研发工程师", titles)
        self.assertIn("研发工程师", titles)

    def test_demoted_job_comes_back_in_tier2(self):
        J.get_pool("化学", CATS, 0, False, self._cfg())
        items, _ = J.get_pool("化学", CATS, 2, False, self._cfg())
        self.assertEqual(items[0]["title"], "材料研发工程师",
                         "切合层剔下来的邻专业岗，放宽一档时要能重新出现")

    def test_fill_does_not_inject_product_line_jobs(self):
        items, _ = J.get_pool("化学", CATS, 0, False, self._cfg())
        titles = [c["title"] for c in items]
        # 化学 base 齐全，表不接；更细的企业岗不能从补位缝里进来
        self.assertNotIn("寡核苷酸化学合成研发岗", titles)
        self.assertGreaterEqual(len(titles), 6)


class PoolFileShapeTests(unittest.TestCase):
    def test_no_stray_demote_bucket_in_real_file(self):
        """真实池文件里只允许出现正规池与 __demote__ 桶，别写出别的键。"""
        try:
            d = json.load(open(J.POOL_FILE, encoding="utf-8"))
        except Exception:
            self.skipTest("池文件不存在")
        for k, v in d.items():
            if k.startswith("__demote__"):
                self.assertIsInstance(v.get("items"), list)
            else:
                self.assertIn("items", v)


if __name__ == "__main__":
    unittest.main()
