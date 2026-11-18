# -*- coding: utf-8 -*-
"""缓存陈旧与「假重启」的回归守卫（2026-11-18 结构性排雷）。

背景：用户多次遇到「改了推荐/复检代码，页面还是旧结论」。排查发现三条叠加
的静默失效链，本文件把它们钉进 CI：

  1. 推荐池 deps 只登记 job_recommend.py，漏了 major_l1_authority.py /
     biz_recheck.py —— 改权威岗位表后指纹不变，旧池继续命中；
  2. `code_fp` 第一个位置参数是 deps 不是 name，误写成 code_fp("名字")
     会把名字逐字符当文件名、全部 missing，指纹退化成常量；
  3. `restore_seeds` 不验指纹就恢复种子，clone 后旧结论「复活」；
  4. `check_file` 对容器型缓存只验时间戳最新一条，混合代际文件被判可信；
  5. `stable_server.start(force=True)` 的 force 分支写在 _http_ok 早返回之后，
     restart 在服务活着时是空操作，新代码永远进不了内存。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import cache_guard as cg
import stable_server as ss


class TestCacheDeps(unittest.TestCase):
    """1. 上游模块必须登记，否则改它缓存不失效。"""

    def test_pool_deps_cover_authority_and_recheck(self):
        deps = cg.REGISTRY["job_recommend_pool"]["deps"]
        for f in ("job_recommend.py", "major_l1_authority.py", "biz_recheck.py"):
            with self.subTest(dep=f):
                self.assertIn(f, deps,
                              "推荐池 deps 漏了 %s：改它缓存不会失效" % f)

    def test_intent_deps_cover_family_and_direction(self):
        deps = cg.REGISTRY["intent_semantics"]["deps"]
        for f in ("job_family.py", "job_direction.py"):
            with self.subTest(dep=f):
                self.assertIn(f, deps, "语义缓存 deps 漏了 %s" % f)

    def test_seed_kind_is_registered(self):
        # 种子恢复要按 kind 区分（snapshot 不作废），lookup 必须是 snapshot
        self.assertEqual(cg.REGISTRY["lookup"].get("kind"), "snapshot")


class TestCodeFpArgSafety(unittest.TestCase):
    """2. 位置参数误用不得退化成常量指纹。"""

    def test_string_arg_treated_as_name(self):
        self.assertEqual(cg.code_fp("job_recommend_pool"),
                         cg.code_fp(name="job_recommend_pool"))

    def test_not_constant_when_misused(self):
        # 若退化成「逐字符当文件名」，会等于一个与真实依赖无关的常量
        bogus = cg.code_fp(["__no_such_file__.py"])
        self.assertNotEqual(cg.code_fp("job_recommend_pool"), bogus,
                            "code_fp 位置参数误用后仍退化成常量指纹")

    def test_build_fp_covers_core_modules(self):
        for f in ("fair_bridge.py", "job_recommend.py", "major_l1_authority.py",
                  "biz_recheck.py", "payload_contract.py"):
            with self.subTest(dep=f):
                self.assertIn(f, cg.BUILD_DEPS)


class TestRestoreSeedsGuard(unittest.TestCase):
    """3. 旧代码生成的种子不得被恢复。"""

    def _tmp(self, obj):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                       encoding="utf-8")
        json.dump(obj, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_no_fingerprint_rejected(self):
        p = self._tmp({"a": {"x": 1}})
        self.assertFalse(cg._seed_fp_ok(p, "job_recommend_pool"))

    def test_old_fp_rejected(self):
        p = self._tmp({"_cg": {"fp": "deadbeef0000"}})
        self.assertFalse(cg._seed_fp_ok(p, "job_recommend_pool"))

    def test_current_fp_accepted(self):
        want = cg.code_fp(name="job_recommend_pool")
        p = self._tmp({"_cg": {"fp": want}})
        self.assertTrue(cg._seed_fp_ok(p, "job_recommend_pool"))


class TestCheckFileMixedGeneration(unittest.TestCase):
    """4. 容器里只要有一条不是本版生成，整份就不可信。"""

    def test_mixed_generation_rejected(self):
        want = cg.code_fp(name="job_recommend_pool")
        obj = {"A": {"_cg": {"fp": want, "ts": "2026-11-18 10:00:00"}},
               "B": {"_cg": {"fp": "oldoldold000", "ts": "2026-11-17 10:00:00"}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "job_recommend_pool.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f)
            with mock.patch.object(cg, "rel", return_value=p):
                ok, why = cg.check_file("job_recommend_pool")
        self.assertFalse(ok, "混合代际容器应判不可信，实际：%s" % why)
        self.assertIn("混合代际", why)

    def test_all_current_accepted(self):
        want = cg.code_fp(name="job_recommend_pool")
        obj = {"A": {"_cg": {"fp": want, "ts": "2026-11-18 10:00:00"}},
               "B": {"_cg": {"fp": want, "ts": "2026-11-18 10:00:01"}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "job_recommend_pool.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f)
            with mock.patch.object(cg, "rel", return_value=p):
                ok, _why = cg.check_file("job_recommend_pool")
        self.assertTrue(ok)


class TestStartForceStopsFirst(unittest.TestCase):
    """5. restart 必须先停旧进程，不能被 _http_ok 早返回吃掉。"""

    def test_force_calls_stop_and_aborts_if_port_busy(self):
        calls = []
        with mock.patch.object(ss, "_http_ok", return_value=True), \
             mock.patch.object(ss, "stop",
                               side_effect=lambda *a, **k: calls.append("stop")), \
             mock.patch.object(ss, "_wait_port_free", return_value=False):
            rc = ss.start(8102, force=True)
        self.assertEqual(calls, ["stop"], "force 分支没先停旧进程")
        self.assertEqual(rc, 1, "端口未释放时应中止，而不是假装已重启")

    def test_plain_start_is_still_idempotent(self):
        with mock.patch.object(ss, "_http_ok", return_value=True), \
             mock.patch.object(ss, "stop") as stop:
            self.assertEqual(ss.start(8102), 0)
            stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
