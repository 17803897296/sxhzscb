# -*- coding: utf-8 -*-
"""
双选会助手 Demo v2 · 核心逻辑

职责：
  1. 岗位族归一 + 校验器（对应「模型主分 -> 规则校验 -> 人工确认」中的校验器）
  2. 城市 / 企业类型 / 规模代理 / 离家距离 的维度取值
  3. 4.5 四分支硬约束（含 excluded 可追溯）
  4. 生成前端所需的静态载荷；最终加权分由页面按学生权重实时计算

设计约束（讨论结论）：
  - 运行时不调用任何模型（第 12 节③ 离线）
  - 「未知」保留为 unknown，绝不给最低档（4.8 五、）
  - 推断岗位不写回 jobs[]，独立字段带 is_inferred / basis / confidence（4.3）
  - 岗位族归一结果固化落盘为 job_family_map.json（4.6 八、）
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

import rules as R

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")


# ------------------------------------------------------------------ 数据加载
def load_data():
    with open(os.path.join(DATA, "companies.json"), encoding="utf-8") as f:
        companies = json.load(f)
    with open(os.path.join(DATA, "booths.json"), encoding="utf-8") as f:
        booths = json.load(f)
    booth_by_name = {b["name"]: b for b in booths}
    return companies, booth_by_name


# ------------------------------------------------------- 岗位族归一（规则兜底）
def normalize_title(title: str) -> dict:
    """返回 {family, form, hit_term, confidence, source, need_review}。

    正式版：这里应改为读取采集阶段由大模型批量归一并落盘的 job_family_map.json。
    规则表保留为校验器 + 离线兜底（4.6 八、）。
    """
    t = (title or "").strip()
    if not t:
        return _fam_result("ANY", "常规", "", 0.0, "空标题", True)

    # form 独立判定（两轴正交，4.5 三、）
    form = "常规"
    for f, kws in R.FORM_KEYWORDS.items():
        if any(k in t for k in kws):
            form = f
            break

    # 名单例外层优先
    if t in R.SPECIAL:
        return _fam_result(R.SPECIAL[t], form, "SPECIAL", 0.99, "名单例外表", False)

    # 最长命中词定主族；不剥离括号（方向词常在括号内）
    best_len, best_fam, best_term = 0, None, ""
    for fam in R.FAMILY_ORDER:
        for kw in R.KEYWORDS[fam]:
            if kw.lower() in t.lower() and len(kw) > best_len:
                best_len, best_fam, best_term = len(kw), fam, kw

    if best_fam is None:
        if form in ("管培生", "专项"):
            return _fam_result("ANY", form, "", 0.6, "无方向管培生/专项计划", False)
        return _fam_result("ANY", form, "", 0.0, "未命中任何关键词", True)

    return _fam_result(best_fam, form, best_term, 0.9, "关键词命中", False)


def _fam_result(family, form, hit_term, confidence, source, need_review):
    return {
        "family": family, "form": form, "hit_term": hit_term,
        "confidence": round(confidence, 2), "source": source,
        "need_review": need_review,
    }


# ---------------------------------------------------------------- 校验器
def validate_family_map(entries: list[dict]) -> dict:
    """4.6 八、的三条校验：族 ID 合法 / hit_term 真在标题里 / 同 title 一致。

    entries: [{title, family, form, hit_term, confidence, source}, ...]
    """
    issues = {"illegal_family": [], "hallucinated_term": [], "conflict": [], "low_confidence": []}
    seen = {}
    for e in entries:
        if e["family"] not in R.FAMILIES:
            issues["illegal_family"].append(e["title"])
        term = e.get("hit_term", "")
        if term and term != "SPECIAL" and term not in e["title"]:
            issues["hallucinated_term"].append((e["title"], term))
        if e["title"] in seen and seen[e["title"]] != e["family"]:
            issues["conflict"].append(e["title"])
        seen[e["title"]] = e["family"]
        if e.get("confidence", 1) < 0.6:
            issues["low_confidence"].append(e["title"])
    return issues


def build_family_map(companies) -> list[dict]:
    """把 277 条岗位名归一结果固化为落盘表（模拟模型输出）。"""
    out, seen = [], set()
    for c in companies:
        for j in (c.get("jobs") or []):
            t = j.get("title", "")
            if t in seen:
                continue
            seen.add(t)
            r = normalize_title(t)
            out.append({"title": t, **r})
    return out


# ------------------------------------------------------------------ 城市归一
def normalize_city(raw: str):
    """返回 (城市名, 能级名) 或 (None, None)。

    脏文本（「证券获批上市」「智慧城市」等）一律返回 None -> unknown，
    绝不静默归「其他一般城市」（4.8 三、）。
    """
    if not raw or raw == "未知":
        return None, None
    for city in R.CITY_ALL:
        if city in raw:
            if city in R.CITY_FIRST:
                return city, "一线"
            if city in R.CITY_NEW1:
                return city, "新一线"
            if city in R.CITY_CAP:
                return city, "普通省会"
            return city, "其他一般城市"
    return None, None


def city_traits(city: str | None) -> list[str]:
    if not city:
        return []
    return [k for k, cities in R.CITY_TRAITS.items() if city in cities]


# -------------------------------------------------------------- 维度取值
def scale_proxy(company: dict, headcount) -> dict:
    """行业地位代理分（4.8 一、）：4 档 + 必须列出依据，不冒充权威排名。"""
    name = company.get("name", "")
    nature = company.get("nature", "未知")
    hc = headcount if isinstance(headcount, (int, float)) else 0
    if hc >= 500:
        return {"value": 1.0, "tier_key": "招聘规模大",
                "basis": f"展位招聘人数 {hc} 人（≥500）", "confidence": 0.6}
    if nature in ("央企", "国企", "事业单位") or re.search("集团|股份|中国|国家|控股", name):
        return {"value": 0.7, "tier_key": "集团/体制可核实",
                "basis": f"企业性质 {nature}；名称含集团/股份等", "confidence": 0.65}
    if hc >= 30:
        return {"value": 0.4, "tier_key": "仅规模信号",
                "basis": f"展位招聘人数 {hc} 人", "confidence": 0.5}
    return {"value": 0.1, "tier_key": "弱信号",
            "basis": f"展位招聘人数 {hc} 人，无其他可核实信号", "confidence": 0.4}


def dimension_values(company: dict, booth: dict | None, family_map: dict) -> dict:
    """生成该企业各维度的静态取值；加权与模板在页面侧完成。"""
    raw_city = company.get("city", "未知")
    city, tier_name = normalize_city(raw_city)
    hc = (booth or {}).get("headcount")
    nature = company.get("nature", "未知")
    industry = company.get("industry", "未知")
    jobs = company.get("jobs") or []

    dims = {}

    # 城市条件：未知 / 脏文本 -> unknown，不填最低档
    if city:
        dims["city"] = {
            "status": "hit", "tier_key": tier_name,
            "value": R.CITY_TIER_DEFAULT[tier_name],
            "basis": f"{city} · {tier_name}",
            "traits": city_traits(city), "city": city,
        }
    else:
        dims["city"] = {"status": "unknown", "tier_key": None, "value": None,
                        "basis": "city 未采到或抽取脏文本", "traits": [], "city": None}

    # 离家近：交给页面按 home_city 计算；这里只提供可计算性
    if city:
        dims["home"] = {"status": "hit", "tier_key": "待按家乡计算", "value": None,
                        "basis": f"工作城市 {city} · {R.CITY_PROVINCE.get(city, '未知')}省",
                        "city": city, "province": R.CITY_PROVINCE.get(city, "")}
    else:
        dims["home"] = {"status": "unknown", "tier_key": None, "value": None,
                        "basis": "工作城市未知，无法计算距离", "city": None, "province": ""}

    # 企业类型：未知 -> unknown（87 家），绝不给最低档
    if nature in R.COMPANY_TYPE_TIER:
        dims["company_type"] = {"status": "hit", "tier_key": nature,
                                "value": R.COMPANY_TYPE_TIER[nature],
                                "basis": f"nature = {nature}"}
    else:
        dims["company_type"] = {"status": "unknown", "tier_key": None, "value": None,
                                "basis": "nature 未采到"}

    # 行业：类别匹配，页面按学生所选行业给 1.0 / 0.0
    if industry and industry != "未知":
        dims["industry"] = {"status": "hit", "category": industry, "value": None,
                            "basis": f"industry = {industry}"}
    else:
        dims["industry"] = {"status": "unknown", "category": None, "value": None,
                            "basis": "industry 未采到"}

    # 行业地位（规模/体制代理分）
    dims["scale"] = {"status": "hit", **scale_proxy(company, hc)}

    # 「双休」已从权重维度中移除（用户确认），不再生成维度取值

    return dims


# ------------------------------------------------------------ 四分支硬约束
def classify(company: dict, intended: set[str], family_map: dict) -> dict:
    """4.5 四分支。返回 state / families / inferred / excluded_reason。"""
    jobs = company.get("jobs") or []
    industry = company.get("industry", "未知")

    if jobs:
        fams = {family_map.get(j.get("title", ""), {}).get("family", "ANY") for j in jobs}
        fams.discard("ANY") if len(fams) > 1 else None
        hit = fams & intended
        # 企业含「不限」岗位时，任何意向都不应被硬删（4.5 三、）
        has_any = any(family_map.get(j.get("title", ""), {}).get("family") == "ANY" for j in jobs)
        if hit or (has_any and intended):
            return {"state": "confirmed_hit", "families": sorted(fams),
                    "inferred": None, "excluded_reason": None}
        return {"state": "confirmed_miss", "families": sorted(fams),
                "inferred": None,
                "excluded_reason": "有 JD，岗位族全不命中意向：" + "/".join(sorted(fams) or ["无"])}

    # 无 JD：行业 -> 岗位族受控映射
    rule = R.INDUSTRY_MAP.get(industry)
    if rule:
        cand = {rule["primary"]} | ({rule["secondary"]} if rule["secondary"] else set())
        if cand & intended:
            return {
                "state": "inferred_hit", "families": sorted(cand),
                "inferred": {
                    "is_inferred": True, "family": rule["primary"],
                    "basis": f"网上未查到岗位信息；依据行业「{industry}」的受控映射推断",
                    "confidence": rule["confidence"],
                    "question": rule["question"],
                },
                "excluded_reason": None,
            }
        return {"state": "inferred_miss", "families": sorted(cand), "inferred": None,
                "excluded_reason": f"行业「{industry}」映射的岗位族不含意向"}
    return {"state": "inferred_miss", "families": [], "inferred": None,
            "excluded_reason": "无 JD 且 industry 未知，无法推断岗位"}


# ------------------------------------------------------------------ 载荷
def build_payload(companies, booth_by_name, family_map_list):
    family_map = {e["title"]: e for e in family_map_list}
    payload, excluded = [], []
    for c in companies:
        booth = booth_by_name.get(c["name"])
        jobs = c.get("jobs") or []
        norm_jobs = [{"title": j.get("title", ""),
                      "family": family_map.get(j.get("title", ""), {}).get("family", "ANY"),
                      "form": family_map.get(j.get("title", ""), {}).get("form", "常规")}
                     for j in jobs]
        rec = {
            "id": c.get("id"), "name": c.get("name"),
            "booth": (booth or {}).get("booth", ""),
            "headcount": (booth or {}).get("headcount"),
            "nature": c.get("nature", "未知"),
            "city_raw": c.get("city", "未知"),
            "industry": c.get("industry", "未知"),
            "jobs": norm_jobs,
            "dims": dimension_values(c, booth, family_map),
        }
        payload.append(rec)

    return payload, excluded


def apply_classification(payload, intended: set[str], family_map_list) -> tuple[list, list]:
    """按学生意向跑四分支，返回 (队列, 排除清单)。实际页面会实时重跑。"""
    family_map = {e["title"]: e for e in family_map_list}
    queue, excluded = [], []
    for rec in payload:
        r = classify(_to_company(rec), intended, family_map)
        rec.update(r)
        if r["state"] in ("confirmed_miss", "inferred_miss"):
            excluded.append({"name": rec["name"], "reason": r["excluded_reason"]})
        else:
            queue.append(rec)
    return queue, excluded


def _to_company(rec):
    return {"name": rec["name"], "city": rec["city_raw"], "nature": rec["nature"],
            "industry": rec["industry"], "jobs": rec["jobs"]}


# ------------------------------------------------------------ Python 侧计分
def score(rec, weights: dict, template: dict, home_city: str | None,
          industries: set[str], config: dict, label_only: set[str]) -> dict:
    """与页面 JS 同口径的计分，用于自检与文档对照。"""
    vals = {}
    d = rec["dims"]

    # 城市条件：模板覆盖默认档位
    if d["city"]["status"] == "hit":
        vals["city"] = (template.get(d["city"]["tier_key"], d["city"]["value"]),
                        d["city"]["basis"])
    # 离家近
    if d["home"]["status"] == "hit" and home_city:
        vals["home"] = (_home_value(d["home"]["city"], home_city), "按家乡城市计算")
    # 企业类型 / 行业 / 规模代理
    if d["company_type"]["status"] == "hit":
        vals["company_type"] = (d["company_type"]["value"], d["company_type"]["basis"])
    if d["industry"]["status"] == "hit":
        vals["industry"] = (1.0 if d["industry"]["category"] in industries else 0.0,
                            d["industry"]["basis"])
    if d["scale"]["status"] == "hit":
        vals["scale"] = (d["scale"]["value"], d["scale"]["basis"])
    # worklife 永不进 vals

    active = {k: v for k, v in vals.items() if k not in label_only}
    pool = sum(weights.get(k, 0) for k in active)
    total = sum(weights.values()) or 1
    if pool <= 0:
        return {"score": 0.0, "effective_weight": 0.0, "parts": {}, "pool": 0}
    s = sum(weights.get(k, 0) / pool * v[0] for k, v in active.items()) * 100
    return {
        "score": round(s, 2),
        "effective_weight": round(pool / total * 100, 1),
        "parts": {k: {"value": round(v[0], 2), "weight": weights.get(k, 0),
                      "contribution": round(weights.get(k, 0) / pool * v[0] * 100, 2),
                      "basis": v[1]} for k, v in active.items()},
        "pool": round(pool, 1),
    }


def _home_value(city, home_city):
    if not city or not home_city:
        return 0.0
    if city == home_city:
        return R.HOME_TIER["同城"]
    p, hp = R.CITY_PROVINCE.get(city), R.CITY_PROVINCE.get(home_city)
    if p and hp and p == hp:
        return R.HOME_TIER["同省"]
    return R.HOME_TIER["其他"]


# -------------------------------------------------------------------- 自检
def self_check(payload, family_map_list) -> dict:
    issues = validate_family_map(family_map_list)
    review = [e["title"] for e in family_map_list if e.get("need_review")]
    fam_count = Counter()
    for rec in payload:
        for j in rec["jobs"]:
            fam_count[j["family"]] += 1
    total_jobs = sum(fam_count.values()) or 1
    top_share = max(fam_count.values()) / total_jobs if fam_count else 0

    city_ok = sum(1 for r in payload if r["dims"]["city"]["status"] == "hit")
    type_ok = sum(1 for r in payload if r["dims"]["company_type"]["status"] == "hit")
    dims_ok = {k: sum(1 for r in payload if r["dims"][k]["status"] == "hit")
               for k in ("city", "company_type", "industry", "scale")}

    # 最低档占比 > 80% 的维度降级为标签（4.8 五、）
    label_only = set()
    for k in ("city", "company_type", "industry", "scale"):
        vals = [r["dims"][k].get("value") for r in payload if r["dims"][k]["status"] == "hit"
                and r["dims"][k].get("value") is not None]
        if vals and min(vals) is not None:
            low = sum(1 for v in vals if v == min(vals)) / len(vals)
            if low > R.SCORING["min_tier_share"]:
                label_only.add(k)

    return {
        "companies": len(payload),
        "jobs": total_jobs,
        "distinct_titles": len(family_map_list),
        "family_distribution": dict(fam_count),
        "top_family_share": round(top_share, 3),
        "unmapped_titles": review,
        "validation_issues": {k: len(v) for k, v in issues.items()},
        "city_available": city_ok,
        "company_type_available": type_ok,
        "dimension_availability": dims_ok,
        "label_only_dimensions": sorted(label_only),
        "rules_version": R.VERSION,
    }
