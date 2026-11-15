# -*- coding: utf-8 -*-
"""构建期批量复核（hit verification）：本地规则判成命中的候选对，交大模型成批判一次。

为什么要有它
    本地规则（共享 2 字核心词 / 同职能族）会把「食品研发工程师」判成「药品研发工程师」
    的命中——两者只共享「研发」。大模型在建库时只生成过语义映射候选词，从来没有逐对
    判过这类兜底命中，所以页面上会出现「共享字就命中」。

为什么它不会拖慢构建
    · 只判「本地规则认为可能命中」的候选对（当前 126 对），不扫 535×18 全量组合；
    · 一次调用判完一批（126 对实测约 2.4s），输出紧凑「序号:0/1」，不产大 JSON；
    · pair 级缓存：同一对 + 同一份代码指纹只判一次，第二次 0 次调用；
    · 构建期算好写进 payload，页面打开零等待。

缓存失效
    走 cache_guard REGISTRY["hit_verify"]，key 含 hit_verify.py / intent_semantics.py /
    job_level.py 的语法树指纹。改任何一行判定逻辑，旧结论自动作废，不会静默沿用。

失败策略
    模型不可用 / 超时 / 输出缺行 → 该对不出现在结果里，前端按「未复核」如实标注，
    继续用本地规则，不编结论、不把「没复核」说成「复核过」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request

import cache_guard
import intent_semantics as IS

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "hit_verdicts.json")

# 一批判多少对。126 对实测 2.4s；留余量压到 160，既少往返又不至于 prompt 过长。
BATCH = 160
# 候选对上限：超过就只判前 N 对（按意向/岗位名排序稳定），其余标未复核，
# 绝不悄悄截断后当成「已复核」。
MAX_PAIRS = 640

PROMPT = """你在复核校招岗位匹配。下面每行是「企业真实岗位名 × 学生意向岗位」。
判断每一对是否属于同一类校招工作、应届生可以同样投递。

只按岗位名判断，不看学生学过什么课程。
只共享通用词（研发 / 工程 / 专员 / 管培生 / 经理 / 助理 / 技术 / 实验 / 分析 / 销售）不算同类。
- 学科教师必须同学科；同一学科不同学段（高中 / 初中 / 小学）算同类。
- 同一职能但业务方向明显不同（药品研发 vs 食品研发、电气测试 vs 光学测试、
  药物分析 vs 质量研究）不算同类。
- 设计类岗位按**设计对象**分方向：机械设计 / 结构设计 / 模具设计属机械结构方向；
  电磁设计 / 电气设计 / 电路设计属电气方向；光学设计、外观造型设计、仿真热设计
  各自是不同方向。只共享「设计工程师」不等于同一类岗位。
- 岗位名里并列多个档位（「工艺工程师/主管/经理」「质量工程师/资深」）时，按其中
  最接近应届的一档判断，不要因为出现主管 / 经理 / 资深就判 0。
- 质量职能内部不按细分再切：质量技术 / 供应商品质 / 品质体系 / 客诉质量都是质量岗
  的叫法差异，算同类；只有跨职能（质量 vs 研发 / 工艺 / 销售）才算不同类。
- 「X管理员 / X管理岗」是现场或设备管理岗，不是设计 / 研发岗：机械管理员、
  机械管理岗 与 机械设计工程师 不算同类；但 设备管理员 与 设备工程师 算同类
  （同一台设备的运行维护）。
- 岗位档位比意向高一级（主管 / 资深 / 高级 / 经理 / 总监 / 专家）不算同类。
- 销售岗不算技术 / 专业 / 职能类意向。
- 专业名、学科名、招聘批次名不是岗位名，一律不算。

逐行输出，每行只写「序号:1」（算同类）或「序号:0」（不算同类）。
不要解释、不要 JSON、不要多余文字、不要漏行。

%s"""


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


def _parse(txt, n):
    """把「序号:0/1」逐行解析成 {序号: 0|1}。只认 0/1，缺行不补、不猜。"""
    out = {}
    for line in str(txt or "").splitlines():
        m = re.match(r"\s*(\d+)\s*[:：.\-]?\s*([01])\b", line)
        if m:
            i = int(m.group(1))
            if 0 <= i < n and i not in out:
                out[i] = int(m.group(2))
    if len(out) < n:
        # 兜底：模型把多对挤在一行（「1:1 2:0 3:1」）时再扫一遍
        for m in re.finditer(r"(\d+)\s*[:：]\s*([01])(?=\s|$)", str(txt or "")):
            i = int(m.group(1))
            if 0 <= i < n and i not in out:
                out[i] = int(m.group(2))
    return out


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
            "model": "", "failed": 0}
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
        if k and rec.get("k") == k and rec.get("v") in (0, 1):
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
        if rec.get("v") in (0, 1):
            verdicts[it + "||" + t] = rec["v"]
    meta["verified"] = len(verdicts)
    return verdicts, meta


def build_verdicts(intents, titles, timeout=120):
    """返回 (verdicts, meta)。

    verdicts: {"意向||岗位名": 0|1}——只含真正判过的对；没判的键不存在。
    meta:     {"total": 候选对数, "asked": 本次送判数, "cached": 命中缓存数,
               "verified": 有结论数, "model": 模型名, "failed": 失败批次数}
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
