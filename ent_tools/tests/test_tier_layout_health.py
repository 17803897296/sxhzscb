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

GENERIC = J._GENERIC_MGMT
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

    def test_specialized_majors_no_generic_in_l1_from_biz_pool(self):
        # 有专属岗 + 归经管大类的专业：大类表补充里的泛岗必须已降级
        for m in all_majors():
            if not has_own_base(m) or J.major_category(m) not in BIZ_CATS:
                continue
            own = J.base_jobs(m)
            supplement = [j for j in J.category_pool(m) if j not in own]
            l1 = J.core_jobs(m)
            for g in GENERIC:
                with self.subTest(major=m, title=g):
                    self.assertFalse(
                        g in supplement and g in l1 and g not in own,
                        "%s 的 L1 还留着大类表泛岗 %s" % (m, g))

    def test_demoted_generic_reachable_in_l2(self):
        for m in all_majors():
            if not has_own_base(m) or J.major_category(m) not in BIZ_CATS:
                continue
            own = set(J.base_jobs(m))
            l2 = J.near_jobs(m)
            for j in J.category_pool(m):
                if J._is_generic_mgmt(j) and j not in own:
                    with self.subTest(major=m, title=j):
                        self.assertIn(j, l2, "%s 的泛岗 %s 降级后丢失" % (m, j))

    def test_guard_valid_on_both_tiers(self):
        for m in all_majors():
            for j in J.core_jobs(m) + J.near_jobs(m):
                with self.subTest(major=m, title=j):
                    self.assertFalse(
                        J._neighbor_core_blocked(j, m),
                        "%s 的 L1/L2 有该拦未拦的岗 %s" % (m, j))


if __name__ == "__main__":
    unittest.main()
