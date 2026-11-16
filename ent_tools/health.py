#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代码体检：只打印数字，不做判断，永远退出 0。

    python -X utf8 health.py

## 这不是门禁

这里曾经是一套「棘轮」护栏 gate.py：13 项指标各有阈值和基线，
任何一项比上次差就退出码 1，还被挂进了 refresh_all 的第 9 步。
它被删掉了，因为它实际干的事是**给写代码收手续费**：

    · 往 fair_report_js.py 里加一个函数 → 单函数行数可能撞线
    · 觉得某个检查不对想改 → 得先跑 --force 并写理由
    · 删掉一个没用的测试 → 单测用例数「变差」被拦
    · 拆一份重复实现拆到一半 → 中途提交被拦

规律很直白：**凡是会拦人的指标，最终都会被绕过**。加 --force、删检查、
跳过那一步——绕过的成本总比遵守低。而一个被绕过的护栏，比没有护栏更坏：
它给人「有保护」的错觉。

所以现在这个文件只做一件事：把「现在是多少」摆出来。
- 没有阈值
- 没有基线，不记历史，不写任何文件
- 没有退出码（除非你自己 import 它算数时出错）
- 不在 refresh_all 里，不跑不会有人提

**看得见就够了。** 真到了需要动的时候，你自己会看出来。

## 怎么用

一个月想起来一次，或者「感觉最近有点乱」的时候跑一下。
对照第二列「2026-11-16 重构完成时」的数值，涨了跌了自己判断要不要管。
REF 只是那天的快照，不参与任何计算，改成什么都行 —— 改错了也不会有后果。

