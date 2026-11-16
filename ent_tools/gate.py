#!/usr/bin/env python
"""架构护栏：只禁止变差，不要求一次变好。

## 为什么是「棘轮」而不是「合规检查」

这个项目有历史包袱：五份缓存读写、736 行的 build、35 处静默吞异常。
这些都不可能一次清完。如果护栏一上来就判定它们「违规」，结果只有两种：
要么所有人开始加 --no-verify，要么有人把这几个检查注释掉。两种都比不做更坏。

所以 gate_baseline.json 记的是**当前实测值**，检查只问一件事：
**比上次更差了吗？** 分十次慢慢还，每次只许往下走。
真降下来了用 `python gate.py --accept` 把新值写进基线，棘轮往前咬一格。

## 每一项都对应这个项目真实踩过的坑

注释里写明了是什么坑。**不要在没有案例的情况下新增检查项** ——
没有来由的规则，第一个被人删掉。

用法：
    python gate.py            检查。变好会自动前进，只有变差才拦（退出码 1）
    python gate.py --force    连变差的也写进基线（只在你明确要放宽时用）

## 设计上刻意「不拦」的东西

写新功能、写注释、拆函数、加测试、加模块 —— 这些会让代码量涨，但都是对的事。
第一版把总行数当硬指标，结果把 1 行硬编码改成 5 行（含三行注释说明为什么）
就被拦下。护栏如果为「正确的事」收手续费，人就会开始绕过它。
所以规模类指标退到 ADVISORY 区：只显示，不拦。
"""
import ast
import json
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASELINE = ROOT / "gate_baseline.json"

# 只扫「产品链路上的代码」，不扫 scratch（一次性脚本，允许乱）
SKIP_DIRS = {"__pycache__", "scratch", "tests", "js", "data", "node_modules"}

# 整份文件排除在「重复实现」统计之外，理由是它**故意**留着一份旧实现：
# _is_old_tmp.py 是 _rule_regress.py 的规则基线，用来和新规则逐项对比退化。
# 它里面的 _parse_json / _func_terms 这些跟 intent_semantics 长得一样是应当的。
DUP_EXEMPT_FILES = {"_is_old_tmp.py"}

# 这些名字天然会在很多文件里出现，且相似度高不代表是拷贝：
#   main   —— 各大构建脚本的入口，都是 argparse + 调几个函数
DUP_EXEMPT_NAMES = {"main"}

# 生成物与源码的对应关系（防止有人直接改生成物）
GENERATED = [
    ("../demo/fair.html", ["build_fair_page.py", "fair_report_js.py",
                          "js/shim.js", "js/recommend.js", "js/match.js"]),
]


# ------------------------------------------------------------------ 工具

def root_py_files():
    return sorted(p for p in ROOT.glob("*.py") if p.name != "gate.py")


def all_py_files():
    out = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def parse(p):
    try:
        return ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return None


