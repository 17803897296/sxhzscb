"""个性化加权排序：把采集端的偏好权重接进我们的链路。

规则来源：shuangxuan-handoff README §5 / §9

    个性化排序分 = 基础匹配分 × (1 + Σ 维度权重ᵢ × 维度匹配度ᵢ / 100)

三条硬约束（交接文档原文）：
1. 权重只调排序，不做硬筛。
2. values 全 0 或缺失 = 未收集，按等权处理，**不能当负分**。
3. 企业类型、家乡城市是软信息，命中加分，未命中不排除。

用法：
    python -X utf8 rank.py --profile data/profile.json --companies data/companies.json -o data/results.json
"""

import argparse
import json
import re
from pathlib import Path

UNKNOWN = "未知"
DIMS = ["salary", "stability", "growth", "autonomy", "impact", "balance"]

NATURE_LEVEL = {"央企": 1.0, "事业单位": 1.0, "国企": 0.9,
                "合资": 0.6, "外企": 0.5, "民企": 0.35}

# 维度匹配度的判据：命中正向 → 1，命中反向 → 低，查不到 → 0.5（中性，不当负分）
RULES = {
    "growth":   (("培训", "导师", "带教", "晋升", "轮岗", "成长", "培养"), ()),
    "autonomy": (("自主", "独立负责", "独立完成", "牵头"), ("协助", "配合", "执行上级")),
    "impact":   (("影响", "服务客户", "价值", "成果应用"), ()),
    "balance":  (("双休", "不加班", "弹性", "八小时", "8小时"), ("倒班", "轮班", "长期出差", "驻场")),
}

SALARY_NUM = re.compile(r"\d")


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def city_of(profile):
    region = profile.get("region")
    if isinstance(region, list):
        return [str(x) for x in region]
    return re.split(r"[、/或及和;；,，\s]+", str(region or ""))


def base_score(profile, company):
    """基础匹配分 0–100：城市 40 + 方向 35 + 紧急 25。"""
    city = str(company.get("city") or UNKNOWN)
    regions = city_of(profile)
    if UNKNOWN in regions or not regions:
        city_part = 0.6
    elif any(r and r in city for r in regions) or city in regions:
        city_part = 1.0
    else:
        city_part = 0.0

    direction = str(profile.get("direction_interest") or "")
    words = [w for w in re.split(r"[、/，,\s]+", direction) if len(w) >= 2]
    jobs = company.get("jobs") or []
    blob = " ".join(str(j.get("title", "")) + str(j.get("requirements", "")) for j in jobs)
    if not words:
        dir_part = 0.5
    elif not jobs:
        dir_part = 0.3
    else:
        hit = sum(1 for w in words if w in blob)
        dir_part = clamp(hit / max(1, len(words)) * 1.5)

    urg = UNKNOWN
    for j in jobs:
        if j.get("urgency") in ("高", "中", "低"):
            urg = j["urgency"]
            break
    urg_part = {"高": 1.0, "中": 0.6, "低": 0.2}.get(urg, 0.4)

    return 40 * city_part + 35 * dir_part + 25 * urg_part, {
        "城市": round(city_part, 2), "方向": round(dir_part, 2), "紧急": round(urg_part, 2)}


def dim_scores(company):
    """六个维度的匹配度，0–1。查不到一律 0.5，不当负分。"""
    jobs = company.get("jobs") or []
    text = " ".join(str(j.get("requirements", "")) for j in jobs)
    salary_text = " ".join(str(j.get("salary", "")) for j in jobs)
    nature = str(company.get("nature") or UNKNOWN)

    out = {}
    out["stability"] = NATURE_LEVEL.get(nature, 0.5)

    if not salary_text or salary_text.strip() == UNKNOWN:
        out["salary"] = 0.5
    elif SALARY_NUM.search(salary_text):
        out["salary"] = 1.0
    else:
        out["salary"] = 0.6

    for dim, (pos, neg) in RULES.items():
        if not text:
            out[dim] = 0.5
            continue
        score = 0.5
        if any(k in text for k in pos):
            score = 1.0
        if any(k in text for k in neg):
            score = 0.1
        out[dim] = score
    return out


def weights_of(profile):
    """返回 (六维权重, 是否未收集)。全 0 或缺 → 等权。"""
    raw = profile.get("values") or {}
    vals = {}
    for d in DIMS:
        try:
            vals[d] = max(0, int(raw.get(d, 0) or 0))
        except (TypeError, ValueError):
            vals[d] = 0
    total = sum(vals.values())
    if total <= 0:
        even = 100 // len(DIMS)
        return {d: even for d in DIMS}, True
    return vals, False


def rank(profile, companies):
    weights, uncollected = weights_of(profile)
    types = profile.get("target_company_types") or []
    if isinstance(types, str):
        types = [types]
    home = str(profile.get("home_city") or UNKNOWN)

    rows = []
    for c in companies:
        base, parts = base_score(profile, c)
        dims = dim_scores(c)

        # Σ 权重 × 匹配度 / 100  → 乘数落在 [1, 2]；未收集时等权，仍不改变相对顺序
        factor = 1 + sum(weights[d] * dims[d] for d in DIMS) / 100.0

        bonus = 0.0
        reasons = []
        if parts["城市"] >= 1.0:
            reasons.append("在期望城市")
        if parts["方向"] >= 1.0:
            reasons.append("方向对口")
        if str(c.get("nature")) in types:
            bonus += 5
            reasons.append("命中企业类型偏好")
        if home != UNKNOWN and str(c.get("city")) == home:
            bonus += 8
            reasons.append("就在家乡")

        final = base * factor + bonus
        rows.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "score": round(final, 1),
            "base": round(base, 1),
            "factor": round(factor, 3),
            "dims": {k: round(v, 2) for k, v in dims.items()},
            "reasons": reasons or ["按综合条件排序"],
            "gaps": [] if (c.get("jobs") or []) else ["未查到岗位，仅按企业与地域排序"],
        })

    rows.sort(key=lambda r: -r["score"])
    n = len(rows)
    for i, r in enumerate(rows):
        r["tier"] = "优先去" if i < max(1, int(n * 0.3)) else ("可以看" if i < max(2, int(n * 0.7)) else "建议放弃")
        r["distanceKm"] = None
        r["nearestCity"] = UNKNOWN
        r.pop("base"), r.pop("factor"), r.pop("dims")
    return rows, weights, uncollected


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="data/profile.json")
    ap.add_argument("--companies", default="data/companies.json")
    ap.add_argument("-o", "--output", default="data/results.json")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
    companies = json.loads(Path(args.companies).read_text(encoding="utf-8-sig"))
    rows, weights, uncollected = rank(profile, companies)

    Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    tag = "未收集，按等权处理" if uncollected else "已采集，合计 %d" % sum(weights.values())
    print("权重：" + " ".join("%s=%d" % (d, weights[d]) for d in DIMS) + "（%s）" % tag)
    print("企业 %d 家，输出 %s" % (len(rows), args.output))
    print()
    for r in rows[:10]:
        print("  %-6s %6.1f  %s" % (r["tier"], r["score"], str(r["name"])[:26]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
