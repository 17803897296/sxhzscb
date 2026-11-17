"""「命中岗位」只列复核后仍有效的对（2026-11-17 用户定口径）。

背景：llm_match 的 matched_jobs 是早期判定结果，主营复核（biz_recheck）撤下的
对不会从里面删掉。琥崧那行于是写成「命中岗位：材料研发工程师、工艺工程师」，
复核行同时写「撤下 1 对」——同一张卡片自相矛盾。

这里钉住三件事：
  1. 被撤下的岗位名不再出现在命中栏；
  2. 只是写法差异（后缀 / 届别 / 编号）的有效岗不能误删；
  3. real 为空（一个都没留下）时原样返回，由上层降级逻辑处理，不在这里清空。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fair_bridge  # noqa: E402


def test_drops_blocked_pair():
    assert fair_bridge._limit_to_kept(
        ["材料研发工程师", "工艺工程师"], ["材料研发工程师"]
    ) == ["材料研发工程师"]


def test_keeps_all_when_nothing_blocked():
    jobs = ["材料研发工程师", "工艺工程师"]
    assert fair_bridge._limit_to_kept(jobs, jobs) == jobs


def test_keeps_title_variant():
    # 公告里带届别 / 岗位编号的写法，不算被撤
    assert fair_bridge._limit_to_kept(
        ["材料研发工程师", "工艺工程师"],
        ["材料研发工程师（2027届）"],
    ) == ["材料研发工程师"]


def test_drops_desensitized_name():
    # 幻觉名被脱敏成「（待现场确认：xxx）」后不该再算命中
    assert fair_bridge._limit_to_kept(
        ["（待现场确认：材料研发工程师）", "材料研发工程师"],
        ["材料研发工程师"],
    ) == ["材料研发工程师"]


def test_empty_real_returns_original():
    # real 空 = 一个都没过复检，交给上层降级，别在这里把命中栏清空
    assert fair_bridge._limit_to_kept(["工艺工程师"], []) == ["工艺工程师"]


def test_all_blocked_returns_original():
    # 全部不在白名单时也原样返回（上层据此判断是否降级）
    assert fair_bridge._limit_to_kept(
        ["装配工"], ["材料研发工程师"]
    ) == ["装配工"]