def func_nodes(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


# ------------------------------------------------------------------ 各项测量

def m_syntax_error():
    """解析不了的文件数。

    这一项是做拦截测试时发现的自家漏洞：所有基于 AST 的检查（重复实现、
    静默吞异常、最大函数）遇到语法错误的文件都是 `if not t: continue` ——
    静默跳过。也就是说**一个文件语法坏了，它的全部检查同时失效**，
    而体检结果看起来和平时一模一样。

    护栏自己不能有盲区，所以单独列一项。正常情况恒为 0。
    """
    bad = 0
    for p in all_py_files():
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            bad += 1
    return bad


def m_files():
    return len(root_py_files())


def m_lines():
    return sum(len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
               for p in root_py_files())


def m_max_file():
    """单文件最大行数。

    坑：build_fair_page.py 一度到 1,911 行，其中 1,437 行是三段 JS 字符串，
    改一行 Python 得先滚过八百行 JS。已抽到 js/。天花板设在这里就是不许回潮。
    """
    return max((len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
                for p in root_py_files()), default=0)


def m_max_func():
    """单函数最大行数。

    坑：fair_bridge.build 736 行，内部一个 for 循环占 418 行。
    一个函数改错，你不知道会坏在哪一层。
    """
    worst = 0
    for p in all_py_files():
        t = parse(p)
        if not t:
            continue
        for n in func_nodes(t):
            worst = max(worst, n.end_lineno - n.lineno + 1)
    return worst


def m_redundant_copies():
    """同一份实现「多出来的份数」。这不是组数，是可删的份数。

    一开始数的是「有几个函数名存在重复」，验证时立刻发现它抓不住蔓延：
    _tag_reason 本来在 2 个文件里有，再拷第 3 份，组数还是 1，指标纹丝不动。
    而复制粘贴蔓延恰恰是屎山最典型的形成方式 —— 第二份的时候没人管，
    等到第五份就没人敢动了。

    所以换成「多余份数」：每组里与至少一份相似的成员数 - 1，全部累加。
    它直接对应「抽成一份能删掉几处」，也直接对应「再拷一份就会被拦」。

    坑的由来：_call_raw 曾 5 份逐字相同，改重试策略要改五处；
    bizScope / biz_scope 两种字段名各读各的、都不报错，是同一个病的第一次发作。

    ## 为什么用「还原成源码逐字比」而不是结构指纹

    试过 0.75 / 0.88+行数 / 0.98+loose0.90 三套结构指纹阈值，每套都误报：
    _key 在 biz_fit 和 job_profile 里都是「json.dumps 后取 md5」，但一个序列化
    payload、一个序列化 {row, prompt, model}，是不同实现；judge 同理。
    误报的代价不是「多看一行提示」，是训练人忽略报警 —— 护栏死掉的标准路径。

    换成 ast.unparse 还原源码再比，相似度在真实代码上分成清晰两段：
        真拷贝   ≥0.954（_load_cache .962 / _save_cache .975 / fetch .997）
        非拷贝   ≤0.877（_row_text .877 / _key .810 / judge .743）
        0.88~0.95 一组都没有
    阈值 0.95 落在空白区中间，不是硬调的。
    """
    named = {}
    for p in all_py_files():
        if p.name in DUP_EXEMPT_FILES:
            continue
        t = parse(p)
        if not t:
            continue
        for n in t.body:                       # 只看模块级，不看嵌套闭包
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name in DUP_EXEMPT_NAMES:
                    continue
                try:
                    src = ast.unparse(n)
                except Exception:
                    continue
                named.setdefault(n.name, []).append((p.name, src))

    extra = 0
    for name, items in named.items():
        if len(items) < 2:
            continue
        # 每个成员：是否至少和一个别的成员相似
        dup_flags = [False] * len(items)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if SequenceMatcher(None, items[i][1], items[j][1]).ratio() >= 0.95:
                    dup_flags[i] = dup_flags[j] = True
        cnt = sum(dup_flags)
        if cnt >= 2:
            extra += cnt - 1
    return extra


def m_silent_except():
    """吞掉异常但什么都没记的处数。

    坑：采集脚本里 `except Exception: pass` 是合理的（单页失败不该崩整批），
    但没日志就变成静默降级。踩过的具体表现：「日志说主营=有，库里是空」、
    「--scope-only 分支提前 return 跳过了落库」，都是这个病的症状。

    只算 `except Exception` / `except BaseException`。捕具体类型的
    （`except UnicodeDecodeError`、`except sqlite3.Error`、`except (TypeError,
    ValueError)`）不算——那是「我知道可能是什么错，且这种情况本来就无妨」，
    是这个项目里合理且正确的写法。把这类也算进来，指标就失去区分力了。
    """
    bad = 0
    for p in all_py_files():
        t = parse(p)
        if not t:
            continue
        for h in ast.walk(t):
            if not isinstance(h, ast.ExceptHandler):
                continue
            # 块里只有 pass / continue / ... 才算静默
            if not all(isinstance(s, (ast.Pass, ast.Continue)) for s in h.body):
                continue
            if h.type is None:
                continue                       # 裸 except 由 m_bare_except 单独管
            try:
                tp = ast.unparse(h.type)
            except Exception:
                continue
            if tp in ("Exception", "BaseException"):
                bad += 1
    return bad


def m_bare_except():
    """裸 `except:`。它会连 KeyboardInterrupt 和 SystemExit 一起吞。"""
    n = 0
    for p in all_py_files():
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"^\s*except\s*:\s*$", line):
                n += 1
    return n


