# -*- coding: utf-8 -*-
"""行业码的回归：码表、归一、分类、人工覆写、落地优先级。

这些测试存在的理由是：行业码是**建库期一次性产出、匹配期直接查表**的判据。
一旦它是错的，匹配端没有任何机会自己发现——页面上只会显示「这家不对口」，
没人知道是码打错了。所以要钉住四件事：

  1. 码表本身别漂移（唯一码、别名可归一、专业映射的键都合法）；
  2. 关键词分类在几个典型行业上别退化；
  3. 人工确认的码**任何情况下**都不被覆盖（否则核过的结论每跑一次就丢）；
  4. 落地优先级：人工 > 已有模型码 > 本次模型码 > 规则。

测试全部跑在临时库上（patch 掉 ind_code 的三个路径常量），不碰 data/ 下的真库。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ind_code as I


class CodesTableTests(unittest.TestCase):
    def test_codes_are_unique_and_named(self):
        codes = [c[0] for c in I.CODES]
        self.assertEqual(len(codes), len(set(codes)), "行业码必须唯一")
        for c in I.CODES:
            self.assertTrue(c[0], "码不能为空")
            self.assertTrue(c[1], "中文名不能为空：%s" % c[0])

    def test_code_names_covers_all(self):
        for code, _name, _alias, _strong, _weak in I.CODES:
            self.assertEqual(I.CODE_NAMES[code], _name)

    def test_pro_map_is_well_formed(self):
        """准入表：键是专业族，值是 {行业码: core|edge}，两级都不能写错。

        PRO_MAP 是匹配期唯一的判据（查表，不跑关键词），写错一个码名就等于
        那道门永远打不开/永远关着 —— 页面上看不出来，只有学生被静默拦掉。
        """
        for pro, mapping in I.PRO_MAP.items():
            self.assertIsInstance(mapping, dict, "专业族 %s 的值必须是 dict" % pro)
            self.assertTrue(mapping, "专业族 %s 没有任何准入档" % pro)
            for code, level in mapping.items():
                self.assertIn(code, I.CODE_NAMES, "PRO_MAP[%s] 里有不存在的码：%s" % (pro, code))
                self.assertIn(level, ("core", "edge"), "PRO_MAP[%s][%s] 档位非法：%s" % (pro, code, level))

    def test_every_code_is_reachable(self):
        """每个行业码至少要有一个专业族认它。

        否则这类企业对所有学生都是「不放」——要么码表多了一个没人认的行业，
        要么准入表漏了一整行，两种都该在测试里炸出来。
        """
        covered = {code for mapping in I.PRO_MAP.values() for code in mapping}
        missing = set(I.CODE_NAMES) - covered
        self.assertFalse(missing, "这些行业码没有任何专业族认：%s" % sorted(missing))

    def test_normalize(self):
        self.assertEqual(I.normalize("semi"), "semi")
        self.assertEqual(I.normalize("SEMI"), "semi")
        self.assertEqual(I.normalize("半导体与集成电路"), "semi")
        self.assertEqual(I.normalize("半导体与集成电路（semi）"), "semi")
        for bad in ("", None, "不存在的行业", 123):
            self.assertEqual(I.normalize(bad), I.UNKNOWN)


class ClassifyTests(unittest.TestCase):
    def _c(self, **kw):
        return I.classify(**I._item_fields(kw))[0]

    def test_typical_industries(self):
        cases = [
            ({"segment": "集成电路设计与晶圆制造"}, "semi"),
            ({"segment": "家纺面料的研发与生产"}, "textile"),
            ({"segment": "现制茶饮研发与门店运营"}, "food"),
            ({"segment": "商业银行，公司金融与零售金融"}, "finance"),
            ({"segment": "中小学课外辅导与升学培优"}, "edu"),
            ({"segment": "软件开发与信息系统集成"}, "it"),
            ({"segment": "房建与基础设施建设"}, "build"),
        ]
        for kw, want in cases:
            self.assertEqual(self._c(**kw), want, kw)

    def test_unknown_when_no_signal(self):
        self.assertEqual(self._c(segment=""), I.UNKNOWN)
        self.assertEqual(self._c(segment="综合性企业"), I.UNKNOWN)


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.live = os.path.join(self.tmp, "live.db")
        self.enrich = os.path.join(self.tmp, "enrich.db")
        self.over = os.path.join(self.tmp, "override.json")
        con = sqlite3.connect(self.live)
        con.execute("CREATE TABLE company_jobs (name TEXT PRIMARY KEY, "
                    "item_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        con.commit()
        con.close()
        con = sqlite3.connect(self.enrich)
        con.execute("CREATE TABLE companies (name TEXT, industry TEXT, segment TEXT, "
                    "biz_scope TEXT, job_brief TEXT, ind_code TEXT, ind_conf TEXT, ind_why TEXT)")
        con.commit()
        con.close()
        self._patches = [patch.object(I, "LIVE_DB", self.live),
                         patch.object(I, "ENRICH_DB", self.enrich),
                         patch.object(I, "OVERRIDE", self.over)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, name, segment="", **extra):
        item = {"name": name, "industry": extra.pop("industry", ""),
                "biz_scope": segment}
        item.update(extra)
        con = sqlite3.connect(self.live)
        con.execute("INSERT OR REPLACE INTO company_jobs VALUES(?,?,?)",
                    (name, json.dumps(item, ensure_ascii=False), "2026-11-17"))
        con.commit()
        con.close()

    def _get(self, name):
        con = sqlite3.connect(self.live)
        row = con.execute("SELECT item_json FROM company_jobs WHERE name=?", (name,)).fetchone()
        con.close()
        return json.loads(row[0])

    def _write_override(self, items):
        with open(self.over, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False)

    def test_override_beats_model_and_force(self):
        """人工确认的码，模型不许覆盖，force 也不许。"""
        self._put("甲公司", segment="办公家具研发制造", ind_code="design",
                  ind_conf="model")
        self._write_override({"甲公司": {"code": "mech", "why": "人工看过"}})

        I.backfill()                                   # 普通补码
        g = self._get("甲公司")
        self.assertEqual((g["ind_code"], g["ind_conf"]), ("mech", "manual"))

        I.backfill(force=True)                          # 整体重算也不许动
        g = self._get("甲公司")
        self.assertEqual((g["ind_code"], g["ind_conf"]), ("mech", "manual"))

    def test_apply_new_codes_skips_settled(self):
        """已有模型码不来回跳；规则码允许被模型覆盖；人工码谁都动不了。"""
        self._put("乙公司", segment="集成电路设计", ind_code="semi", ind_conf="model")
        self._put("丙公司", segment="集成电路设计", ind_code="semi", ind_conf="high")
        self._put("丁公司", segment="集成电路设计", ind_code="design", ind_conf="model")
        self._write_override({"丁公司": {"code": "semi", "why": "人工"}})

        I.apply_new_codes([
            {"name": "乙公司", "code": "elec", "why": "模型改主意"},
            {"name": "丙公司", "code": "semi", "why": "模型确认"},
            {"name": "丁公司", "code": "elec", "why": "模型乱判"},
        ])
        self.assertEqual(self._get("乙公司")["ind_code"], "semi", "已有模型码不该被覆盖")
        self.assertEqual(self._get("丙公司")["ind_conf"], "model", "规则码应被模型覆盖")
        self.assertEqual(self._get("丁公司")["ind_code"], "design", "人工码不该被动")

    def test_backfill_fills_missing(self):
        self._put("戊公司", segment="家纺面料的研发与生产")
        I.backfill()
        g = self._get("戊公司")
        self.assertEqual(g["ind_code"], "textile")
        self.assertIn(g["ind_conf"], ("high", "medium", "low"))


if __name__ == "__main__":
    unittest.main()