真想让它消失，删掉这一个文件就行：**没有任何模块 import 它**。
"""
import ast
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# 只扫产品链路上的代码，不扫 scratch（一次性脚本允许乱）
SKIP_DIRS = {"__pycache__", "scratch", "tests", "js", "data", "node_modules"}

# 故意留着旧实现的文件，不参与「重复实现」统计
DUP_EXEMPT_FILES = {"_is_old_tmp.py", "health.py"}
DUP_EXEMPT_NAMES = {"main"}          # 各构建脚本的入口，长得像不代表是拷贝

# 2026-11-16 重构完成时的实测快照，仅作参照
REF = {
    "py_files": 61,
    "py_lines": 20689,
    "max_file_lines": 2032,
    "max_func_lines": 736,
    "extra_copies": 11,
    "silent_except": 51,
    "bare_except": 0,
    "hardcoded_path": 0,
    "syntax_error": 0,
    "test_cases": 50,
    "stale_page": 0,
    "page_drift": 0,
}


# ------------------------------------------------------------------ 取数

def _py_files():
    out = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _root_files():
    return sorted(p for p in ROOT.glob("*.py") if p.name != "health.py")


def _parse(p):
    try:
        return ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return None


def _lines(p):
    return len(p.read_text(encoding="utf-8", errors="ignore").splitlines())


def py_files():
    return len(_root_files())


def py_lines():
    return sum(_lines(p) for p in _root_files())


def max_file_lines():
    return max((_lines(p) for p in _root_files()), default=0)


def longest_func():
    """最长的那个函数：(行数, 位置)。

    736 行的是 fair_bridge.build（内部一个 for 循环占 418 行）。
    这是全项目最典型的「改一处不知道会坏哪儿」，但拆它要动主营链路，
    所以在拆之前，这个数字就一直停在这儿 —— 体检表记着，不催。
    """
    best, where = (0, "")
    for p in _py_files():
        t = _parse(p)
        if not t:
            continue
        for n in ast.walk(t):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = n.end_lineno - n.lineno + 1
                if size > best:
                    best, where = size, "%s:%s" % (p.name, n.name)
    return best, where


def max_func_lines():
    return longest_func()[0]


def extra_copies():
    """同一份实现「多出来的份数」= 抽成一份能删掉几处。

    用 ast.unparse 还原源码后逐字比，阈值 0.95。这个阈值不是调出来的：
    真实代码上真拷贝 ≥0.954、非拷贝 ≤0.877，0.88~0.95 之间空着。
    结构指纹（0.75 / 0.88 / 0.98）都试过，每套都误报 _key、judge 这类
    「同名不同事」，噪声比信号多。

    现状 11 份，主要是 _load_cache / _save_cache 各 5 份。
    现在还它要动 5 个模块的缓存路径，收益不大；留着，别再拷第 6 份。
    """
    named = {}
    for p in _py_files():
        if p.name in DUP_EXEMPT_FILES:
            continue
        t = _parse(p)
        if not t:
            continue
        for n in t.body:                 # 只看模块级，不看嵌套闭包
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if n.name in DUP_EXEMPT_NAMES:
                continue
            try:
                named.setdefault(n.name, []).append((p.name, ast.unparse(n)))
            except Exception:
                continue

    extra = 0
    for items in named.values():
        if len(items) < 2:
            continue
        flags = [False] * len(items)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if SequenceMatcher(None, items[i][1], items[j][1]).ratio() >= 0.95:
                    flags[i] = flags[j] = True
        if sum(flags) >= 2:
            extra += sum(flags) - 1
    return extra


def silent_except():
    """`except Exception: pass` 的处数（只算块里全是 pass/continue 的）。

    采集场景里这类写法有一半是合理的（单页失败不该崩整批），
    问题只是没留日志 —— 表现是「跑完全绿，数据是缺的」。
    51 处不等于 51 个 bug，但每一处都值得在改到它的时候顺手加一行日志。
    """
    bad = 0
    for p in _py_files():
        t = _parse(p)
        if not t:
            continue
        for h in ast.walk(t):
            if not isinstance(h, ast.ExceptHandler) or h.type is None:
                continue
            if not all(isinstance(s, (ast.Pass, ast.Continue)) for s in h.body):
                continue
            try:
                if ast.unparse(h.type) in ("Exception", "BaseException"):
                    bad += 1
            except Exception:
                continue
    return bad


def bare_except():
    n = 0
    for p in _py_files():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.search(r"^\s*except\s*:\s*$", line):
                n += 1
    return n


def hardcoded_path():
    pat = re.compile(r"[A-Za-z]:[\\/]Users[\\/]|/Users/[A-Za-z]|/home/[a-z]")
    n = 0
    for p in _py_files():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if pat.search(line.split("#")[0]):
                n += 1
    return n


def syntax_error():
    """语法坏了的文件数。恒为 0；不为 0 说明有文件连 import 都过不了。

    单独列出来是因为别的检查遇到坏文件都是 `continue`（静默跳过），
    一个文件语法坏了，它其余所有指标会同时「看起来正常」。
    """
    return sum(1 for p in _py_files() if _parse(p) is None)


def test_cases():
    n = 0
    for p in (ROOT / "tests").glob("test_*.py"):
        n += len(re.findall(r"^\s+def test_",
                            p.read_text(encoding="utf-8", errors="ignore"), re.M))
    return n


def stale_page():
    """页面比 payload 旧 = 改了数据没重建页面（1 是，0 否）。"""
    page = (ROOT / "../demo/fair.html").resolve()
    payload = DATA / "fair_payload.json"
    if not page.exists() or not payload.exists():
        return 0
    return 1 if page.stat().st_mtime < payload.stat().st_mtime - 2 else 0


def page_drift():
    """demo/fair.html 的 md5 和构建戳对不上 = 有人直接手改了生成物。"""
    stamp_p = DATA / ".page_stamp.json"
    page = (ROOT / "../demo/fair.html").resolve()
    if not stamp_p.exists() or not page.exists():
        return 0
    try:
        stamp = json.loads(stamp_p.read_text(encoding="utf-8"))
    except Exception:
        return 0
    rec = stamp.get(str(page)) or {}
    md5 = hashlib.md5(page.read_bytes()).hexdigest()
    return 1 if rec.get("md5") and rec["md5"] != md5 else 0


# key, 中文名, 取数函数, 大数字是好还是坏
ITEMS = [
    ("py_files",        "根目录 py 文件数",   py_files,        "低"),
    ("py_lines",        "根目录 py 总行数",   py_lines,        "低"),
    ("max_file_lines",  "最长文件（行）",     max_file_lines,  "低"),
    ("max_func_lines",  "最长函数（行）",     max_func_lines,  "低"),
    ("extra_copies",    "多余的可删拷贝份数", extra_copies,    "低"),
    ("silent_except",   "静默吞异常处数",     silent_except,   "低"),
    ("bare_except",     "裸 except 处数",     bare_except,     "低"),
    ("hardcoded_path",  "硬编码绝对路径",     hardcoded_path,  "低"),
    ("syntax_error",    "语法错误文件数",     syntax_error,    "低"),
    ("test_cases",      "单测用例数",         test_cases,      "高"),
    ("stale_page",      "页面比数据旧",       stale_page,      "低"),
    ("page_drift",      "生成物被手改",       page_drift,      "低"),
]


def main():
    rows = [(label, fn(), REF.get(key), better) for key, label, fn, better in ITEMS]

    w = max(len(r[0]) for r in rows)
    print("代码体检  %s" % __import__("datetime").datetime.now()
          .strftime("%Y-%m-%d %H:%M"))
    print("=" * (w + 34))
    print("%-*s %14s %14s" % (w, "指标", "现在", "2026-11-16"))
    print("-" * (w + 34))
    for label, now, ref, better in rows:
        mark = ""
        if isinstance(now, int) and isinstance(ref, int) and now != ref:
            sign = "+" if now > ref else "-"
            worse = (now > ref) if better == "低" else (now < ref)
            mark = "  %s%d%s" % (sign, abs(now - ref), "  ↑" if worse else "  ↓")
        print("%-*s %14s %14s%s" % (w, label, now, "—" if ref is None else ref, mark))
    print("-" * (w + 34))
    print("↑ 比那天多/差    ↓ 比那天少/好    只是参照，不是标准")
    size, where = longest_func()
    if where:
        print("最长函数是 %s（%d 行）。" % (where, size))
    print()
    print("这不是门禁：没有阈值、没有基线、没有退出码，也不在 refresh_all 里。")
    print("觉得哪项不对就不对，接着写你的代码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
