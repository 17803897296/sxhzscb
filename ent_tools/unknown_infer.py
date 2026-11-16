# -*- coding: utf-8 -*-
"""匹配端 · 无具体岗位企业的意向岗位符合度判定（大模型）。

库里有两类「无具体岗」企业，标签不同但匹配端一视同仁：
  未知1：早期只搜到主营业务介绍，有粗略描述，但没有真实岗位公告；
  未知2：联网检索（双引擎 + 招聘平台 / 高校就业网）仍未取得发布方岗位页。
 未知：来源本身就是「未知」（早期批次没留采集痕迹），同样 0 岗。

两者都不能当成「不对口」直接排除（「没查到 ≠ 不招」是既有红线），
统一交给大模型按**学生当前勾选的意向岗位**判断：按行业、主营业务、
单位性质，这家单位今年校招是否可能招这些岗位中的某几个。

- 只从学生勾选的意向岗位原词里挑，最多 2 个，绝不造新岗位名；
- 判断要克制：明显不匹配就给「无」，报告改成中性的「关联较弱，现场问一句」；
- 结果按 (意向岗位 + 企业清单指纹) 缓存，避免每次 build 都调模型；
- 模型未配置 / 超时 / 格式异常 -> 沿用上次判定；仍没有则 fallback，
  由 fair_bridge 退回中性话术，绝不编造。
输出用「行格式」而非 JSON：实测 GLM-4-flash 对多条 JSON 容易输出成
list-of-objects 或夹 markdown 围栏，行格式解析最稳；同时保留 JSON 兼容解析。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from intent_semantics import _parse_json
import cache_guard
import llm
from llm import load_env as _load_env

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "unknown_infer_cache.json")
# 逐家判，不批量：同一批里塞进不同行业的企业，会改变模型对单家的判断
# （实测「国联财务」在含 IT 企业的一批里被judged 可能招软件岗，单独判则是「无」）。
# 这类判定要可复现，宁可多调几次；结果有缓存，重建一次付一次代价。
BATCH = 1
TIMEOUT = 40
# 并发跑「批次」——不是把几家企业并进一个提示词，是多家各发一个请求。
# BATCH 保持 1：实测同一批里塞进不同行业的企业会改变对单家的判断
# （「国联财务」混在含 IT 企业的一批里会被判「可能招软件岗」，单独判是「无」）。
# 并发不改提示词内容，所以判定结果与逐条串行**逐字一致**。
MAX_WORKERS = max(1, int(os.environ.get("FAIR_LLM_WORKERS") or 8))

# 视为「没有岗位公告」的来源取值。早先只认 未知1/未知2，漏了「未知」这一批
# （source_kind=未知、job_count=0 的企业），它们于是掉进 fair_bridge 的模板兜底，
# 出现「根据教育行业推断，可能招 Python 后端」这种其实没判断过的断言。
NO_JOB_SOURCES = ("未知1", "未知2", "未知", "")

PROMPT = """你是双选会会前规划助手。下面这些参会单位**没有查到具体岗位公告**，但已补充了联网核实的「主营范围 / 主要产品」。
学生勾选的意向岗位：{intended}

请逐家判断：按该单位的行业 / **主营范围与主要产品** / 单位性质，它今年校招是否**可能招**上面这些岗位里的某几个？

【输出格式】严格按行输出，每行一条，用竖线 | 分隔。不要输出 JSON、表头、解释或空行：
编号 | 可能招的岗位（从上面意向岗位里挑原词，最多 2 个，用顿号分隔；一个都不合适就写「无」） | 一句话依据
示例：1 | 供应链管培生、采购寻源专员 | 医药制造企业需要供应链与采购支持

