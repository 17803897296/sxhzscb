# -*- coding: utf-8 -*-
"""全量 L1/L2 分层健康守卫（138 专业逐个检查）。

2026-11-18 用户（工业工程本科）发现泛管理岗占据 L1 的语义错误后补。
锁住三条不变量：
  1. 有专属岗的专业，L1 不得有「从经管大类表补充进来的」泛管理岗
     （它们降到 L2，由 near_jobs 接住）——专属岗自身含同类词不算。
  2. 被降级的泛管理岗必须能在 L2 找到（降级 ≠ 丢弃）。
  3. L1 非空、闸门在 L1/L2 全程有效。

判定口径：泛岗是否「串层」看来源，不看词面——同一个词在别的专业大类表里
是正当对口岗（土木的项目管理、数学的数据分析、人文的产品运营），只有从
经管大类表异常补充进来的才算。
"""
import unittest

import job_recommend as J

BIZ_CATS = ("管理学（经管大类）", "经济学（经管大类）")


def all_majors():
    names = set(J.MAJOR_BASE)
    for keys, _cat in J.CATEGORY_HINT:
        names.update(keys)
    return sorted(names)


def has_own_base(major):
    return (major in J.MAJOR_BASE or any(
        (k in major) or (major and major in k) for k in J.MAJOR_BASE))


class TestTierLayoutHealth(unittest.TestCase):
    def test_every_major_has_nonempty_l1(self):
        for m in all_majors():
            with self.subTest(major=m):
                self.assertTrue(J.core_jobs(m), "%s 的 L1 为空" % m)

    def test_specialized_majors_l1_only_own_jobs(self):
        # 结构性规则：有专属岗的专业，L1 必须恰好是专属岗（大类补充全降到 L2）。
        # 不再依赖「泛岗词表」——那是列不全的（中医的医药代表、仪器的硬件工程师
        # 都不在旧经管词表里）。
        for m in all_majors():
            if not has_own_base(m):
                continue
            own = [j for j in J.base_jobs(m)
                   if not J._neighbor_core_blocked(j, m)]
            l1 = J.core_jobs(m)
            with self.subTest(major=m):
                self.assertEqual(l1, own, "%s 的 L1 不是纯专属岗" % m)

    def test_demoted_pool_jobs_reachable_in_l2(self):
        # 降级不丢弃：大类表里不在专属岗的岗，必须都能在 L2 找到
        for m in all_majors():
            if not has_own_base(m):
                continue
            own = set(J.base_jobs(m))
            l2 = J.near_jobs(m)
            for j in J.category_pool(m):
                if not J._neighbor_core_blocked(j, m) and j not in own:
                    with self.subTest(major=m, title=j):
                        self.assertIn(j, l2, "%s 的大类岗 %s 降级后丢失" % (m, j))

    def test_guard_valid_on_both_tiers(self):
        for m in all_majors():
            for j in J.core_jobs(m) + J.near_jobs(m):
                with self.subTest(major=m, title=j):
                    self.assertFalse(
                        J._neighbor_core_blocked(j, m),
                        "%s 的 L1/L2 有该拦未拦的岗 %s" % (m, j))


if __name__ == "__main__":
    unittest.main()
