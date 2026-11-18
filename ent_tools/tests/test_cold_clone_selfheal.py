# -*- coding: utf-8 -*-
"""冷克隆自愈与构建产物同步（2026-11-18 冷克隆演练实测出来的两个绊脚石）。

**绊脚石一：新同学 clone 下来 pytest 直接红。**
仓库只跟踪 `careersafari.db.seed`，活库不入库。「缺就复制」原本只写在
`fair_server` 的启动钩子里，于是只有跑服务那条路能自愈。`pytest` 不跑服务，
`fair_bridge` 走 `repository.connect(LIVE_DB)` → 文件不存在 → sqlite 顺手建一个
0 字节空库 → `no such table: company_jobs` → 两个复检展示行测试红，报错还指不到
真正原因。实测：`git archive HEAD | tar -x` 出来的干净目录跑 pytest 得到
「2 failed, 1 skipped」；把自愈下沉到 connect 之后变 281 全绿。

**绊脚石二：提交的页面与提交的 payload 不同步，CI 必然红。**
`build_fair_page` 读 `data/fair_payload.json` 内联进 `demo/fair.html`。这条链
只靠 CI 的 `git diff --exit-code` 兜着，本地改完 payload 忘了重建，提交上去
页面还内联着上一版（实测：payload 是 11:13:00 版，页面内联着 10:34:33 版，
数据内容也不同）。这里用一条只读断言提前到本地 pytest——不用跑构建、不写盘。
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
ROOT = os.path.dirname(HERE)

import repository  # noqa: E402


class TestLiveDbSelfHeal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "probe.db")

    def _make_seed(self, name="probe.db.seed"):
        """造一份带 company_jobs 的「种子」，模拟只读种子库。"""
        seed = os.path.join(self.tmp, name)
        con = sqlite3.connect(seed)
        try:
            con.execute("create table company_jobs (name text, item_json text)")
            con.execute("insert into company_jobs values ('甲','{}')")
            con.commit()
        finally:
            con.close()
        return seed

    def test_missing_db_copied_from_seed(self):
        self._make_seed()
        self.assertFalse(os.path.exists(self.db))
        self.assertTrue(repository.ensure_live_db(self.db, quiet=True))
        self.assertTrue(os.path.getsize(self.db) > 0)
        with repository.connect(self.db) as con:
            self.assertEqual(
                con.execute("select count(*) from company_jobs").fetchone()[0], 1)

    def test_zero_byte_residue_overwritten(self):
        """sqlite3.connect 探测失败会留下 0 字节空文件，不能把它当成可用库。"""
        self._make_seed()
        open(self.db, "wb").close()
        self.assertEqual(os.path.getsize(self.db), 0)
        self.assertTrue(repository.ensure_live_db(self.db, quiet=True))
        self.assertTrue(os.path.getsize(self.db) > 0)

    def test_existing_db_untouched(self):
        """有内容的活库一律不动——自愈不是覆盖。"""
        seed = self._make_seed()
        shutil.copy2(seed, self.db)
        before = os.path.getmtime(self.db)
        self.assertFalse(repository.ensure_live_db(self.db, quiet=True))
        self.assertEqual(os.path.getmtime(self.db), before)

    def test_no_seed_no_crash(self):
        """连种子都没有时安静返回 False，别抛。"""
        self.assertFalse(repository.ensure_live_db(self.db, quiet=True))

    def test_connect_makes_cold_clone_work(self):
        """冷克隆路径：connect 一次就自动有库，不需要先跑服务。"""
        self._make_seed()
        with repository.connect(self.db) as con:
            n = con.execute("select count(*) from company_jobs").fetchone()[0]
        self.assertEqual(n, 1)


class TestPageEmbedsCommittedPayload(unittest.TestCase):
    """提交的页面必须内联提交的 payload，否则 CI 的 git diff 会红。"""

    def test_fair_html_embeds_current_payload(self):
        page = os.path.join(ROOT, "demo", "fair.html")
        payload_p = os.path.join(DATA, "fair_payload.json")
        if not (os.path.exists(page) and os.path.exists(payload_p)):
            self.skipTest("页面或 payload 缺席")
        payload = open(payload_p, encoding="utf-8").read()
        html = open(page, encoding="utf-8").read()
        # 构建是原样字符串替换（REPORT_JS.replace("__TRI_DATA__", TRI_JSON)），
        # 所以完整 payload 必须逐字出现在页面里。
        self.assertIn(payload, html,
                      "demo/fair.html 内联的不是当前 data/fair_payload.json —— "
                      "说明改了 payload 没重建页面，跑 python -m ent_tools build")

    def test_match_html_also_embeds_current_payload(self):
        """match.html 与 fair.html 共用同一份内联 payload，也必须同步。"""
        match = os.path.join(ROOT, "demo", "match.html")
        payload_p = os.path.join(DATA, "fair_payload.json")
        if not (os.path.exists(match) and os.path.exists(payload_p)):
            self.skipTest("页面或 payload 缺席")
        payload = open(payload_p, encoding="utf-8").read()
        html = open(match, encoding="utf-8").read()
        self.assertIn(payload, html,
                      "demo/match.html 内联的不是当前 payload，跑 build 重建")


if __name__ == "__main__":
    unittest.main()
