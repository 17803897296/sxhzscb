#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""改完代码后的标准收尾——一条命令做完，不再靠记忆。

为什么需要它：
    过去每次改判定逻辑，总有一个环节没跟着更新：缓存还是旧的、页面还是旧的、
    payload 没重建，于是出现「规则明明改了，页面还是旧结论」。靠人记着 rm 缓存
    不可靠，所以把收尾动作固定成流水线，并且任何一步失败就停、退出码非 0。

流程：
    1  缓存体检     指纹不符的旧缓存归档到 data/_stale_cache/（可回溯）
    2  重算方向     按主营/岗位重算方向提示（_dir_fill.py）
    3  重建 payload  失效项在此自动重建（会调用模型，慢）
    4  重建页面     demo/fair.html（内联 JS 与 payload 取数逻辑）
    5  测试数据页   demo/test_users.html（三套随机画像，一键切换 / 提交入库）
    6  测试夹具     _js_matchintent.js / data/_parity.json 从**当前源码**重新生成，
                   不再维护手工副本（手工副本漂移过一次，等于拿旧规则给小抄打分）
    7  JS 语法校验   页面内联 script + 夹具 JS 都过 node --check
    8  一致性测试    前后端匹配 / 前后端矩阵 parity / 规则回归

这里**没有**「代码体检」这一步。以前加过一个 gate.py（指标不许变差），
跑了两天就发现它是在给写代码收手续费：加个函数、拆个文件都可能撞线。
现在体检归 health.py，只打印数字、永远退出 0，也**不在本流程内**——
想看就自己跑，不想看它绝不会拦你。

用法：
    python refresh_all.py             # 全流程
    python refresh_all.py --skip-build  # 不重建 payload（只查缓存 + 页面 + 测试）
    python refresh_all.py --skip-tests  # 只重建，不跑测试
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

import cache_guard

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(ROOT, "demo", "fair.html")