def m_hardcoded_path():
    """硬编码绝对路径。clone 到别的机器就跑不起来。"""
    pat = re.compile(r"[A-Za-z]:[\\/]Users[\\/]|/Users/[A-Za-z]|/home/[a-z]")
    n = 0
    for p in all_py_files():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            code = line.split("#")[0]
            if pat.search(code):
                n += 1
    return n


def m_bad_module_name():
    """以 `_` 开头但被别人 import 的模块数。

    坑：CDP 复用层一开始叫 _cdp.py，本地跑得好好的；但 sync_repo._blocked()
    会排除所有 `_` 开头的文件，clone 出来的实例 import portal 直接失败。
    本地测不出来，因为本地文件都在。
    """
    n = 0
    for p in all_py_files():
        if not p.name.startswith("_"):
            continue
        stem = p.stem
        for q in all_py_files():
            if q == p:
                continue
            text = q.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(import %s\b|from %s import)" % (stem, stem),
                         text, re.M):
                n += 1
                break
    return n


def m_missing_deps():
    """缓存登记表里声明了、但文件不存在的依赖数。

    坑：job_profile 根本没登记进 cache_guard.REGISTRY，它的 code_fp 退化成
    常量 md5("1|")，169 条画像缓存永远不会因为改代码而失效 —— 改了 prompt
    结论全变、缓存照样命中。这是最坏的一种静默失效。
    """
    sys.path.insert(0, str(ROOT))
    try:
        import cache_guard
    except Exception:
        return 0
    miss = 0
    for key, spec in cache_guard.REGISTRY.items():
        for dep in spec.get("deps") or []:
            if not (ROOT / dep).exists():
                miss += 1
    return miss


def m_generated_drift():
    """生成物和「构建戳」对不上的数量 = 有人直接手改了生成物。

    demo/fair.html 是 build_fair_page.py 的输出，不是源码。手改它的话，
    下次构建就全丢，而且改的人以为自己改好了。
    构建时写 data/.page_stamp.json，这里只比对 md5。
    """
    import hashlib
    stamp_p = DATA / ".page_stamp.json"
    if not stamp_p.exists():
        return 0
    try:
        stamp = json.loads(stamp_p.read_text(encoding="utf-8"))
    except Exception:
        return 0
    drift = 0
    for rel, _src in GENERATED:
        p = (ROOT / rel).resolve()
        if not p.exists():
            continue
        md5 = hashlib.md5(p.read_bytes()).hexdigest()
        rec = stamp.get(str(p)) or {}
        if rec.get("md5") and rec["md5"] != md5:
            drift += 1
    return drift


def m_stale_page():
    """页面比数据旧 = 改了数据忘了重建页面。

    坑：这个项目出过「库里有新企业、报告还是旧的」，因为采集和收尾是两个入口。
    """
    page = (ROOT / "../demo/fair.html").resolve()
    payload = DATA / "fair_payload.json"
    if not page.exists() or not payload.exists():
        return 0
    return 1 if page.stat().st_mtime < payload.stat().st_mtime - 2 else 0


def m_zombie_pipeline():
    """僵尸管道的数据文件是否还在。

    坑：collect.py → companies.json（118 家）和 collect_roster.py → 145 家并存。
    危险不在混乱，在于有一天你会跑错那个，插进去 96 家不相干企业。
    """
    n = 0
    if (DATA / "companies.json").exists():
        n += 1
    return n


