# -*- coding: utf-8 -*-
"""对口理由的措辞口径（2026-11-17 删「更细≠同岗」之后钉住）。

为什么要这组断言：
    这条规则原先有两份判据、两份文案。删规则时只改了 Python 的 pair_verdict 与
    _reason_by_direction，前端 miDirReason 对同一对直接 return ''，页面只好拿模型
    原句兜底——用户看到的仍是「不是同一个岗位」。所以理由句本身必须被测试钉住，
    不能只测 verdict。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_semantics as S  # noqa: E402


def test_more_specific_job_reads_as_aligned():
    """岗位比意向更细（大类意向进小类）：写「同一条线，算对口」，不再否决。"""
    s = S._reason_by_direction("仓储物流", "物流专员")
    assert "下面的具体方向" in s
    assert "算对口" in s
    assert "不是同一个岗位" not in s


def test_more_specific_job_is_a_hit():
    """判据和文案必须同向：这一对本身也得判 ok，否则又是「文案说对、判定说不」。"""
    assert S.pair_verdict("仓储物流", "物流专员") == "ok"
    assert S.pair_verdict("AI 算法", "算法工程师") == "ok"


def test_parallel_direction_still_says_different():
    """并列方向（后端 × 量化）是真不同，不能因为删了大小类规则就一起放行。"""
    assert S.pair_verdict("量化开发工程师", "后端开发工程师") == "no"
    s = S._reason_by_direction("量化开发工程师", "后端开发工程师")
    assert "方向不同" in s


def test_intent_without_direction_says_concrete_post():
    """意向没写方向（管培生）：说「这一大类下的具体岗位」，不回读岗位核噪音。"""
    s = S._reason_by_direction("高分子材料研发工程师", "管培生")
    assert "这一大类下的具体岗位" in s
    assert "不是同一细分" not in s


def test_wider_job_has_no_reason():
    """岗位比意向更泛（AI算法研究 → 算法）是更宽的同类岗，不需要解释差异。"""
    assert S._reason_by_direction("算法工程师", "AI算法研究工程师") == ""