def _run(label, cmd, cwd=HERE, allow_fail=False, timeout=None):
    t0 = time.time()
    print("\n== %s ==" % label)
    print("   $ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-14:]
    for line in tail:
        print("   " + line)
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()[-10:]
        for line in err:
            print("   ! " + line)
        if not allow_fail:
            print("   [失败] %s（%.0fs）" % (label, time.time() - t0))
            sys.exit(p.returncode or 1)
        print("   [注意] %s 非零退出（%.0fs），按约定继续" % (label, time.time() - t0))
    else:
        print("   [通过] %.0fs" % (time.time() - t0))
    return p


def step_cache():
    print("\n== 1/8 缓存体检 ==")
    # 活缓存不入库，clone 后第一次跑要先从只读种子复制出来（同 careersafari.db.seed）。
    restored = cache_guard.restore_seeds()
    if restored:
        print("   从只读种子恢复：" + "、".join(restored))
    moved = cache_guard.purge_invalid()
    if moved:
        for n, p in moved:
            print("   归档 " + n + " -> " + os.path.relpath(p, HERE))
    else:
        print("   没有需要归档的缓存")
    cache_guard.status()


def step_infer_fill():
    """对 0 岗 + 非校招企业建不带意向的画像，把推断岗补进库并逐岗标注。

    必须在 dirfill / build 之前：方向表和 payload 都要看到补进去的推断岗标注。
    校招核实库认作校招的企业不碰（它们用真实 URL 岗）。FAIR_INFER_FILL=0 可关闭。
    """
    print("\n== 1/8 画像推断岗补库（0岗+非校招）==")
    _run("fill_inferred_jobs.py",
         [sys.executable, "-X", "utf8", "fill_inferred_jobs.py"])


def step_dirfill():
    """按主营业务重算「岗位方向」，写进 job_dir_hint 表。

    放在重建 payload 之前：报告侧读这张表优先、算不出来才现算。新企业入库后
    没跑这一步不会报错（会退回现算），但慢、而且 seed 库里的 hint 会缺，
    下次干净 clone 就丢；所以固定成流水线的一步。纯本地规则，秒级。
    """
    print("\n== 2/8 重算岗位方向 ==")
    _run("_dir_fill.py", [sys.executable, "-X", "utf8", "_dir_fill.py"])


def step_build():
    print("\n== 3/8 重建 payload ==")
    p = _run("fair_bridge.build()", [sys.executable, "-X", "utf8", "-c",
                                     "import fair_bridge;fair_bridge.build()"])
    return p


def step_page():
    print("\n== 4/8 重建页面 ==")
    _run("build_fair_page.py", [sys.executable, "-X", "utf8", "build_fair_page.py"])


def step_test_users():
    """重建「测试数据」页：三套随机画像（药学 / 工程造价 / 能源与动力工程）。

    这一页是给演示和验收用的入口，早先只在本地生成过一次、没进流水线，
    结果后面重建时没人记得跑它，页面上就找不到入口了。
    现在每次 refresh 都重生成，并保证服务端能直接访问。
    """
    print("\n== 5/8 重建测试数据页 ==")
    _run("build_test_users_page.py",
         [sys.executable, "-X", "utf8", "build_test_users_page.py"])


# 前后端一致性的意向清单：覆盖面比任何一个学生的画像都宽，改匹配规则时先看它。
PARITY_INTENTS = [
    "供应链管培生", "采购寻源专员", "供应链计划专员", "数据分析岗", "运营管理培训生",
    "质量工程师", "新媒体运营专员", "外贸业务员", "软件开发工程师", "机械设计工程师",
    "电气工程师", "人力资源专员", "市场营销专员", "法务专员", "临床医学专员",
    "中学语文教师", "行政专员", "客户经理", "会计", "化学教师", "管培生", "物流专员",
    "产品经理", "嵌入式软件开发",
]


def step_fixtures():
    """从当前源码重新生成测试夹具，杜绝「拿手工副本给小抄」的假一致。

    过去 `_js_matchintent.js` 是页面规则块的手抄副本，改页面时忘同步，
    parity 测试就用旧规则算出「一致」，等于白跑。这里直接切页面源码里的
    `const MI_GEN=` … `function matchIntent(` 之间那段，测的就是线上跑的那份规则。
    """
    print("\n== 6/8 生成测试夹具 ==")
    import json

    from fair_report_js import REPORT_JS
    import intent_semantics as IS

    a = REPORT_JS.index("const MI_GEN=")
    b = REPORT_JS.index("function e(s){")   # 到 matchIntent 结束、样式函数之前
    js = ("/* 自动生成，勿手改：抽自 fair_report_js.py 的页面规则块，"
          "由 refresh_all.py 每次重新生成 */\n" + REPORT_JS[a:b].strip() + "\n")
    open(os.path.join(HERE, "_js_matchintent.js"), "w", encoding="utf-8").write(js)
    print("   _js_matchintent.js  %d 字符（抽自页面源码）" % len(js))

    payload = json.load(open(os.path.join(HERE, "data", "fair_payload.json"), encoding="utf-8"))
    titles = sorted({str(t).strip() for r in payload.get("rows") or []
                     for t in (r.get("job_titles") or []) if str(t).strip()})
    mat = {it: {t: bool(IS._role_ok(it, t) and IS._share_core(it, t)) for t in titles}
           for it in PARITY_INTENTS}
    # 只看 role/share 是盲区：命中最终结论还取决于 pair_verdict（ok/ask/no）。
    # 2026-11-15「电磁设计 × 机械设计」就是 verdict 层漏判——role/share 都对，
    # 但方向不同。这里把 verdict 也抽成夹具，前端 miPairVerdict 必须逐对一致。
    ver = {it: {t: IS.pair_verdict(t, it) for t in titles} for it in PARITY_INTENTS}
    # 轮岗类判定也做同源对拍：它决定「值得问一句」的范围，
    # 前后端一旦不一致，页面就会问出后端不会问的话。
    rot = {t: bool(IS.is_rotation_job(t)) for t in titles}
    json.dump({"ints": PARITY_INTENTS, "titles": titles, "mat": mat,
               "ver": ver, "rot": rot},
              open(os.path.join(HERE, "data", "_parity.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print("   data/_parity.json    %d 意向 × %d 岗位 = %d 对（含 verdict）"
          % (len(PARITY_INTENTS), len(titles), len(PARITY_INTENTS) * len(titles)))


def step_js():
    print("\n== 7/8 JS 语法校验 ==")
    if not os.path.isfile(PAGE):
        print("   [失败] 找不到 " + PAGE)
        sys.exit(1)
    src = open(PAGE, encoding="utf-8").read()
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
    js = "\n;\n".join(b for b in blocks if "src=" not in b[:80])
    fd, tmp = tempfile.mkstemp(suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(js)
    try:
        _run("node --check 页面（%d 个 script 块 / %d 字符）" % (len(blocks), len(js)),
             ["node", "--check", tmp])
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    _run("node --check 规则夹具（_js_matchintent.js）",
         ["node", "--check", os.path.join(HERE, "_js_matchintent.js")])


def step_tests():
    print("\n== 8/8 一致性测试 ==")
    _run("前后端匹配一致（_test_match.js）", ["node", "_test_match.js"])
    _run("24 意向 × 岗位前后端 parity（_test_parity.js）", ["node", "_test_parity.js"])
    # 规则回归脚本只打印退化数，没有退出码约定，从输出里抓关键行
    p = _run("规则回归（_rule_regress.py）", [sys.executable, "-X", "utf8", "_rule_regress.py"],
             allow_fail=True)
    m = re.search(r"退化项\s*(\d+)\s*个", p.stdout or "")
    if m and int(m.group(1)) > 0:
        print("   [失败] 规则回归退化 %s 项" % m.group(1))
        sys.exit(1)


def step_collect(argv):
    """联网采集（可选）：从展位表出发，搜企业 → 抓页面 → 模型抽取 → 落库。

    以前采集和收尾是两个入口，人得记住「先跑 collect_roster，再跑 refresh_all」，
    中间漏一步就会出现「库里有新企业、报告还是旧的」。现在 --collect 串在一起。
    """
    print("\n== 采集中（联网，可跳过） ==")
    cmd = [sys.executable, "-X", "utf8", "collect_roster.py"]
    for i, a in enumerate(argv):
        if a == "--workers" and i + 1 < len(argv):
            cmd += ["--workers", argv[i + 1]]
    mode = [x for x in ("--scope-only", "--retry-miss", "--force") if x in argv]
    cmd += mode
    _run("collect_roster.py", cmd, timeout=3600)


def step_db():
    """确保活库存在：仓库只放只读种子，clone 后第一次跑不依赖先起一次服务。

    fair_server 启动时也会做同样的事，但流水线（refresh_all）不该依赖它先跑过
    ——否则干净 clone 直接 refresh_all 会报 no such table（2026-11-17 复查修）。
    """
    db = os.path.join(HERE, "data", "careersafari.db")
    seed = db + ".seed"
    if not os.path.exists(db):
        if os.path.exists(seed):
            import shutil
            shutil.copy2(seed, db)
            print("\n== 0/8 活库初始化 ==\n   从只读种子复制 data/careersafari.db")


def main(argv):
    skip_build = "--skip-build" in argv
    skip_tests = "--skip-tests" in argv
    t0 = time.time()
    step_db()
    if "--collect" in argv:
        step_collect(argv)
    step_cache()
    step_infer_fill()
    step_dirfill()
    if not skip_build:
        step_build()
    step_page()
    step_test_users()
    step_fixtures()
    step_js()
    if not skip_tests:
        step_tests()
    print("\n全部通过，用时 %.0fs。页面：%s" % (time.time() - t0, PAGE))
    print("服务端读到的是刚写入的 data/fair_payload.json，无需重启 %s。"
          % os.environ.get("FAIR_PORT", "8102"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