def m_test_cases():
    """单测用例数。这是唯一「越大越好」的指标。"""
    n = 0
    for p in (ROOT / "tests").glob("test_*.py"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        n += len(re.findall(r"^\s+def test_", text, re.M))
    return n


# ------------------------------------------------------------------ 指标表

# ------------------------------------------------------------------ 指标表
#
# 分两张表，这是这个护栏最重要的一条设计决定：
#
#   ITEMS     会拦人。只放「客观确定」或「只有你做了明确错事才会触发」的项。
#             正常写新功能、写注释、拆函数、加测试，一条都不该撞上。
#
#   ADVISORY  只显示，不拦。规模类指标放这里。
#
# 为什么规模类不能拦：第一版把「总行数」当硬指标，结果把 1 行硬编码路径改成
# 5 行（含三行注释说明为什么），总行数涨 25，修好一项反而被拦下。
# 第二版给了 80 行容差，但同一个问题还在：**写新功能一定涨行数**，
# 用行数拦人等于禁止开发。真该盯的是「单文件/单函数暴涨」——
# 那才是屎山的形态：所有新代码都塞进一个函数。所以 max_* 留在 ITEMS，
# lines / files 退到 ADVISORY。
ITEMS = [
    # key, 中文名, 取数, 方向, 容差, 说明
    ("syntax_error",     "语法错误文件数",     m_syntax_error,     "lower", 0, "一个文件语法坏了，它的所有检查都会静默失效"),
    ("max_file_lines",   "单文件最大行数",     m_max_file,         "lower", 0, "超过 2000 行先问「哪块该拆」"),
    ("max_func_lines",   "单函数最大行数",     m_max_func,         "lower", 0, "超过 100 行先问「能不能只搬不改」"),
    ("redundant_copies", "多余的可删拷贝份数", m_redundant_copies, "lower", 0, "同一份实现拷了几份；抽成一份就能删掉这么多"),
    ("silent_except",    "静默吞异常处数",     m_silent_except,    "lower", 0, "除了 pass 什么都不做的 except"),
    ("bare_except",      "裸 except 处数",     m_bare_except,      "lower", 0, "会连 Ctrl-C 一起吞"),
    ("hardcoded_path",   "硬编码绝对路径",     m_hardcoded_path,   "lower", 0, "clone 到别的机器就跑不起来"),
    ("bad_module_name",  "下划线开头却被 import", m_bad_module_name, "lower", 0, "sync_repo 会跳过它，clone 后 import 失败"),
    ("missing_deps",     "登记表缺依赖",       m_missing_deps,     "lower", 0, "缓存指纹会退化成常量，改了代码不失效"),
    ("generated_drift",  "生成物被手改",       m_generated_drift,  "lower", 0, "demo/fair.html 是输出，改它下次构建就丢"),
    ("stale_page",       "页面比数据旧",       m_stale_page,       "lower", 0, "改了数据忘了重建页面"),
    ("zombie_pipeline",  "僵尸管道文件",       m_zombie_pipeline,  "lower", 0, "两套采集入口并存，早晚跑错"),
    ("test_cases",       "单测用例数",         m_test_cases,       "higher", 0, "唯一越大越好的指标"),
]

ADVISORY = [
    # 只显示，不拦。看了心里有数就行。
    ("files",            "根目录 py 文件数",   m_files,            "lower",  1, "主干目录只放链路模块，一次性脚本进 scratch/"),
    ("lines",            "根目录 py 总行数",   m_lines,            "lower", 80, "代码量本身不是罪，但它涨得比功能快就是信号"),
]


# ------------------------------------------------------------------ 主流程

def measure():
    out = {}
    for key, _, fn, _, _, _ in list(ITEMS) + list(ADVISORY):
        out[key] = fn()
    return out


def load_baseline():
    if not BASELINE.exists():
        return {}
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8")).get("items") or {}
    except Exception:
        return {}


def save_baseline(values):
    prev = {}
    if BASELINE.exists():
        try:
            prev = json.loads(BASELINE.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    out = {
        "_note": "gate.py 的基线。指标变好会**自动**写入（不用 --accept，"
                 "护栏不该为做了正确的事收手续费）；变差一律拦下，除非 --force。"
                 "手改这个文件等于拆掉护栏。",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "history": (prev.get("history") or [])[-8:] + [{
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": values,
        }],
        "items": values,
    }
    BASELINE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def judge(now, was, direction, tol):
    """比较一项指标。返回 (状态, 是否该拦)。

    抽成独立函数是为了能单测：容差这块最容易改错，而它改错的后果是
    「要么天天误报让人关掉 gate，要么悄悄放行真退化」，两种都不会自己暴露。
    """
    if was is None:
        return "新增", False
    if now == was:
        return "持平", False
    if direction == "lower":
        if now > was + tol:
            return "变差", True
        return ("容差内", False) if now > was else ("变好", False)
    if now < was - tol:
        return "变差", True
    return ("容差内", False) if now < was else ("变好", False)


def main(argv):
    force = "--force" in argv
    cur = measure()
    base = load_baseline()

    if not base:
        save_baseline({**cur, **{k: cur[k] for k, *_ in ADVISORY}})
        print("首次运行，已把当前实测值写进 %s 作为基线。" % BASELINE.name)
        print("之后再跑，只有让指标变差时才拦你。")
        return 0

    rows, adv, worse, better = [], [], [], []
    for key, label, _, direction, tol, why in ITEMS:
        now, was = cur[key], base.get(key)
        state, should_block = judge(now, was, direction, tol)
        if should_block:
            worse.append((key, label, was, now, tol))
        elif state == "变好":
            better.append((key, label, was, now))
        rows.append((key, label, was, now, state, direction, why, tol))

    for key, label, _, direction, tol, why in ADVISORY:
        now, was = cur[key], base.get(key)
        adv.append((label, was, now, now - (was if was is not None else now)))

    MARKS = {"变差": "✗ 变差", "变好": "✓ 变好", "持平": "· 持平",
             "新增": "+ 新增", "容差内": "~ 容差内"}

    w_key = max(len(r[1]) for r in rows)
    print("架构护栏（棘轮：只禁止变差）  %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("─" * (w_key + 40))
    for key, label, was, now, state, direction, _, tol in rows:
        extra = ""
        if direction == "higher":
            extra = "（越大越好）"
        elif tol:
            extra = "（容差 %d）" % tol
        print("%-*s %10s %10s   %s%s" % (w_key, label,
                                         "—" if was is None else was, now,
                                         MARKS[state], extra))

    if worse:
        print("\n拦下：以下 %d 项超出容差" % len(worse))
        for key, label, was, now, tol in worse:
            why = next(r[6] for r in rows if r[0] == key)
            gap = "（超容差 %d）" % tol if tol else ""
            print("  ✗ %s：%s → %s%s    %s" % (label, was, now, gap, why))
        if force:
            save_baseline({**cur, **{k: cur[k] for k, *_ in ADVISORY}})
            print("\n--force 已放宽基线。提交信息里请写清为什么这次必须放宽。")
        else:
            print("\n这些项只在你做了明确错事时才会涨 —— 修好再来。")
            print("确实判断错了（比如这条规则在本项目不适用），删掉那条检查并写明理由，"
                  "不要加 --no-verify 绕过。")
            return 1

    if better:
        print("\n有 %d 项比基线好：%s" % (
            len(better), "、".join("%s %s→%s" % (l, w, n) for _, l, w, n in better)))

    # 变好就直接前进，不要求 --accept。
    # 第一版每次变好都提示「跑 --accept 固化」，这是纯摩擦：你随手删了重复
    # 实现、抽了个函数，指标变好，却被打断一次。护栏不该为「做了正确的事」
    # 收手续费。只有变差才需要人介入。
    if better and not worse:
        save_baseline({**cur, **{k: cur[k] for k, *_ in ADVISORY}})
        print("已自动写入基线，棘轮往前咬一格。")

    if adv:
        print("\n参考（不拦，只是让你心里有数）：")
        for label, was, now, delta in adv:
            sign = "+" if delta > 0 else ""
            print("  %-*s %10s %10s   %s%s" % (w_key, label,
                                              "—" if was is None else was, now,
                                              sign, delta))
        print("  规模涨了不是罪。真该盯的是单文件/单函数暴涨 —— 那说明新代码全塞进一个地方了。")

    print("\n通过。" if not worse else "")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
