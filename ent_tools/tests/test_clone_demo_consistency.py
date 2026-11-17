# -*- coding: utf-8 -*-
"""克隆演示一致性守卫（复审 2026-11-18 P0 的回归网）。

两个历史翻车点，CI 必须拦住：
1. 提交的 fair_payload.json 是用生效画像 A 构建的，种子库 fair_active 却还
   指着画像 B —— clone 第一次 GET /api/fair/payload 就触发重建，演示结果
   当场变脸（42 行掉成 35 行、31 条语义映射归零）。
2. *.json.seed 缓存种子与当前代码指纹不符 —— clone 启动恢复种子后立刻被
   cache_guard 判废归档，语义/复核全退回字面匹配。
"""
import json
import os
import sqlite3
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")


class TestCloneDemoConsistency(unittest.TestCase):
    def test_payload_profile_matches_seed(self):
        """提交的 payload 必须是用种子库生效画像构建的。"""
        payload_p = os.path.join(DATA, "fair_payload.json")
        seed_p = os.path.join(DATA, "careersafari.db.seed")
        if not (os.path.exists(payload_p) and os.path.exists(seed_p)):
            self.skipTest("payload 或种子库缺席（本地开发环境）")
        active = json.load(open(payload_p, encoding="utf-8")).get("active_fair_id")
        con = sqlite3.connect("file:%s?mode=ro" % seed_p.replace("\\", "/"), uri=True)
        try:
            row = con.execute(
                "select fair_id from fair_active where id='current'").fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row, "种子库缺 fair_active/current")
        self.assertEqual(active, row[0],
                         "payload 用画像 %s 构建，种子生效画像却是 %s —— "
                         "克隆首次打开会重建变脸；跑 sync_repo.sync_profile_seed()"
                         % (active, row[0]))

    def test_seed_caches_match_code(self):
        """全部缓存种子的代码指纹必须与当前代码一致。"""
        try:
            import cache_guard
        except ImportError:
            self.skipTest("cache_guard 不可用")
        stale = []
        for fn in cache_guard.SEEDS:
            p = os.path.join(DATA, fn + ".seed")
            if not os.path.exists(p):
                stale.append("%s：种子缺席" % fn)
                continue
            ok, detail = cache_guard.check_file(p)
            if not ok:
                stale.append("%s：%s" % (fn, detail))
        self.assertEqual(stale, [],
                         "缓存种子与代码指纹不符（clone 首开会被判废归档）：\n"
                         + "\n".join(stale))


if __name__ == "__main__":
    unittest.main()
