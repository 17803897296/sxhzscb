# -*- coding: utf-8 -*-
"""统一缓存卫生（cache hygiene）——改代码后旧结论必须自动作废。

为什么要有这个文件：
    过去反复踩同一个坑——改了判定规则、换了模型，页面/报告还是旧结论。
    根因不是"忘了 rm"，而是每个缓存各写一套 key，key 里只有输入数据，
    没有**生成这段结果的代码**。于是：
      · 改了 _role_ok / _sanitize，intent_semantics 缓存 key 不变 → 旧 map 照样命中；
      · 改了 MAJOR_BASE 词表，job_recommend 池子 key 不变 → 旧岗位池被复用；
      · 换了模型，unknown_infer 缓存不知道 → 页面上写着"大模型判断"其实是上个模型的。
    人工删缓存不可靠（漏一个就又是一轮误判），所以在这里把「代码指纹」做成硬约束：

        key = md5(缓存名 | 依赖模块的语法树指纹 | 业务参数...)

    用 AST 指纹而不是文件 mtime：改注释/空行不会误伤；改任何一行逻辑或提示词
    都会失效。失效时不静默沿用——旧文件归档到 data/_stale_cache/ 并可回溯。

登记制：
    任何新缓存必须先在 REGISTRY 里登记（名字/路径/依赖模块/类型）。
    python cache_guard.py status 会把「未登记但看起来像缓存的 json」列出来提醒。

用法：
    python cache_guard.py status                 # 缓存体检
    python cache_guard.py purge                  # 归档所有可重建缓存
    python cache_guard.py purge intent_semantics # 只归档指定缓存
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

import repository            # noqa: E402  sqlite 连接的唯一出口

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STALE = os.path.join(DATA, "_stale_cache")
DB = os.path.join(DATA, "careersafari.db")

# kind:
#   derived  —— 由本地代码算出来的结果，代码变即作废（默认）
#   snapshot —— 外部抓取的原始快照，代码变不作废，只报年龄（重抓成本高）
#   db       —— 结果落在 sqlite 表里，用 cache_meta 表记指纹
#   artifact —— 产物（payload/html），只报新鲜度，不作废
REGISTRY = {
    "intent_semantics": {
        "path": "intent_semantics_cache.json", "kind": "derived",
        # 2026-11-18：pair_verdict / same_dir / 方向升级都直接调 job_family
        # 与 job_direction，族表或方向表一改，缓存里的判定就可能翻，必须跟着失效。
        "deps": ["intent_semantics.py", "job_level.py",
                 "job_family.py", "job_direction.py", "demo_v2/rules.py"],
        "desc": "意向岗位 ↔ 企业岗位名的语义判定",
    },
    "job_recommend_pool": {
        "path": "job_recommend_pool.json", "kind": "derived",
        # 2026-11-18：池内容由三处共同决定——job_recommend 的分层规则、
        # major_l1_authority 的专属对口岗表（base_jobs / has_own_jobs 直接查它）、
        # biz_recheck 的邻专业闸门词表。之前只登 job_recommend.py，
        # 改权威表后指纹不变、旧池继续命中——这是「改了代码没生效」的根因之一。
        "deps": ["job_recommend.py", "major_l1_authority.py", "biz_recheck.py"],
        "desc": "按专业/经历生成的校招岗位推荐池",
    },
    "unknown_infer": {
        "path": "unknown_infer_cache.json", "kind": "derived",
        # 2026-11-16：传输层（_call_raw）已合并到 llm.py，所以加这一项。
        # 否则「改了 prompt 的拼装方式但忘了改某个模块」时缓存不会失效。
        "deps": ["unknown_infer.py", "llm.py"],
        "desc": "无具体岗企业（未知1/未知2）按意向推断",
    },
    "biz_fit": {
        "path": "biz_fit_cache.json", "kind": "derived",
        # 依赖 job_family / intent_semantics 是因为 _settle() 用岗位族判「证据与意向同方向」，
        # 族表一改，缓存里的 yes/ask 结论就可能翻，必须跟着失效。
        # 2026-11-16：传输层（_call_raw）已合并到 llm.py，所以加这一项。
        # 否则「改了 prompt 的拼装方式但忘了改某个模块」时缓存不会失效。
        "deps": ["biz_fit.py", "job_family.py", "intent_semantics.py", "llm.py"],
        "desc": "按主营业务判企业是否与学生方向交叉（yes/ask/no，逐家）",
    },
    "job_profile": {
        "path": "job_profile_cache.json", "kind": "derived",
        # 2026-11-16 补登记。之前它根本不在 REGISTRY 里，于是 code_fp 退化成
        # md5("1|") 这个常量 —— 169 条画像缓存**永远不会因为改代码而失效**。
        # 这是最坏的一种失效：改了 prompt 结论全变，缓存却照样命中，没人发现。
        # 现在依赖 job_profile.py 与 llm.py（传输层），改哪个都会正确作废。
        "deps": ["job_profile.py", "llm.py"],
        "desc": "企业岗位画像（逐家，模型 + 校验链）",
    },
    "hard_filter": {
        "path": "hard_filter_cache.json", "kind": "derived",
        "deps": ["hard_filter.py", "demo_v2/rules.py"],
        "desc": "学生自由填空（只要南京/只看互联网）解析结果",
    },
    "hit_verify": {
        "path": "hit_verdicts.json", "kind": "derived",
        # 只依赖提示词文件！缓存里存的是**模型原判**，融合在 hit_verify.verify_pairs
        # 读取时用当前规则现算。所以：
        #   改 PROMPT（hit_prompt.py）      → 模型要答的东西变了，必须重问；
        #   改 fuse / blocks / _general_enough → 只是融合变，原判照样有效；
        #   改 intent_semantics / job_family   → 规则变，融合跟着变，也不必重问。
        # 早先这里含 hit_verify.py 自身，改一行融合就整份缓存作废、重问模型，
        # 而模型会抖（同一对出现过 1 和 0.5），命中家数在 19 / 13 之间跳。
        "deps": ["hit_prompt.py"],
        "desc": "本地规则候选命中的大模型批量复核（逐对三态，构建期算好）",
    },
    "lookup": {
        "path": "lookup_cache.json", "kind": "snapshot",
        "deps": ["portal.py"],
        "desc": "企业检索联网抓取快照（改代码不重抓）",
    },
    "llm_match": {
        "kind": "db", "table": "llm_match",
        "deps": ["fair_bridge.py", "intent_semantics.py", "job_level.py",
                 "demo_v2/rules.py", "job_family.py"],
        "desc": "模型判定的 hit/inferred/miss（历史画像生成，需按画像核对）",
    },
    "fair_payload": {
        "path": "fair_payload.json", "kind": "artifact",
        "deps": ["fair_bridge.py", "intent_semantics.py", "job_level.py", "unknown_infer.py",
                 "biz_fit.py", "hard_filter.py", "demo_v2/rules.py", "demo_v2/core.py",
                 # 族表改了，pair_verdict 的跨职能判定就变了，命中必须重算；
                 # 漏登记会让 payload 显示「新鲜」，实际沿用旧结论。
                 "job_family.py",
                 # 与 hit_verify 缓存相反：融合逻辑（fuse）和提示词都会改变 payload
                 # 里的融合结论，所以这两个都要登记。hit_prompt 变了会触发重问，
                 # 重问结果也要落进 payload。
                 "hit_verify.py", "hit_prompt.py"],
        "desc": "三端合并后的页面数据产物",
    },
}

_FP_MEMO: dict[str, str] = {}
# 本文件记录了 key 的构造方式。若以后改 key 算法（加字段/换哈希），把它 +1，
# 让所有旧缓存一起失效；否则新旧 key 空间混在一起，又会出现"命中但含义不同"。
GUARD_VER = "1"


def _abs(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(HERE, rel)


def file_fp(rel: str) -> str:
    """单文件语法树指纹；改注释/空行不失效，改逻辑或字面量（含提示词）必失效。"""
    path = _abs(rel)
    if path in _FP_MEMO:
        return _FP_MEMO[path]
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except Exception:
        _FP_MEMO[path] = "missing"
        return "missing"
    try:
        blob = ast.dump(ast.parse(src), annotate_fields=True, include_attributes=False)
    except SyntaxError:
        blob = src          # 语法错误的中间态：退回原文，宁可失效也不留脏
    fp = hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]
    _FP_MEMO[path] = fp
    return fp


def code_fp(deps=None, name: str = "") -> str:
    """依赖模块的组合指纹。deps 省略时按缓存名取登记表。

    注意第一个位置参数是 deps、不是 name。历史上有人写成 code_fp("xxx")，
    于是字符串被逐字符当成文件名、全部 missing，指纹退化成常量、缓存永不失效。
    这里把「字符串 deps」宽容地当成 name，避免再踩同一个静默坑。
    """
    if isinstance(deps, str) and not name:
        deps, name = None, deps
    if deps is None:
        deps = (REGISTRY.get(name) or {}).get("deps") or []
    raw = GUARD_VER + "|" + "|".join("%s:%s" % (d, file_fp(d)) for d in deps)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# 影响「一次 build 产出的 payload」的全部上游模块。payload 顶层记一份该指纹，
# 服务端据此判断盘上 payload 是否由当前代码生成——只比 active_fair_id 会漏掉
# 「代码改了但画像没换」这种最常见、也最难察觉的陈旧场景。
BUILD_DEPS = [
    "fair_bridge.py", "payload_contract.py",
    "job_recommend.py", "major_l1_authority.py", "biz_recheck.py",
    "intent_semantics.py", "hit_verify.py", "ind_code.py", "job_profile.py",
    "hard_filter.py", "biz_fit.py", "job_family.py", "job_direction.py",
    "job_level.py", "unknown_infer.py",
]


def build_fp() -> str:
    """一次 build 全部上游模块的组合指纹（payload 里记一份，供陈旧判定）。"""
    return code_fp(BUILD_DEPS)


def key(name: str, *parts, deps=None) -> str:
    """缓存 key：缓存名 + 代码指纹 + 业务参数。业务参数请传可 json 序列化的值。"""
    payload = json.dumps([name, code_fp(deps, name)] + list(parts),
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:24]


def stamp(name: str, model: str = "", extra=None) -> dict:
    """写进缓存文件的元信息，供 status / 自动校验使用。"""
    return {
        "name": name,
        "fp": code_fp(name=name),
        "model": model or "",
        "schema": (REGISTRY.get(name) or {}).get("schema", 1),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "extra": extra or {},
    }


def _short_model(tag: str) -> str:
    """部分模块的 model tag 是 base|model|ver 形式，展示时只留模型名。"""
    tag = tag or ""
    parts = [p for p in tag.split("|") if p]
    return parts[-2] if len(parts) >= 2 and "://" in parts[0] else tag


def rel(name: str) -> str:
    p = (REGISTRY.get(name) or {}).get("path")
    return os.path.join(DATA, p) if p else ""


def check_file(name: str) -> tuple[bool, str]:
    """(是否可信, 说明)。只看文件里记的指纹，不校验业务参数。"""
    path = rel(name)
    if not path or not os.path.isfile(path):
        # 没文件 ≠ 脏：很多缓存是懒生成的，用到时才建。
        return True, "尚无缓存（用到时自动生成）"
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception as exc:
        return False, "读取失败：%s" % str(exc)[:60]
    cg = obj.get("_cg") if isinstance(obj, dict) else None
    note = ""
    if not cg and isinstance(obj, dict):
        # 容器型缓存：顶层是 {业务key: 条目}，指纹记在各条目里（如推荐池）。
        # 必须逐条比对——只取时间戳最新的一条会放过混合代际文件：
        # 实测曾出现同一份池里 18 种不同 fp，只要最新那条匹配就整份判可信。
        subs = [v.get("_cg") for v in obj.values()
                if isinstance(v, dict) and v.get("_cg")]
        if subs:
            want = code_fp(name=name)
            stale = [s for s in subs if s.get("fp") != want]
            note = "，%d/%d 条为本版生成" % (len(subs) - len(stale), len(obj))
            if stale:
                return False, ("容器内 %d/%d 条不是本版生成（混合代际），"
                               "应归档后重算" % (len(stale), len(obj)))
            cg = max(subs, key=lambda x: x.get("ts") or "")
    if not cg:
        return False, "旧格式（无指纹），无法确认是否本版代码生成"
    want = code_fp(name=name)
    if cg.get("fp") != want:
        return False, "代码已变（缓存 %s ≠ 当前 %s）" % (str(cg.get("fp"))[:6], want[:6])
    ts = cg.get("ts") or ""
    age = ""
    try:
        t0 = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
        age = "，%.0f 小时前" % ((time.time() - t0) / 3600)
    except Exception:
        pass
    return True, "指纹一致%s%s%s" % (
        note, ("，模型 " + _short_model(cg["model"])) if cg.get("model") else "", age)


# ── 数据库表缓存 ────────────────────────────────────────────────
def _ensure_meta(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cache_meta(
        name TEXT PRIMARY KEY, fp TEXT, model TEXT, extra TEXT,
        ts TEXT, rows INTEGER)""")