【要求】
1. 岗位名只能是上面学生勾选的意向岗位**原词**，不许造新岗位名、不许写岗位族。
2. 判断要克制：单位性质明显不匹配就写「无」（例如党政机关、中小学通常不招「供应链管培生」）；
   但「没有岗位公告」不等于「不招」，主营范围与意向岗位相关就给 1-2 个。
   若主营范围为空、只有单位名称与行业，就按名称能看出的业务判断（「XX 铝业」= 铝加工，
   「XX 汽车部件」= 汽车零部件）；**名字也看不出业务（如「XX 新区高新技术产业」）就写「无」**，
   不要拿行业名硬凑——写错方向比写「无」更糟。
   名称与行业对不上时（如名称是「新材料」、行业标「IT」），以名称能看出的实际业务为准；
   拿不准就写「无」。
2b. **同一职能要看方向**：制造业 / 医药 / 材料企业的「质检、检测、化验、检验、品质」是质量或实验条线，
   不是「测试工程师」（软件测试）；只有明确写「软件测试 / 系统测试 / 自动化测试 / 测试开发」才算。
   企业的「信息化、数字化、系统运维」岗对软件类意向可以算，但要在依据里写明是信息化方向。
3. **依据必须落到该单位的主营范围 / 主要产品上**（如「主营钽电解电容器，需要材料与工艺工程师」），
   不要只泛泛说「行业相关」，不要编造岗位，不要提模型或推断流程。
4. 必须覆盖下面每一个编号，顺序输出。

