# -*- coding: utf-8 -*-
"""JS 渲染 A/B：旧的一次性 --dump-dom  vs  新的常驻浏览器复用标签页。

用法：
    python -X utf8 _js_ab.py
    python -X utf8 _js_ab.py --urls u1 u2          # 指定页面
    python -X utf8 _js_ab.py --from-db 12          # 从 job_evidence 取真实招聘页

判据（三条都要看，只看耗时是不够的）：
    1. 成功率        —— 谁拿得到正文
    2. 正文长度      —— 渲染完的内容是否等价（比例 0.9~1.1 视为一致）
    3. 岗位词命中数  —— 下游真正吃的是这个，长度变了但岗位变少就是质量下降
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cdp           # noqa: E402
import portal        # noqa: E402

JOB_WORDS = ["招聘", "岗位", "职位", "薪资", "学历", "专业", "投递", "简历",
             "工程师", "管培", "实习", "校招", "年薪", "任职要求", "岗位职责"]


def norm(h):
    return re.sub(r"\s+", "", portal.strip_html(h or ""))


def hits(t):
    return sum(1 for w in JOB_WORDS if w in t)


def urls_from_db(n):
    db = HERE / "data" / "careersafari.db"
    con = sqlite3.connect(db)
    seen, out = set(), []
    for (u,) in con.execute(
            "select source_url from job_evidence where source_url like 'http%'"):
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= n:
            break
    return out


def run(urls, timeout, budget):
    exe = portal.browser_exe()
    if not exe:
        print("找不到 Edge/Chrome，跳过")
        return 1
    print("浏览器:", exe)
    print("超时 %ss / 预算 %sms\n" % (timeout, budget))
    head = "%-46s %8s %8s %10s %10s %7s" % ("URL", "旧耗时", "新耗时", "旧字数", "新字数", "岗位")
    print(head)
    print("-" * len(head))
    tot_o = tot_n = 0.0
    ok_o = ok_n = 0
    worse = []
    for u in urls:
        short = u if len(u) <= 46 else u[:43] + "..."
        t = time.time()
        old = portal._fetch_js_locked(u, exe, timeout=timeout, budget=budget)
        to = time.time() - t
        t = time.time()
        new = cdp.render(u, exe, timeout=timeout, budget=budget)
        tn = time.time() - t
        a, b = norm(old), norm(new)
        tot_o += to
        tot_n += tn
        ok_o += 1 if a else 0
        ok_n += 1 if b else 0
        ratio = (min(len(a), len(b)) / max(len(a), len(b))) if (a and b) else 0.0
        flag = "" if ratio >= 0.9 else "  <-- 差异"
        if a and b and hits(b) < hits(a) - 2:
            worse.append((u, hits(a), hits(b)))
            flag = "  <-- 岗位变少"
        print("%-46s %7.2fs %7.2fs %10d %10d %6d%s"
              % (short, to, tn, len(a), len(b), hits(b), flag))
    n = len(urls)
    print("-" * len(head))
    print("合计 %d 页：旧 %.2fs（成功 %d）  新 %.2fs（成功 %d）  提速 %.1f%%，"
          "平均每页 %.2fs -> %.2fs"
          % (n, tot_o, ok_o, tot_n, ok_n,
             (1 - tot_n / tot_o) * 100 if tot_o else 0,
             tot_o / n, tot_n / n))
    if worse:
        print("\n岗位词变少的页面（需人工看）：")
        for u, x, y in worse:
            print("  %s  旧 %d -> 新 %d" % (u, x, y))
    print("\nCDP 计数:", cdp.stats())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", nargs="*", default=None)
    ap.add_argument("--from-db", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--budget", type=int, default=6000)
    a = ap.parse_args(argv)
    urls = a.urls or urls_from_db(a.from_db)
    return run(urls, a.timeout, a.budget)


if __name__ == "__main__":
    sys.exit(main())
