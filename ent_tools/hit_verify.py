# -*- coding: utf-8 -*-
"""构建期批量复核（hit verification）：本地规则判成命中的候选对，交大模型成批判一次。

为什么要有它
    本地规则（共享 2 字核心词 / 同职能族）会把「食品研发工程师」判成「药品研发工程师」
    的命中——两者只共享「研发」。大模型在建库时只生成过语义映射候选词，从来没有逐对
    判过这类兜底命中，所以页面上会出现「共享字就命中」。

为什么它不会拖慢构建
    · 只判「本地规则认为可能命中」的候选对（当前 126 对），不扫 535×18 全量组合；
    · 一次调用判完一批（126 对实测约 2.4s），输出紧凑「序号:1/0.5/0」，不产大 JSON；
    · pair 级缓存：同一对 + 同一份代码指纹只判一次，第二次 0 次调用；
    · 构建期算好写进 payload，页面打开零等待。

缓存失效（为什么 key 只跟提示词走）
    cache_guard REGISTRY["hit_verify"] 的 deps 只指 hit_prompt.py。因为缓存里存的是
    **模型原判**，融合在读取时用当前规则现算：

        改 PROMPT                    → 模型要答的东西变了 → 重问
        改 fuse / blocks / _general_enough → 融合结论变，原判不用重答 → 不重问
        改 intent_semantics / job_family   → 规则变，融合跟着变 → 不重问

    早先 deps 含本文件自身，导致每改一次融合逻辑就归档整份 pair 缓存、重问一遍模型；
    而 temperature=0.1 仍会抖（同一对实测出现过 1 和 0.5），命中家数在 19 / 13 之间
    跳，构建不可复现。分开以后同一份缓存下融合结论只由代码决定，可复算。
    payload 侧则相反：fair_payload 的 deps 含本文件，融合逻辑一改 payload 必须重算。

失败策略
    模型不可用 / 超时 / 输出缺行 → 该对不出现在结果里，前端按「未复核」如实标注，
    继续用本地规则，不编结论、不把「没复核」说成「复核过」。

三态复核（为什么不是二分类）
    二分类逼模型在「同类」和「不同类」里选一个，于是「C++开发工程师 × 后端开发
    工程师」这种技术栈相邻、信息不足以下结论的对子，只能被判成 1 —— 直接变成
    误报（实测精确率 90.9%，但 40 对里 6 对是这种情况）。三态允许模型**弃权**：

        1    确认同类，可作为命中证据
        0.5  弃权：技术栈相邻 / 细分方向相邻 / 岗位名信息不足。不算命中证据，
             也不记为「已否决」，产物里保留为「待问」
        0    确认不同类，否决该对证据

    弃权比判错便宜：双选会现场学生按报告跑企业，误报要白跑一趟（3.5 分钟 +
    机会成本），漏报只是少看一家（名单里还有几十家）。代价函数按
    Cost = 3×FP + 1×FN 计，所以「不确定」一律不进命中证据。

    与本地规则融合见 fuse()：规则管确定性事实（跨职能 / 跨专业 / 档位），
    模型管需要常识的族内细分；任何一方不确定时都不硬给结论。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request

import cache_guard
import intent_semantics as IS
from hit_prompt import PROMPT

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "hit_verdicts.json")

# 一批判多少对。126 对实测 2.4s；留余量压到 160，既少往返又不至于 prompt 过长。
BATCH = 160
# 候选对上限：超过就只判前 N 对（按意向/岗位名排序稳定），其余标未复核，
# 绝不悄悄截断后当成「已复核」。
MAX_PAIRS = 640

# 提示词在 hit_prompt.py：它是缓存 key 的唯一依据（改它才重问模型，
# 改本文件的融合逻辑不重问）。导入即可，别在这里手改字符串。


def _raw_call(cfg, prompt, timeout):
    """原样返回模型文本（复核输出是行文本，不是 JSON，不能复用 IS._call）。"""
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.1}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]


# 复核结论的三种取值。0.5 是「弃权」，不是「半分」——见文件头「三态复核」。
VERDICTS = (0, 0.5, 1)


def _general_enough(title, intent):
    """岗位名是否比意向更泛（大类岗，同族意向都能投）。

    这是**包含关系**，属确定性事实，规则说了算：模型在「认泛」上弱（40 对实测
    漏判全集中在这一类），它弃权时不该由它决定。
    大类岗名（管培生 / 储备干部 / 研究员）例外——那类不能反推小类，由 align_jobs
    走「现场问一句」，不升级成命中。
    """
    if not title or not intent:
        return False
    try:
        # 轮岗类（管培生 / 培训生 / 储备干部）与纯大类名不能反推小类：
        # 「技术管培生 × 后端开发」不是对口证据，只能现场问一句。
        if IS.is_bare_generic(title) or IS.is_rotation_job(title):
            return False
        # 意向本身没方向词（初级软件工程师 / 开发工程师）：同职能的任何细分方向
        # 都可投，这是 pair_verdict「意向没方向 → 收所有细分」的原有约定。模型对
        # 「嵌入式软件工程师 × 初级软件工程师」这类组合会弃权，不能让它推翻规则。
        # 跨职能的已在 pair_verdict 被挡掉，不会走到这里。
        if not IS._dir_core(intent)[0]:
            return not _cross(title, intent)
        return bool(IS.is_wider_job(title, intent)) or not IS._dir_core(title)[0]
    except Exception:
        return False


def _cross(title, intent):
    """跨职能判定，单独包一层：族表异常时不要让整个复核流程崩掉。"""
    try:
        import job_family
        return job_family.cross_function(title, intent)
    except Exception:
        return False


def _same_dir(title, intent):
    """同族同方向的细分说法 → 同一岗。判据与 align_jobs 共用一份（IS.same_dir）。

    两处必须同一个判据：align_jobs 决定这一岗进不进命中候选，fuse 决定模型给出
    原判后放不放行。两边各写一套就会重演「页面说命中、后端说不对口」的老问题。
    """
    try:
        return IS.same_dir(title, intent)
    except Exception:
        return False


def fuse(rule, model, title="", intent=""):
    """把本地规则三态与模型三态合成最终结论。返回 None = 未复核（交回本地规则）。

    分层原则：规则管确定性事实（跨职能 / 跨专业 / 档位 / 并列方向 / 更泛），模型
    管需要常识的族内细分。任何一方不能确定时都不硬给结论——弃权（0.5）比判错便宜。

    | 规则  | 模型 | 融合 | 理由                                        |
    |-------|------|------|---------------------------------------------|
    | no    | 1    | 0.5  | 多半是词表没覆盖某种叫法（服务端→后端），    |
    |       |      |      | 不硬压成否，也不放行成命中                   |
    | no    | 0/.5 | 0    | 双方都不认为同类                            |
    | ask   | 1/.5 | 1/0.5| 泛研发意向撞带方向岗位（研发工程师 × C++研发）：同族同向   |
    |       |      |      | 的细分说法放行，换对象的（器件测试 × 测试）弃权              |
    | ask   | 0    | 0    | 双方都否决                                  |
    | ok    | 1    | 1    | 双证据                                      |
    | ok    | .5   | 1/0.5| 岗位比意向更泛（开发工程师 × 后端）或同族同 ▸ |
    |       |      |      | 方向 → 规则裁定；其余弃权不进证据            |
    | ok    | 0    | 0    | 模型明确否认（挡错是它的强项，精确率 90.9%） |
    """
    if model is None:
        return None
    if rule == "no":
        return 0.5 if model == 1 else 0
    if rule == "ask":
        if model == 0:
            return 0
        # ask 只剩「泛研发意向 × 带方向岗位」这一种（2026-11-17 删「更细≠同岗」后，
        # 大类进小类在规则层已是 ok，不会走到这里）。原约定「确认同类也不升格」，
        # 但换意向实测发现这条把 AI算法工程师 / 数据分析岗 这类同名同族的岗也挡在
        # 证据外——既不命中、也不进「待问」，静默消失。同族 + 方向核包含 + 意向核
        # 不是职责词时放行；其余（器件测试 × 测试、电商运营 × 运营）继续弃权。
        try:
            if _same_dir(title, intent) and not IS.is_rotation_job(intent):
                return 1
        except Exception:
            pass
        return 0.5
    if model == 1:
        return 1
    if model == 0.5:
        return 1 if (_general_enough(title, intent) or _same_dir(title, intent)) else 0.5
    return 0


def _parse(txt, n):
    """把「序号:1 / 序号:0.5 / 序号:0」逐行解析成 {序号: 0|0.5|1}。

    只认这三个值，缺行不补、不猜（缺行 = 该对未复核，前端如实标注）。
    """
    out = {}
    for line in str(txt or "").splitlines():
        m = re.match(r"\s*(\d+)\s*[:：.\-]?\s*(\.5|[01](?:\.5)?)\b", line)
        if m:
            i = int(m.group(1))
            if 0 <= i < n and i not in out:
                out[i] = _num(m.group(2))
    if len(out) < n:
        # 兜底：模型把多对挤在一行（「1:1 2:0 3:0.5」）时再扫一遍
        for m in re.finditer(r"(\d+)\s*[:：]\s*(\.5|[01](?:\.5)?)(?=\s|$)", str(txt or "")):
            i = int(m.group(1))
            if 0 <= i < n and i not in out:
                out[i] = _num(m.group(2))
    return out


def blocks(v):
    """这个复核结论是否挡掉命中证据。

    0（否决）和 0.5（弃权）都挡——「不确定」不进证据，也不冒充「复核过」。
    None（未复核）不挡：交回本地规则，前端按「未复核」如实标注。
    """
    return v in (0, 0.5)


def _num(s):
    """「0.5」→ 0.5，「1」→ 1（整数保持 int，缓存里好读）。"""
    s = str(s)
    return 0.5 if s.endswith(".5") else int(s)



def candidate_pairs(intents, titles):
    """本地规则认为「可能命中 / 待问」的对子，作为复核候选。

    用与页面同源的两道闸：_share_core（共享核心词或同职能族）+ pair_verdict
    （学科 / 档位 / 方向三道常识互斥）。这里只做**超集**，判不判得中交给模型，
    所以宁可多留几个，也不让真命中漏过复核。
    """
    intents = [str(x).strip() for x in (intents or []) if str(x).strip()]
    titles = sorted({str(t).strip() for t in (titles or []) if str(t).strip()})
    pairs = []
    for it in intents:
        for t in titles:
            if (IS._share_core(it, t) or it in t or (t in it and len(t) >= 4)) \
                    and IS.pair_verdict(t, it) != "no":
                pairs.append((t, it))
    pairs.sort(key=lambda p: (p[1], p[0]))
    return pairs[:MAX_PAIRS]


def _load_cache(fp):
    """只认同一份代码指纹的旧结论；指纹不符就当没有（自动失效，不静默沿用）。"""
    if not os.path.isfile(CACHE):
        return {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            old = json.load(f)
        if (old.get("_cg") or {}).get("fp") != fp:
            return {}
        items = old.get("items") or {}
        return items if isinstance(items, dict) else {}
    except Exception:
        return {}


def _save_cache(fp, items, model):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"_cg": cache_guard.stamp("hit_verify", model=model),
                       "items": items}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def verify_pairs(pairs, timeout=120):
    """按给定的 (岗位名, 意向) 对复核，返回 (verdicts, meta)。

    与 build_verdicts 的区别：不生成候选、不做本地粗筛，只判传进来的对。
    给「现场临时填的意向」用——payload 里没有这些意向的预计算结论，
    页面按需补一次，结果照样进同一个 pair 缓存。
    """
    pairs = [(str(t).strip(), str(it).strip()) for t, it in (pairs or [])
             if str(t).strip() and str(it).strip()]
    # 去重，保持稳定顺序
    pairs = list(dict.fromkeys(pairs))[:MAX_PAIRS]
    meta = {"total": len(pairs), "asked": 0, "cached": 0, "verified": 0,
            "abstain": 0, "model": "", "failed": 0}
    if not pairs:
        return {}, meta

    fp = cache_guard.code_fp(name="hit_verify")
    items = _load_cache(fp)
    cfg = IS._load_env()
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    meta["model"] = model
    have_key = bool(os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", ""))

    todo = []          # [(t, it, key)]
    for t, it in pairs:
        k = cache_guard.key("hit_verify", it, t) if have_key else None
        rec = items.get(it + "||" + t) or {}
        if k and rec.get("k") == k and rec.get("v") in VERDICTS:
            meta["cached"] += 1
        else:
            todo.append((t, it, k))

    meta["asked"] = len(todo)
    dirty = False
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        prompt = PROMPT % "\n".join(
            "%d. 企业岗位「%s」× 学生意向「%s」" % (i, t, it)
            for i, (t, it, _) in enumerate(chunk))
        try:
            txt = _raw_call(cfg, prompt, timeout)
            got = _parse(txt, len(chunk))
        except Exception as exc:
            print("[hit_verify] 复核失败（%d 对）：%s" % (len(chunk), exc))
            meta["failed"] += 1
            continue
        for i, (t, it, k) in enumerate(chunk):
            if i in got and k:
                items[it + "||" + t] = {"k": k, "v": got[i]}
                dirty = True

    if dirty:
        _save_cache(fp, items, model)

    verdicts = {}
    for t, it in pairs:
        rec = items.get(it + "||" + t) or {}
        v = rec.get("v")
        if v not in VERDICTS:
            continue                      # 未复核，交回本地规则
        fv = fuse(IS.pair_verdict(t, it), v, t, it)
        if fv is None:
            continue
        verdicts[it + "||" + t] = fv
        if fv == 0.5:
            meta["abstain"] += 1
    meta["verified"] = len(verdicts)
    return verdicts, meta


def build_verdicts(intents, titles, timeout=120):
    """返回 (verdicts, meta)。

    verdicts: {"意向||岗位名": 0|0.5|1}——只含真正判过的对；没判的键不存在。
    0.5 = 最后确认不了（见 fuse）：不进命中证据，也不冒充「已否决」。
    meta:     {"total": 候选对数, "asked": 本次送判数, "cached": 命中缓存数,
               "verified": 有结论数, "abstain": 其中弃权数, "model": 模型名,
               "failed": 失败批次数}
    """
    pairs = candidate_pairs(intents, titles)
    verdicts, meta = verify_pairs(pairs, timeout)
    meta["total"] = len(pairs)
    return verdicts, meta


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    p = json.load(open(os.path.join(HERE, "data", "fair_payload.json"), encoding="utf-8"))
    ts = sorted({str(t).strip() for r in p["rows"] for t in (r.get("job_titles") or [])
                 if str(t).strip()})
    its = list((p.get("intent_semantics") or {}).keys())
    v, m = build_verdicts(its, ts)
    print(json.dumps(m, ensure_ascii=False))
    print("否决样例：")
    for i, (k, val) in enumerate(sorted(v.items())):
        if val == 0 and i < 25:
            print("   ", k)