def db_check(name: str) -> tuple[bool, str]:
    info = REGISTRY.get(name) or {}
    table = info.get("table")
    if not table:
        return False, "未登记表名"
    try:
        with repository.connect(DB) as con:
            _ensure_meta(con)
            row = con.execute("SELECT fp, model, ts, rows FROM cache_meta WHERE name=?",
                              (name,)).fetchone()
            cnt = con.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
    except Exception as exc:
        return False, "读取失败：%s" % str(exc)[:60]
    if not row:
        return (False, "表 %s 共 %d 行，早于本机制、无生成指纹：岗位名/理由不采信，"
                       "已按当前语义层重算" % (table, cnt))
    fp, model, ts, rows = row
    if fp != code_fp(name=name):
        return (False, "表 %s 由旧版逻辑生成（指纹 %s ≠ 当前 %s），仅作参考"
                % (table, str(fp)[:6], code_fp(name=name)[:6]))
    return True, "表 %s 共 %d 行，指纹一致%s" % (table, cnt, ("，生成于 " + ts) if ts else "")


def db_stamp(name: str, rows: int = 0, model: str = "", extra=None):
    info = REGISTRY.get(name) or {}
    table = info.get("table")
    if not table:
        return
    with repository.connect(DB) as con:
        _ensure_meta(con)
        con.execute("INSERT OR REPLACE INTO cache_meta(name,fp,model,extra,ts,rows)"
                    " VALUES(?,?,?,?,?,?)",
                    (name, code_fp(name=name), model or "",
                     json.dumps(extra or {}, ensure_ascii=False),
                     time.strftime("%Y-%m-%d %H:%M:%S"), rows))
        con.commit()