企业清单（编号 | 名称 | 来源类型 | 行业 | 性质 | 主营范围/主要产品 | 粗略描述）：
{rows}
"""


def _model_tag():
    """当前生效的模型名（含提示词版本），必须进缓存指纹。

    曾经漏了它：glm-4-flash 时期算出的"大模型判断依据"在换成 deepseek 后照样命中，
    页面上写着大模型判断，其实还是旧模型的结论，且无法从文件里看出来。
    """
    try:
        cfg = _load_env()
    except Exception:
        cfg = {}
    return "%s|%s|v4" % (
        os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "",
        os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "")


def _fingerprint(items, intended):
    blob = json.dumps(
        {"intended": list(intended),
         "model": _model_tag(),
         "prompt": PROMPT,
         "code": cache_guard.code_fp(name="unknown_infer"),
         "items": [[x["name"], x.get("jobSource", ""), x.get("industry", ""),
                    x.get("nature", ""), x.get("biz_scope", ""),
                    x.get("job_brief", "")] for x in items]},
        ensure_ascii=False, sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def _load_cache():
    """只返回「本版代码 + 当前模型」产出的 map。

    旧文件里可能混着上一版逻辑或上一个模型的结果，直接按指纹过滤掉：
    宁可重跑一次模型，也不要拿旧结论当新结论用。
    """
    try:
        with open(CACHE, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {"maps": {}, "fps": {}}
    maps = obj.get("maps") or {}
    fps = obj.get("fps") or {}
    cur = cache_guard.code_fp(name="unknown_infer")
    kept = {k: v for k, v in maps.items()
            if fps.get(k) == cur or (v and (v.get("_cg") or {}).get("fp") == cur)}
    if len(kept) != len(maps):
        print("[unknown_infer] 丢弃 %d 份旧版/旧模型缓存（代码指纹不符）"
              % (len(maps) - len(kept)))
    return {"maps": kept, "fps": {k: cur for k in kept}}


def _save_cache(obj):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _prompt_rows(batch):
    lines = []
    for idx, c in enumerate(batch, 1):
        brief = str(c.get("job_brief") or "").replace("\n", " ")[:110]
        scope = str(c.get("biz_scope") or "").replace("\n", " ").strip()[:180]
        if not scope:
            scope = "（未查到主营，按行业/性质判断）"
        lines.append("%d | %s | %s | %s | %s | %s | %s" % (
            idx, c.get("name", ""), c.get("jobSource", ""),
            c.get("industry", "未知"), c.get("nature", "未知"), scope, brief))
    return "\n".join(lines)


def _parse_lines(text, intended):
    out = {}
    for line in (text or "").replace("｜", "|").splitlines():
        line = line.strip().strip("`").strip()
        if not line or "|" not in line or line.startswith(("编号", "---", "示例")):
            continue
        parts = [p.strip() for p in line.split("|")]
        m = re.search(r"\d+", parts[0])
        if not m:
            continue
        i = int(m.group())
        jobs = [x.strip() for x in re.split(r"[、,，;；/]", parts[1])
                if x.strip() in intended][:2]
        out[i] = {"likely": list(dict.fromkeys(jobs)),
                  "reason": (parts[2] if len(parts) > 2 else "")[:90]}
    return out


def _parse_any(text, intended):
    """行格式优先；兼容模型偶尔仍吐 JSON（dict / list / list-of-items）。"""
    rows = _parse_lines(text, intended)
    if rows:
        return rows
    data = None
    try:
        data = _parse_json(text)
    except Exception:
        try:
            t = re.sub(r",\s*([}\]])", r"\1", text)
            if "```" in t:
                t = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", t).strip()
            data = json.loads(t)
        except Exception:
            return {}
    entries = []
    if isinstance(data, dict):
        entries = data.get("items") or []
    elif isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                if isinstance(x.get("items"), list):
                    entries.extend(x["items"])
                else:
                    entries.append(x)
    out = {}
    for r in entries:
        if not isinstance(r, dict):
            continue
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        likely = [str(t).strip() for t in (r.get("likely") or [])
                  if str(t).strip() in intended][:2]
        out[i] = {"likely": list(dict.fromkeys(likely)),
                  "reason": str(r.get("reason") or "")[:90]}
    return out


def build_map(companies, intended, timeout=TIMEOUT):
    """返回 {企业名: {likely:[意向岗位], reason, via}}；失败项 via='fallback'。"""
    intended = [str(x).strip() for x in (intended or []) if str(x).strip()]
    items = [c for c in companies
             if str(c.get("jobSource") or "") in NO_JOB_SOURCES
             and not (c.get("jobs") or [])]
    if not intended or not items:
        return {}

    key = _fingerprint(items, intended)
    cached = _load_cache()
    maps = cached.get("maps") or {}
    if maps.get(key):
        return maps[key]

    cfg = _load_env()
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY"))
    out = {}
    if has_key:
        def _one_batch(batch, to):
            text = llm.chat(PROMPT.format(intended="、".join(intended),
                                          rows=_prompt_rows(batch)),
                            cfg, timeout=to)
            return _parse_any(text, intended)

        batches = [items[s:s + BATCH] for s in range(0, len(items), BATCH)]
        results = {}
        workers = min(MAX_WORKERS, len(batches)) or 1
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one_batch, b, timeout): i
                    for i, b in enumerate(batches)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:
                    print("[unknown_infer] 批次 %d 判定失败，回退：%s"
                          % (i + 1, str(exc)[:70]))
                    results[i] = {}
        for i, batch in enumerate(batches):
            by_i = results.get(i) or {}
            for idx, c in enumerate(batch, 1):
                r = by_i.get(idx) or {}
                out[c["name"]] = {
                    "likely": list(dict.fromkeys(r.get("likely") or []))[:2],
                    "reason": str(r.get("reason") or "")[:90],
                    "via": "llm" if r else "fallback",
                }
    else:
        for c in items:
            out[c["name"]] = {"likely": [], "reason": "", "via": "fallback"}

    # 只缓存整批都成功的结果；按画像指纹并存多份，换画像来回切换都能命中
    if out and all(v["via"] == "llm" for v in out.values()):
        maps[key] = out
        while len(maps) > 8:
            maps.pop(next(iter(maps)))
        fps = dict(cached.get("fps") or {})
        fps[key] = cache_guard.code_fp(name="unknown_infer")
        _save_cache({"maps": maps, "fps": fps, "model": _model_tag(),
                     "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "_cg": cache_guard.stamp("unknown_infer", model=_model_tag())})
    else:
        # 只补同一个 key（同画像 + 同代码 + 同模型）的上次结果；
        # 以前从任意旧 map 里捡，换画像后会把别人的「大模型判断依据」贴过来。
        old = maps.get(key) or {}
        for n, v in out.items():
            if v["via"] == "fallback" and old.get(n, {}).get("via") == "llm":
                out[n] = old[n]
    return out
