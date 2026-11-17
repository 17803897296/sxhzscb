# -*- coding: utf-8 -*-
"""sqlite 连接收口守卫：新代码不许再直接 sqlite3.connect。

背景见 repository.py 开头的四条翻车记录（漏 close 锁库、忘 commit 丢写、路径三种
拼法、空文件探测把流水线第一步搞崩）。2026-11-17 起了 repository.py 做唯一出口，
首批收口了 build 链路上的 8 处（fair_bridge / ind_code / cache_guard）。

这条测试做两件事：
  ① 活代码里每个文件的裸连接数不许**超过**下面登记的数（多一个就挂）；
  ② 每收口一处就把数字改小，收完删行 —— 这是一份可执行的待办清单，
     不是「以后有空再说」。
备份目录（data/_backup_*）与 scratch/ 不算活代码；tests/ 里的测试自己建临时库，
用裸连接反而更直观，也不收。
"""
import io
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 文件 → 还允许有几处裸连接（2026-11-17 收口后的现状）。
# 只剩这几处：要么一次开两个连接在同一个循环里读写（清白要引入 ExitStack），
# 要么是一次性补采脚本（跑完就废）。往下减，别往上加。
BUDGET = {
    "_biz_fill.py": 1,
    "_biz_fill2.py": 1,
    "_dir_fill.py": 2,
    "_ind_code_llm.py": 2,
    "pipeline/_js_ab.py": 1,
    "pipeline/build_campus_recheck.py": 2,
    "pipeline/build_enterprises_page.py": 1,
    "build_fair_page.py": 1,
    "collect_roster.py": 3,
    "pipeline/enrich_companies.py": 2,
    "fair_bridge.py": 1,
    "fair_ingest.py": 1,
    "fill_inferred_jobs.py": 2,
    "pipeline/import_roster.py": 1,
    "ind_code.py": 4,
    "job_profile.py": 1,
    "job_store.py": 1,
    "load_to_db.py": 1,
    "match_fill.py": 1,
    "pipeline/merge_biz.py": 1,
    "pipeline/merge_handoff_jobs.py": 2,
    "sync_repo.py": 4,
    "test_sessions.py": 1,
}


def raw_connects(path):
    """数「真的调用了」的 sqlite3.connect。

    用 AST 数而不是 grep：注释和文档字符串里会提到它（repository.py 的开头就
    写着这次翻车），拿文本数会把解释文字也算成一个连接点。
    """
    import ast
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "connect"
                and isinstance(f.value, ast.Name) and f.value.id == "sqlite3"):
            n += 1
    return n


class TestNoRawSqlite(unittest.TestCase):
    def test_budget_not_exceeded(self):
        over = {}
        for f in sorted(os.listdir(HERE)):
            if not f.endswith(".py") or f == "repository.py":
                continue
            n = raw_connects(os.path.join(HERE, f))
            cap = BUDGET.get(f, 0)
            if n > cap:
                over[f] = (cap, n)
        self.assertEqual(over, {}, "有文件新增了裸 sqlite3.connect（上限, 实际）：%r" % over)

    def test_stale_budget_entries_removed(self):
        """收口完的文件要从 BUDGET 里删掉，否则清单会长期虚高、失去约束力。"""
        stale = {}
        for f, cap in BUDGET.items():
            p = os.path.join(HERE, f)
            if not os.path.exists(p):
                # .gitignore 的本机一次性脚本（ent_tools/_*.py）在克隆/CI 里不存在，
                # 是预期缺席，不算 stale；但 git 跟踪的文件缺席必须红。
                if not self._git_tracked(f):
                    continue
                stale[f] = "文件不存在"
                continue
            n = raw_connects(p)
            if n < cap:
                stale[f] = (cap, n)
        self.assertEqual(stale, {}, "预算该下调/删除（上限, 实际）：%r" % stale)

    @staticmethod
    def _git_tracked(rel):
        """文件是否被 git 跟踪（冷克隆/CI 里判断「缺席是预期还是失职」）。"""
        import subprocess
        try:
            r = subprocess.run(
                ["git", "ls-files", "--", rel], cwd=HERE,
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True   # 拿不准就按缺席处理，宁红勿漏
        return bool(r.stdout.strip())

    def test_repository_is_usable(self):
        import repository as R
        if not os.path.exists(R.LIVE_DB):
            # 不能直接 connect：sqlite 对缺席路径会静默创建 0 表空壳，
            # 留在克隆里会坑死所有「看文件存在就当活库用」的下游（比如 build）。
            self.skipTest("活库不在（冷克隆/CI 只有种子），跳过可用性检查")
        with R.connect(R.LIVE_DB) as con:
            self.assertIsNotNone(con.execute("SELECT 1").fetchone())


if __name__ == "__main__":
    unittest.main()