# ── 只读种子 ────────────────────────────────────────────────────
# 这些活缓存不入库（*.seed 才入库）：服务一处理请求就会改写它们。
# 与 REGISTRY 的区别——REGISTRY 管「体检」（该不该失效），
# SEEDS 管「预置」（clone 后要不要自带一份结论）。两者目的不同，别合并。
SEEDS = [
    "intent_semantics_cache.json",     # 意向↔岗位名语义映射（重建的核心依据）
    "intent_semantics_live.json",      # 前台按需补判的小缓存
    "job_recommend_pool.json",         # 按专业生成的校招岗位池
    "unknown_infer_cache.json",        # 无具体岗企业的意向推断
    "biz_fit_cache.json",              # 企业主营业务与方向的交叉判断
    "job_profile_cache.json",          # 企业岗位画像（169 条，重建最贵的一份）
    "hit_verdicts.json",               # 命中复核的模型原判
    "lookup_cache.json",               # 企业检索联网快照（重抓成本高）
]


def _seed_fp_ok(seed_path: str, name: str) -> bool:
    """种子里的指纹是否就是当前代码生成的那一版。

    无指纹的旧格式一律判 False —— 宁可首次构建重算，也不把来历不明的
    旧结论当成「本版结果」塞给用户（历史上旧推荐池就是这么「复活」的）。
    """
    try:
        with open(seed_path, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return False
    want = code_fp(name=name)
    cg = obj.get("_cg") if isinstance(obj, dict) else None
    if cg:
        return cg.get("fp") == want
    if isinstance(obj, dict):
        subs = [v.get("_cg") for v in obj.values()
                if isinstance(v, dict) and v.get("_cg")]
        if subs:
            return all(s.get("fp") == want for s in subs)
    return False


def restore_seeds() -> list:
    """活缓存缺失时从只读种子复制（与 careersafari.db.seed 同一套做法）。

    仓库只提交 X.seed（冻结的结论快照），运行时用同名活文件 X；活文件全部被
    .gitignore 排除。这样服务跑一遍 `git status` 不会脏——活库 careersafari.db
    已经用这个办法解决过同样的问题，缓存原来没跟上：

      · 缓存是派生结果，服务一处理请求就会重写它；
      · 一旦被 git 跟踪，clone 出来什么都没干就已经是脏的，新人第一反应
        一定是「我是不是搞坏了」，然后 `git checkout` 把刚算出的结论撤掉。

    只在活文件**不存在**时复制：已存在的活文件不会被种子覆盖，所以本地跑出来
    的新结论不会因为重启被回滚到旧快照。

    2026-11-18：恢复前先验指纹。种子由旧代码生成时（fp 对不上当前代码）
    直接跳过——否则 clone 后第一次启动就把旧结论当本版结果用，正是
    「改了代码仍看到旧推荐」的复现路径。
    """
    out = []
    if not os.path.isdir(DATA):
        return out
    by_file = {v.get("path"): n for n, v in REGISTRY.items() if v.get("path")}
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith(".seed") or fn.endswith(".db.seed"):
            continue          # 数据库的初始化在 fair_server 里单独做（有 malformed 防护）
        seed = os.path.join(DATA, fn)
        live = seed[:-len(".seed")]
        if os.path.exists(live):
            continue
        name = by_file.get(fn[:-len(".seed")])
        if name:
            kind = (REGISTRY.get(name) or {}).get("kind")
            # snapshot 是外部快照，代码变不作废；其余派生缓存必须指纹对得上。
            if kind != "snapshot" and not _seed_fp_ok(seed, name):
                continue      # 旧代码生成的种子：不恢复，让首次构建按当前代码重算
        try:
            shutil.copy2(seed, live)
            out.append(fn[:-len(".seed")])
        except Exception:
            pass
    return out


def freeze_seeds(names=None) -> list:
    """把当前活缓存冻结成只读种子（发布前用）。返回被冻结的文件名。

    种子必须与代码同版本：里面的 _cg.fp 是「生成这份结论的代码」的指纹，
    代码一变指纹就不符，clone 后第一次构建会自动判失效重算——这是对的，
    不该继续沿用旧结论。
    """
    out = []
    for fn in (names or SEEDS):
        path = os.path.join(DATA, fn)
        if not os.path.isfile(path):
            continue
        try:
            shutil.copy2(path, path + ".seed")
            out.append(fn + ".seed")
        except Exception:
            pass
    return out


# ── 归档 ────────────────────────────────────────────────────────
def archive(name: str, keep: int = 6) -> str:
    """把失效缓存移到 data/_stale_cache/，可回溯，不直接删。"""
    path = rel(name)
    if not path or not os.path.isfile(path):
        return ""
    os.makedirs(STALE, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(STALE, "%s__%s.json" % (name, ts))
    try:
        shutil.move(path, dst)
    except Exception:
        return ""
    olds = sorted(f for f in os.listdir(STALE) if f.startswith(name + "__"))
    for f in olds[:-keep] if keep else []:
        try:
            os.remove(os.path.join(STALE, f))
        except Exception:
            pass
    return dst


def purge(names=None, keep: int = 6) -> list:
    names = names or [n for n, v in REGISTRY.items()
                      if v.get("kind") in ("derived",)]
    out = []
    for n in names:
        if n in REGISTRY and REGISTRY[n].get("kind") == "db":
            continue
        dst = archive(n, keep=keep)
        if dst:
            out.append((n, dst))
    return out


def purge_invalid(keep: int = 6) -> list:
    """只归档「指纹对不上 / 无指纹」的派生缓存，其余保留。

    在 build 开头调用：改过代码就把上一代结果挪进 _stale_cache（可回溯），
    不留在原地冒充新结果，也不用人工 rm。
    """
    moved = []
    for n, info in REGISTRY.items():
        if info.get("kind") != "derived":
            continue
        ok, _why = check_file(n)
        if ok:
            continue
        dst = archive(n, keep=keep)
        if dst:
            moved.append((n, dst))
    return moved


# ── 体检报告 ────────────────────────────────────────────────────
_ICON = {True: "OK  ", False: "STALE"}


def status(verbose: bool = True) -> dict:
    """返回 {name: {ok, why, kind, desc}}，verbose 时顺带打印。"""
    res = {}
    lines = []
    for name, info in REGISTRY.items():
        kind = info.get("kind", "derived")
        if kind == "db":
            ok, why = db_check(name)
        elif kind == "artifact":
            path = rel(name)
            if not os.path.isfile(path):
                ok, why = False, "尚未生成"
            else:
                ts = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(os.path.getmtime(path)))
                stale = [d for d in info.get("deps", [])
                         if os.path.getmtime(_abs(d)) > os.path.getmtime(path)]
                ok = not stale
                why = ("生成于 " + ts) + ("；依赖已改动：" + "、".join(stale) if stale else "")
        elif kind == "snapshot":
            path = rel(name)
            ok = os.path.isfile(path)
            why = ("快照 %d KB" % (os.path.getsize(path) // 1024)) if ok else "无快照"
        else:
            ok, why = check_file(name)
        res[name] = {"ok": ok, "why": why, "kind": kind, "desc": info.get("desc", "")}
        lines.append("  %-18s %-7s %s" % (name, _ICON[ok], why))

    if verbose:
        print("缓存体检（代码指纹 = 依赖模块语法树；改逻辑即失效）")
        print("\n".join(lines))
        bad = [n for n, v in res.items() if not v["ok"] and v["kind"] == "derived"]
        if bad:
            print("  失效：" + "、".join(bad) + "（下次使用会自动重建并归档旧文件）")
    return res


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "status":
        status()
        return 0
    if cmd == "purge":
        names = argv[2:] or None
        moved = purge(names)
        if not moved:
            print("没有可归档的缓存文件。")
        for n, p in moved:
            print("归档 %-18s -> %s" % (n, os.path.relpath(p, HERE)))
        return 0
    if cmd == "fp":
        for n in (argv[2:] or list(REGISTRY)):
            print("%-18s %s  deps=%s" % (n, code_fp(name=n), REGISTRY[n].get("deps")))
        return 0
    if cmd == "freeze":
        out = freeze_seeds(argv[2:] or None)
        print("已冻结 %d 份种子：%s" % (len(out), "、".join(out) or "（无）"))
        print("种子要与代码同版本提交，否则 clone 后第一次构建会判失效重算。")
        return 0
    if cmd == "seeds":
        for fn in SEEDS:
            live = os.path.join(DATA, fn)
            seed = live + ".seed"
            print("  %-32s 活%s 种子%s" % (
                fn,
                "有" if os.path.isfile(live) else "无",
                "有" if os.path.isfile(seed) else "无"))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
