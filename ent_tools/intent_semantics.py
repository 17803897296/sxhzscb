# -*- coding: utf-8 -*-
"""匹配端 · 岗位语义符合度（大模型推断）。

学生在采集端勾选的 intended_jobs 与企业校招公告里的真实岗位名往往字面不同，
旧的 matchIntent 只做 22 个关键词的字面交集，导致报告里显示的「命中岗位」
和学生勾选的岗位看起来没关系。

这里在 fair_bridge.build() 时用 GLM-4-flash 判断「企业岗位名」与「意向岗位」
是否属于同一类校招工作，把结果写进 payload 顶层 `intent_semantics`，前端据此
显示「语义命中：企业岗位 X ↔ 你选的 Y」。

- 每个意向岗位单独一次短调用（只让它从清单里挑岗位名），输出小、不易超时。
- 结果按 (意向岗位组合 + 岗位名集合) 缓存，避免每次 build 都调模型。
- 模型未配置 / 超时 / 解析失败 -> 沿用上次成功判定；仍没有则返回 {}，
  前端自动退回原有字面/关键词匹配，绝不编造。
- 只做「岗位语义」判断，不改企业事实字段，也不碰 calcOne 打分。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "intent_semantics_cache.json")

PROMPT = """你是校招岗位语义匹配助手。
学生意向岗位：{intent}
下面是本次双选会企业校招公告里出现的岗位名（去重清单）：{titles}

请从清单里挑出与「{intent}」属于同一类校招工作、应届生可以同样投递的岗位名。
要求：
1. 只能写**清单里出现过的原词**，不要自己造新岗位名；最多 15 个，按相符程度排序。
2. 判定要严格：职责与专业要求确实是同类工作才算相符。宁可少挑，不要凑数。
   「运营培训生（综合柜员方向）」不算「商业分析」；「管培生」不自动等于「供应链计划」；
   「护士」不算「管培生」；「前端开发工程师」不算「供应链计划」；
   「算法工程师」不必然等于「数据分析师」。
3. 不要输出任何解释文字。
4. 严格输出 JSON：{{"titles":["岗位名", "岗位名"]}}
"""


def _load_env():
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _candidate_titles(titles, intended, limit=240):
    """语义无关的初筛：只把「可能和意向岗位沾边」的岗位名送进模型，压短 prompt。

    纯字符粗筛，不做语义判断；宁可多留，也不把真正相关的岗位名筛掉。
    """
    core = ["管培", "培训生", "专员", "分析师", "工程师", "顾问", "助理", "储备",
            "数据", "分析", "供应链", "计划", "采购", "物流", "运营", "项目",
            "商业", "经营", "风控", "咨询", "市场", "财务", "产品", "管理",
            "支持", "研究", "策略", "质量", "交付", "实施"]
    keys = set(core)
    for it in intended:
        for i in range(max(0, len(it) - 1)):
            keys.add(it[i:i + 2])
    scored = []
    for t in titles:
        n = sum(1 for k in keys if k in t)
        if n:
            scored.append((n, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return sorted(t for _, t in scored[:limit])


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def _call(cfg, prompt, timeout):
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.2}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return _parse_json(raw["choices"][0]["message"]["content"])


def build_map(intended, all_titles, timeout=45):
    """返回 {意向岗位: {titles, terms, reason, via}}；失败返回上次判定或 {}。"""
    intended = [str(x).strip() for x in (intended or []) if str(x).strip()]
    titles = sorted({str(t).strip() for t in (all_titles or []) if str(t).strip()})
    if not intended or not titles:
        return {}
    digest = hashlib.md5(
        ("|".join(sorted(intended)) + "||" + "|".join(titles)).encode("utf-8")
    ).hexdigest()
    cached_map = {}
    if os.path.isfile(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            cached_map = cached.get("map") or {}
            if cached.get("key") == digest and cached_map:
                return cached_map
        except Exception:
            pass

    cfg = _load_env()
    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")):
        return cached_map
    short = _candidate_titles(titles, intended)
    pool = "、".join(short or titles[:240])

    out = {}
    for it in intended:
        try:
            data = _call(cfg, PROMPT.format(intent=it, titles=pool), timeout)
            tl = [str(x).strip() for x in ((data or {}).get("titles") or [])
                  if str(x).strip()][:15]
        except Exception as exc:
            # 偶发超时/断网：沿用上一次成功的大模型判定，别让功能直接退化成字面匹配。
            print("[intent_semantics] %s 判定失败，沿用上次结果：%s" % (it, exc))
            return cached_map or {}
        out[it] = {"titles": tl, "terms": [],
                   "reason": "大模型判定为同类校招岗位", "via": "llm"}

    if not any(v["titles"] for v in out.values()):
        return cached_map or {}
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"key": digest, "map": out}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out
