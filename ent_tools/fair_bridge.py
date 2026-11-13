# -*- coding: utf-8 -*-
"""
三端协调数据中台：采集端 + 企业端 + 匹配端 -> 一份可直接渲染的 payload。

数据源统一走 ent_tools/data/careersafari.db：
  fair_profiles  （采集端：学生画像）
  company_jobs / jd_records / positions（企业端：118 家、277 条岗位）
  llm_match      （匹配端：模型判定的 hit / inferred / miss）

输出 ent_tools/data/fair_payload.json，被 build_fair_page.py 内联进原 fair.html。
页面形式（聊天式采集端、卡片、CSS 类）一律不动，只换内容。

重点解决上一轮实测暴露的短板：
  - 模型结果里 gaps 只有 6 种取值（模板化）-> 按每家企业真实缺失字段动态生成；
  - 分数不可比（54/118 家生效权重 < 60%）-> 报告显示「生效权重」并逐项列算分依据；
  - priority 只有 5 档 -> 粗排靠它，细排靠 reason / matched_jobs / verify_question。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
OUT = os.path.join(DATA, "fair_payload.json")

sys.path.insert(0, os.path.join(HERE, "demo_v2"))
import core as V2            # noqa: E402  复用五项权重 / 维度取值口径
import rules as R            # noqa: E402
import intent_semantics      # noqa: E402  岗位语义符合度（大模型推断）
import hard_filter           # noqa: E402  v12 硬约束解析与教培类判定

# ── v4.2 最新数据源（hy3 工作区产出）────────────────────────────
# 采集流程（校招官网 → 招聘软件 → 联网兜底 / 4 列库）保持原样，一步不改。
# 这里只在 build 时按企业名把 v4.2 数据"合并"进既有 payload 骨架：
#   覆盖 4 个打分字段；其余字段全部作为展示层追加；booth/state/evidence 等保留。
V42_DB = os.path.join(DATA, "v42_company_match.db")
V42_JSON_FIELDS = (
    "rank_hits", "growth_hits", "jobs_total", "jobs_official", "jobs_platform",
    "jobs_websearch", "work_cities", "source_urls", "field_confidence", "industry_rank",
)
V42_DISPLAY_FIELDS = (
    "industry_rank_label", "rank_dim", "rank_tier", "rank_hits", "industry_rank",
    "growth_tier", "growth_hits", "growth_evidence_url", "growth_note",
    "segment", "segment_source_url", "segment_leader",
    "segment_leader_source_url", "segment_leader_confidence",
    "work_cities", "job_coverage", "has_job_info", "non_enterprise",
)
# v4.2 采集层保留细分性质，打分层仍是 国央企 / 民企 / 外企 三桶：
# 只归一"打分读取值"，原值存 *_raw 供展示，绝不丢数据。
NATURE_TO_BUCKET = {"民办非企业": "民企", "合伙制": "民企", "合资": "民企",
                    "党政机关": "事业单位"}


def load_v42():
    """读 v4.2 companies 表；文件缺失时静默跳过，保证旧链路可回滚。"""
    if not os.path.exists(V42_DB):
        return {}
    con = sqlite3.connect(V42_DB)
    con.row_factory = sqlite3.Row
    try:
        out = {}
        for row in con.execute("SELECT * FROM companies"):
            d = dict(row)
            for k in V42_JSON_FIELDS:
                v = d.get(k)
                if isinstance(v, str) and v[:1] in "[{":
                    try:
                        d[k] = json.loads(v)
                    except Exception:
                        pass
            raw = {}
            n = (d.get("nature") or "").strip()
            if n in NATURE_TO_BUCKET:
                raw["nature"] = n
                d["nature"] = NATURE_TO_BUCKET[n]
            st = (d.get("scale_tier") or "").strip()
            if st in ("", "未知"):
                raw["scale_tier"] = st or "(空)"
                d["scale_tier"] = R.classify_scale(
                    d.get("nature"), d.get("name", ""), d.get("industry"))
            d["_raw"] = raw
            out[d["name"]] = d
        return out
    finally:
        con.close()


def v42_view(d):
    """报告层展示字段。4 列岗位来源结构原样保留（某源为空也留键，布局不变）。"""
    if not d:
        return {}
    v = {k: d.get(k) for k in V42_DISPLAY_FIELDS}
    sources = {}
    for col, key in (("jobs_official", "official"), ("jobs_platform", "platform"),
                     ("jobs_websearch", "websearch"), ("jobs_total", "total")):
        arr = d.get(col) or []
        if isinstance(arr, str):
            try:
                arr = json.loads(arr)
            except Exception:
                arr = []
        sources[key] = [x.get("title", "") for x in arr
                        if isinstance(x, dict) and x.get("title")]
    v["job_sources"] = sources
    v["raw"] = d.get("_raw") or {}
    return v


DEFAULT_PROFILE = {
    "major": "管理科学与工程",
    "experiences": ["某电商平台供应链实习：用 SQL 做库存周转分析，把补货周期从 14 天压到 9 天"],
    "intended_jobs": ["供应链计划", "数据分析师", "商业分析"],
    "home_city": "南京", "region": "南京",
}

# 现场提问库：按缺口类型给一句能问出结果的话
ASK = {
    "岗位": "请问贵司今年是不是招 {job} 岗位？",
    "薪资": "请问 {company} 这个岗位的薪资范围大概是多少？",
    "作息": "请问 {company} 这边是双休吗？加班多不多？",
    "人数": "请问 {company} 这个岗位今年计划招几个人？",
    "城市": "请问 {company} 的工作地点具体在哪个城市？",
    "性质": "请问 {company} 是央企 / 国企 / 民企 / 外企？",
    "行业": "请问 {company} 这个岗位属于哪条业务线？",
    "职责": "请问 {company} 的 {job} 日常主要做什么？",
    "阶段": "请问 {company} 的 {job} 是校招还是社招？接受应届生吗？",
    "证据": "请问 {company} 有今年的招聘公告或岗位说明可以看一眼吗？",
}


def read_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        companies = {}
        for r in con.execute("SELECT name,item_json FROM company_jobs"):
            companies[r["name"]] = json.loads(r["item_json"])
        llm = {}
        for r in con.execute("SELECT * FROM llm_match"):
            llm[r["name"]] = {
                "state": r["state"], "priority": r["priority"],
                "matched_jobs": json.loads(r["matched_jobs"]),
                "reason": r["reason"], "gaps": json.loads(r["gaps"]),
                "verify_question": r["verify_question"], "has_jd": bool(r["has_jd"]),
            }
        # v6：优先读 fair_ingest 标记的「当前生效画像」；没有则退回时间最新一条。
        # 这样采集端写库后，匹配端读到的确实是学生刚提交的那份，而不是冻结快照。
        prof = None
        has_active = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fair_active'").fetchone()
        if has_active:
            act = con.execute(
                "SELECT fair_id FROM fair_active WHERE id='current'").fetchone()
            if act:
                prof = con.execute(
                    "SELECT document_json FROM fair_profiles WHERE fair_id=?",
                    (act[0],)).fetchone()
        if prof is None:
            prof = con.execute(
                "SELECT document_json FROM fair_profiles ORDER BY created_at DESC LIMIT 1").fetchone()
        profile = json.loads(prof["document_json"]) if prof else {}
        return companies, llm, profile
    finally:
        con.close()


def booths():
    return {b["name"]: b for b in json.load(
        open(os.path.join(DATA, "booths.json"), encoding="utf-8"))}


def read_feedback():
    """v7：现场核实结果（fair_ingest.fair_feedback 表）回流到匹配端。"""
    try:
        import fair_ingest
        return fair_ingest.read_feedback()
    except Exception:
        return {}


def profile_from(profile):
    """采集端画像 -> 匹配端用的字段；缺的用默认补全。"""
    ans = profile.get("answers") or {}
    ident = profile.get("identity") or {}
    fair = profile.get("fair") or {}
    return {
        "major": ident.get("major") or DEFAULT_PROFILE["major"],
        "experiences": [e.get("text", "") for e in (profile.get("resume", {})
                        .get("extraction", {}).get("experiences") or [])] or DEFAULT_PROFILE["experiences"],
        "intended_jobs": ans.get("intended_jobs") or DEFAULT_PROFILE["intended_jobs"],
        "home_city": fair.get("home_city") or DEFAULT_PROFILE["home_city"],
        "region": ans.get("region") or DEFAULT_PROFILE["region"],
        "values": ans.get("values") or {},
        # v10：城市/企业类型/行业的排名（采集端追问得到；缺则用默认排名）
        "city_rank": ans.get("city_rank") or dict(R.CITY_RANK_DEFAULT),
        "company_type_rank": ans.get("company_type_rank") or dict(R.COMPANY_TYPE_RANK_DEFAULT),
        "industry_rank": ans.get("industry_rank") or dict(R.INDUSTRY_RANK_DEFAULT),
        "fair_id": profile.get("fair_id", "FAIR-DEMO-0001"),
    }


def weights_from(profile):
    """七项 100：优先用采集端显式传来的 tri_weights，其次兼容旧字段，最后用默认。"""
    # 起始全部置 0：未显式选中的维度（含新增的行业地位/成长性）默认 0，
    # 不参与权重，避免旧 5 维画像被凭空加上默认分导致合计 > 100。
    w = {d["id"]: 0.0 for d in R.DIMENSIONS}
    ans = profile.get("answers") or {}
    tri = ans.get("tri_weights") or {}
    # 用户传来的权重字典里，0 表示「明确不选」，必须保留为 0，不能回退到默认分。
    picked = {k: float(v) for k, v in tri.items() if k in w}
    if picked and sum(picked.values()) > 0:
        total = sum(picked.values())
        w.update({k: round(v / total * 100, 1) for k, v in picked.items()})
        return w
    # 无任何显式权重（空画像/旧格式）：用 DIMENSIONS 默认 7 维（合计 100），
    # 保证「没填完整」也能正常出一份默认权重的匹配报告。
    w = {d["id"]: float(d["default"]) for d in R.DIMENSIONS}
    vals = profile.get("values") or ans.get("values") or {}
    mapped = {
        "city": vals.get("city") if vals.get("city") is not None else vals.get("salary"),
        "home": vals.get("home") if vals.get("home") is not None else vals.get("stability"),
        "company_type": vals.get("company_type") if vals.get("company_type") is not None else vals.get("growth"),
        "industry": vals.get("industry") if vals.get("industry") is not None else vals.get("autonomy"),
        "scale": vals.get("scale") if vals.get("scale") is not None else vals.get("impact"),
    }
    picked = {k: float(v) for k, v in mapped.items() if k in w and v is not None}
    if picked and sum(picked.values()) > 0:
        total = sum(picked.values())
        w.update({k: round(v / total * 100, 1) for k, v in picked.items()})
    return w


def dynamic_gaps(c, llm_row, booth):
    """按这家企业真实缺什么生成 gaps，取代模型那 6 种模板。"""
    g = []
    jobs = c.get("jobs") or []

    def put(kind, why, conf):
        ctx = {"job": "、".join(llm_row["matched_jobs"][:2]) or "对口",
               "company": c.get("name", "贵司"),
               "city": c.get("city", "未知"), "industry": c.get("industry", "未知")}
        g.append({"kind": kind, "why": why, "confidence": conf,
                  "question": ASK[kind].format(**ctx)})

    if not jobs:
        put("岗位", "网上未查到岗位记录", 0.3)
        put("证据", "没有今年的招聘公告可核对", 0.3)
    if c.get("nature") in ("未知", "", None):
        put("性质", "nature 字段未采到", 0.2)
    if c.get("city") in ("未知", "", None):
        put("城市", "city 字段未采到", 0.2)
    elif not V2.normalize_city(c.get("city"))[0]:
        put("城市", "city 抽取异常（%s）" % str(c.get("city"))[:18], 0.2)
    if c.get("industry") in ("未知", "", None):
        put("行业", "industry 字段未采到", 0.2)
    if jobs:
        if all(str(j.get("salary", "未知")) in ("未知", "", "None") for j in jobs):
            put("薪资", "岗位记录里薪资全为未知", 0.2)
        if all(str(j.get("headcount", "未知")) in ("未知", "", "None") for j in jobs):
            put("人数", "岗位记录里招聘人数全为未知", 0.3)
        if all("职责：未知" in str(j.get("requirements", "")) for j in jobs):
            put("职责", "岗位描述缺职责原文", 0.4)
        if all("招聘阶段：未知" in str(j.get("requirements", "")) for j in jobs):
            put("阶段", "未标注校招 / 社招", 0.4)
    put("作息", "公开 JD 中「双休」出现 0/277", 0.2)
    if llm_row["state"] == "inferred":
        put("岗位", "岗位为模型推断，需现场确认", 0.3)
    # 去重（同 kind 保留置信度更低的，因为更该问）
    seen = {}
    for x in g:
        if x["kind"] not in seen or x["confidence"] < seen[x["kind"]]["confidence"]:
            seen[x["kind"]] = x
    return list(seen.values())


def score_parts(c, W, profile):
    """v10：五项逐项算分；未知不计入分母，绝不填最低档。
    城市/企业类型/行业按用户排名给值；命中多类取用户最看重的那类（最高值）。
    规模与行业地位客观分档，不问用户；离家近含邻省 0.6。"""
    vals = {}

    # —— 城市：7 类排名，命中多类取用户排名最靠前者 ——
    cats = R.city_categories(c.get("city"))
    cr = profile.get("city_rank") or dict(R.CITY_RANK_DEFAULT)
    matched = [(cat, cr.get(cat, len(R.CITY_CATS) + 1)) for cat in cats if cat in cr]
    if matched:
        matched.sort(key=lambda x: x[1])  # 排名升序，1 = 最看重
        best_cat, best_rank = matched[0]
        v = R.rank_value(best_rank, len(R.CITY_CATS))
        vals["city"] = (v, "城市 %s 命中「%s」(你的第%d优先)" % (c.get("city"), best_cat, best_rank))

    # —— 离家近：同城 / 同省 / 邻省 / 其他 ——
    home = profile.get("home_city")
    cc = c.get("city")
    if home and cc:
        hp = R.CITY_PROVINCE.get(cc)
        hhp = R.CITY_PROVINCE.get(home)
        if cc == home:
            v = 1.0
        elif hp and hhp and hp == hhp:
            v = 0.8
        elif hhp and hp and hp in R.ADJ_PROVINCES.get(hhp, []):
            v = 0.6
        else:
            v = 0.3
        vals["home"] = (v, "工作城市 %s / 家乡 %s" % (cc, home))

    # —— 企业类型：国央企 / 民企 / 外企 三档排名 ——
    b = R.company_type_bucket(c.get("nature"))
    ctr = profile.get("company_type_rank") or dict(R.COMPANY_TYPE_RANK_DEFAULT)
    if b and b in ctr:
        v = R.rank_value(ctr[b], len(R.COMPANY_TYPE_BUCKETS))
        vals["company_type"] = (v, "性质 %s → %s(你的第%d优先)" % (c.get("nature"), b, ctr[b]))

    # —— 行业：对口是硬约束，这里只做排序偏好（不在列表内给中性地板分）——
    ind = c.get("industry")
    ir = profile.get("industry_rank") or dict(R.INDUSTRY_RANK_DEFAULT)
    n_ind = len([k for k in R.INDUSTRY_RANK_DEFAULT])
    if ind and ind in ir:
        v = R.rank_value(ir[ind], n_ind)
        vals["industry"] = (v, "行业 %s(你的第%d优先)" % (ind, ir[ind]))
    else:
        vals["industry"] = (0.4, "行业 %s（不在你的偏好排序内）" % ind)

    # —— 规模 / 行业地位：客观分档，不问用户 ——
    st = c.get("scale_tier") or R.classify_scale(c.get("nature"), c.get("name", ""), c.get("industry"))
    if st in R.SCALE_TIERS:
        vals["scale"] = (R.SCALE_TIERS[st], "规模档 %s（体制/上市/集团客观判定）" % st)

    pool = sum(W.get(k, 0) for k in vals) or 1
    total = sum(W.values()) or 100
    out = []
    for k, (v, basis) in vals.items():
        w = W.get(k, 0)
        out.append({"id": k,
                    "name": next(x["name"] for x in R.DIMENSIONS if x["id"] == k),
                    "value": round(v, 2), "weight": w,
                    "contribution": round(w / pool * v * 100, 1), "basis": basis})
    return {"score": round(sum(x["contribution"] for x in out), 1),
            "effective_weight": round(pool / total * 100, 1),
            "parts": out, "pool": round(pool, 1)}


INDUSTRY_OK = {
    "金融": ["研究", "量化", "投行", "风控", "财富", "理财", "交易", "证券", "基金",
             "金融", "顾问", "结算", "稽核", "数据", "分析"],
    "IT": ["研发", "算法", "开发", "数据", "软件", "硬件", "测试", "产品", "工程师",
           "系统", "AI", "运维", "分析"],
    "制造": ["供应链", "工艺", "生产", "质量", "设备", "采购", "物流", "计划",
             "制造", "机械", "电气", "项目", "分析"],
    "建筑": ["工程", "造价", "项目", "设计", "施工", "监理", "建筑", "管理"],
    "教育": ["教研", "教师", "教务", "培训", "编辑", "课程", "学术", "运营"],
    "医药": ["研发", "临床", "注册", "医药", "生物", "质量", "数据", "医学", "分析"],
    "能源": ["工程", "运行", "项目", "技术", "储能", "电力", "安全"],
    "消费": ["销售", "市场", "运营", "品牌", "产品", "供应链", "零售"],
    "未知": [],
}


def audit(c, m, all_titles=None, by_title=None):
    """企业端事实校验：把模型结论与企业真实 JD / 行业 / 全库岗位名对一遍。"""
    notes, trust = [], "high"
    all_titles = all_titles or {}
    jobs = c.get("jobs") or []
    titles = {j.get("title", "") for j in jobs}
    ind = c.get("industry", "未知")

    if jobs and m["matched_jobs"]:
        hit = [t for t in m["matched_jobs"] if t in titles]
        if not hit:
            notes.append("模型给出的岗位均不在该企业 %d 条真实 JD 中（疑似串批次）" % len(titles))
            trust = "low"
        elif len(hit) < len(m["matched_jobs"]):
            notes.append("部分岗位名不在该企业 JD 原文中")
            trust = "medium"
        # 串批次检测：岗位名出现在别家 JD、却不在本家
        for t in m["matched_jobs"]:
            if t and t not in titles and t in all_titles:
                owners = (by_title or {}).get(t, set())
                if owners and c.get("name") not in owners:
                    notes.append("岗位名「%s」出现在其他企业 JD 中，疑似串用" % t[:16])
                    trust = "low"
                    break

    if ind not in ("未知", "") and m["reason"]:
        for other, key in (("IT行业", "IT"), ("互联网行业", "IT"),
                           ("金融行业", "金融"), ("制造业", "制造"),
                           ("教育行业", "教育")):
            if other in m["reason"] and key != ind:
                notes.append("模型理由提到「%s」，但企业端 industry=%s" % (other, ind))
                trust = "low"
                break

    if m["state"] == "inferred":
        ok = INDUSTRY_OK.get(ind, [])
        if ok and m["matched_jobs"] and not any(any(k in t for k in ok) for t in m["matched_jobs"]):
            notes.append("推断岗位与 %s 行业常见岗位不太吻合，请优先现场确认" % ind)
            if trust != "low":
                trust = "medium"
        if not jobs:
            notes.append("岗位为按行业推断，不是企业公开事实")
            if trust == "high":
                trust = "medium"

    if not notes:
        notes.append("与企业端记录一致")
    return trust, notes


_TITLE_NOISE = ("实习生", "岗位", "职位", "岗", "校招", "春招", "秋招", "招聘")
_TITLE_RE_MJ = re.compile(r"mj\d+")
_TITLE_RE_GRAD = re.compile(r"(?:20\d{2})?届")


def _norm_title(s):
    """岗位名归一化，用于判断「模型给的叫法」与「企业 JD 原文」是否同一个岗。"""
    t = str(s or "").lower()
    t = _TITLE_RE_MJ.sub("", t)
    t = re.sub(r"[^0-9a-z\u4e00-\u9fa5]", "", t)
    for w in _TITLE_NOISE:
        t = t.replace(w, "")
    t = _TITLE_RE_GRAD.sub("", t)
    t = re.sub(r"\d+", "", t)
    return t


def _same_title(a, b):
    """同一岗位的不同写法（业务运营岗（代销方向） vs 业务运营（代销方向）-27届秋招(MJ003064)）。

    只放宽「写法差异」，不放宽「凭空编造」：要求归一化后完全相等，或一方包含另一方
    且长度比 ≥0.6、较短一方至少 4 字。
    """
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) < 4 or len(nb) < 4:
        return False
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb)) >= 0.6
    return False


def hallucination_guard(c, m, trust, by_title):
    """v6 幻觉拦截：拦「岗位名」和「证据等级」，不拦「企业」。

    用户明确要求推断企业照常排序、现场一句话核实。所以这里绝不删企业、
    不降 priority，只做两件事：
      ① 岗位名：有 JD 却给了非本家岗位名、无 JD 却抄了别家岗位名、
         或推断岗位与该行业完全不吻合 -> 改成「待现场确认」，不念给 HR；
      ② 证据等级：无 JD 却被模型标成 hit（明确命中）-> 下调为 inferred，
         让「有真实岗位证据」这组名副其实。企业仍在队列里，只是换组。
    原文全部保留在 original_matched_jobs / original_state 供追溯。
    """
    titles = {j.get("title", "") for j in (c.get("jobs") or [])}
    has_jd = bool(titles)
    ind = c.get("industry", "未知")
    ok_words = INDUSTRY_OK.get(ind, [])
    bad, reasons, industry_warn = [], [], False
    for t in m["matched_jobs"]:
        if not t or any(_same_title(t, x) for x in titles):
            continue
        if has_jd:
            bad.append(t)
            reasons.append("有 JD 却给了不在本家 %d 条岗位记录里的岗位名" % len(titles))
        elif t in by_title and c.get("name") not in by_title[t]:
            bad.append(t)
            reasons.append("无 JD 却抄了其他企业 JD 里的岗位名")
        elif ok_words and not any(k in t for k in ok_words):
            industry_warn = True          # 只提示，不屏蔽：推断岗位正是要拿去现场问的
    bad = list(dict.fromkeys(bad))
    display = [("（待现场确认：%s）" % t[:14]) if t in bad else t
              for t in m["matched_jobs"]]
    ask = [t for t in m["matched_jobs"] if t not in bad] or ["对口岗位"]

    parts = []
    if bad:
        parts.append("已拦截 %d 个硬事实错误岗位名（%s）：%s" %
                     (len(bad), "、".join(x[:12] for x in bad[:3]),
                      "；".join(dict.fromkeys(reasons))[:110]))
    if industry_warn:
        parts.append("推断岗位与 %s 行业常见岗位不太吻合，现场优先确认即可" % ind)
    if trust == "low" and not bad:
        parts.append("模型结论与企业端事实对不上，请以现场问句为准")
    if not has_jd and m["state"] == "hit":
        parts.append("企业端没有岗位记录，模型却标「明确命中」，已下调为推断待核实")
    if bad or trust == "low" or industry_warn or (not has_jd and m["state"] == "hit"):
        parts.append("本企业仍照常参与排序，现场问一句即可确认；没有就顺延")
    return {
        "action": "masked" if bad else ("flagged" if (trust == "low" or industry_warn) else "none"),
        "bad_jobs": bad, "display_jobs": display, "ask_jobs": ask,
        "note": "；".join(parts),
        "state_fix": (m["state"] == "hit" and not has_jd),
        "original_state": m["state"],
    }


def build():
    companies, llm, raw_profile = read_db()
    v42 = load_v42()
    # 以 v4.2 为尊：4 个打分字段取最新数据（归一后），其余字段只作展示追加。
    for _name, _c in companies.items():
        _v = v42.get(_name)
        if not _v:
            continue
        for _k in ("city", "nature", "industry", "scale_tier"):
            if _v.get(_k):
                _c[_k] = _v[_k]
    profile = profile_from(raw_profile)

    # v12（2026-11-13 用户要求）：硬约束过滤。
    #  ① 学生那句自由填空（「只要南京」「只看互联网」）由大模型解析成条件，
    #     不满足的企业**直接从名单里删除**，不是降权。
    #  ② 教培类企业默认剔除，学生明确表示「愿意考虑」才保留。
    # 过滤发生在后续所有计算之前，被删的企业不会出现在 rows 里。
    _conds = hard_filter.build_conditions(raw_profile, list(companies.values()))
    _dropped = []
    if _conds["hard"].get("mode") not in (None, "none") or _conds["drop_training"]:
        _keep = {}
        for _n, _c in companies.items():
            if _conds["drop_training"] and hard_filter.is_training_biz(_c):
                _dropped.append({"name": _n, "why": "教培类（学生未选择接受）"})
                continue
            if not hard_filter.match(_c, _conds["hard"]):
                _dropped.append({"name": _n, "why": "不满足硬约束：" + (_conds["raw_text"] or "")})
                continue
            _keep[_n] = _c
        companies = _keep

    # 用大模型判断「学生勾选的意向岗位」与「企业真实岗位名」是否语义相符。
    # 结果放进 payload 顶层，前端据此显示语义命中；模型不可用时返回 {}，前端退回字面匹配。
    _all_titles = sorted({j.get("title", "").strip()
                          for c in companies.values()
                          for j in (c.get("jobs") or [])
                          if j.get("title")})
    # 只用当前画像里学生真正勾选的意向岗位；确实没有画像时才回退默认示例。
    # 不把「管科 + 供应链计划/数据分析师/商业分析」硬塞进每个用户的语义映射。
    _intended = list(dict.fromkeys(
        list(profile.get("intended_jobs") or []) or list(DEFAULT_PROFILE["intended_jobs"])))
    intent_sem = intent_semantics.build_map(_intended, _all_titles)
    W = weights_from(raw_profile)
    bm = booths()
    fb = read_feedback()

    c_list = list(companies.values())
    by_title = {}
    for c in c_list:
        for j in c.get("jobs") or []:
            by_title.setdefault(j.get("title", ""), set()).add(c.get("name", ""))
    fam = V2.build_family_map(c_list)
    payload, _ = V2.build_payload(c_list, bm, fam)
    by_name = {r["name"]: r for r in payload}

    rows = []
    for name, c in companies.items():
        m = llm.get(name) or {"state": "miss", "priority": 1, "matched_jobs": [],
                              "reason": "", "gaps": [], "verify_question": "", "has_jd": bool(c.get("jobs"))}
        rec = by_name.get(name, {})
        trust0, _ = audit(c, m, by_title, by_title)
        guard = hallucination_guard(c, m, trust0, by_title)
        # 对外一律用脱敏后的岗位名（问句、命中列表、显著提示都不再念幻觉岗位）
        m_eff = dict(m)
        m_eff["matched_jobs"] = guard["display_jobs"]
        if guard["state_fix"]:
            m_eff["state"] = "inferred"
        # 证据等级修正后重跑一次事实校验，让行业不吻合检测也能生效
        m_audit = dict(m)
        m_audit["state"] = m_eff["state"]
        trust, check_notes = audit(c, m_audit, by_title, by_title)
        m_gap = dict(m_eff)
        m_gap["matched_jobs"] = guard["ask_jobs"]   # 问句里念正常岗位名，不念屏蔽占位符
        gaps = dynamic_gaps(c, m_gap, bm.get(name))
        sc = score_parts(c, W, profile)
        booth = bm.get(name, {})

        # 现场提问：权重 × (1 - 证据可信度)，取前 2；推断岗位固定排第一
        qs = []
        if m_eff["state"] == "inferred":
            qs.append({"q": "【显著提示】网上没有岗位信息，根据「%s」行业我们推断他们可能有 %s 类岗位，"
                            "您可以去现场问一句：%s" % (
                                c.get("industry", "未知"),
                                "、".join(guard["ask_jobs"][:2]) or "对口",
                                m["verify_question"] or "是不是招这个岗位？"),
                       "priority": 999, "infer": True})
        for g in gaps:
            wmap = {"岗位": W["industry"], "行业": W["industry"], "职责": W["scale"],
                    "阶段": W["scale"], "薪资": W["scale"], "人数": W["scale"],
                    "城市": W["city"], "性质": W["company_type"], "作息": W["city"], "证据": W["scale"]}
            qs.append({"q": g["question"], "priority": round(wmap.get(g["kind"], 10) *
                       (1 - g["confidence"]), 1), "infer": False,
                       "why": g["why"]})
        qs.sort(key=lambda x: -x["priority"])

        jobs = c.get("jobs") or []
        hits, misses = [], []
        for j in jobs[:6]:
            t = j.get("title", "")
            (hits if t in m_eff["matched_jobs"] else misses).append(t)
        if not hits and m_eff["matched_jobs"]:
            hits = list(m_eff["matched_jobs"][:3])

        # ---- v7 现场核实回流：反向修正企业端证据等级 ----
        fbrow = fb.get(name)
        state_final = m_eff["state"]
        trust_final, notes_final = trust, list(check_notes)
        if fbrow and fbrow["result"] == "yes":
            if state_final == "inferred":
                state_final = "hit"
            trust_final = "high" if trust_final != "low" else "medium"
            notes_final.append("现场已确认招此岗位（%s），推断升级为明确命中" % fbrow["updated_at"])
            qs = [q for q in qs if not q.get("infer")]
        elif fbrow and fbrow["result"] == "no":
            notes_final.append("现场确认不招（%s），已顺延，排在队列末尾" % fbrow["updated_at"])

        rows.append({
            # 静态原始层：交给页面按学生当前画像实时重算分数 / 提问 / 队列
            "dims": rec.get("dims", {}),
            "trust": trust_final, "check_notes": notes_final, "guard": guard,
            "feedback": fbrow or None,
            "name": name, "booth": booth.get("booth", ""), "headcount": booth.get("headcount"),
            "industry": c.get("industry", "未知"), "nature": c.get("nature", "未知"),
            "city": c.get("city", "未知"),
            "province": c.get("province", ""),
            "scale_tier": c.get("scale_tier") or R.classify_scale(
                c.get("nature"), c.get("name", ""), c.get("industry")),
            "state": state_final, "priority": m["priority"],
            "original_state": guard["original_state"],
            "matched_jobs": m_eff["matched_jobs"],
            "original_matched_jobs": m["matched_jobs"],
            "reason": m["reason"],
            "verify_question": m["verify_question"],
            "hits": hits, "misses": misses, "job_count": len(jobs),
            # 企业真实岗位名（前端做语义命中用；不只是模型预生成的 matched_jobs）
            "job_titles": [j.get("title", "") for j in jobs if j.get("title")],
            "gaps": gaps, "questions": qs[:2],
            "score": sc["score"], "effective_weight": sc["effective_weight"],
            "parts": sc["parts"],
            "evidence": {"source": c.get("source", ""), "date": c.get("date", ""),
                         "jobSource": c.get("jobSource", "未知"),
                         "jobEvidence": str(c.get("jobEvidence", "未知"))[:80]},
            "infer_notice": ("网上没有岗位信息，根据「%s」行业我们推断他们可能有 %s 类岗位，"
                             "您可以去现场问一句是不是招这个岗位" % (
                                 c.get("industry", "未知"),
                                 "、".join(guard["ask_jobs"][:2]) or "对口"))
                            if state_final == "inferred" else "",
            # v4.2 展示层：行业地位(多榜) / 成长 / 细分领域 / 4 列岗位来源。
            # 只展示与报告，不进 calcOne、不回写 scale_tier / industry。
            "v42": v42_view(v42.get(name)),
        })

    # 名次只由排序分决定（2026-11-13 用户明确要求）。
    # 可信度 / 待核实 / 岗位状态一律不进名次比较——它们是展示标注，不是排序依据。
    # 唯一例外：「现场确认不招」属于事实出局，仍顺延到队尾。
    rows.sort(key=lambda r: (1 if (r.get("feedback") or {}).get("result") == "no" else 0,
                             -r["score"], -r["priority"], r["name"]))
    by_booth = sorted([r for r in rows if r["booth"]], key=lambda r: str(r["booth"]))
    booth_pos = {r["name"]: i for i, r in enumerate(by_booth)}
    for i, r in enumerate(rows):
        r["next"] = rows[i + 1]["name"] if i + 1 < len(rows) else ""
        r["rank"] = i + 1
        r["rank_reason"] = ("第 %d 位：排序分 %s（仅 %s%% 权重生效） / %s"
                            % (i + 1, r["score"], r["effective_weight"],
                               {"hit": "有真实 JD 命中", "inferred": "按行业推断待核实",
                                "miss": "无对口岗位"}[r["state"]]))
        if (r.get("feedback") or {}).get("result") == "yes":
            r["rank_reason"] += "；现场已确认招，升级为明确命中"
        if (r.get("feedback") or {}).get("result") == "no":
            r["rank_reason"] += "；现场确认不招，已顺延（保留在队尾供核对）"
        p = booth_pos.get(r["name"])
        r["nearby"] = ([by_booth[p - 1]["name"]] if p and p > 0 else []) + \
                      ([by_booth[p + 1]["name"]] if p is not None and p + 1 < len(by_booth) else [])

    out = {
        "version": "v7-tri-2026-11-10", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile, "weights": W,
        "active_fair_id": profile.get("fair_id", ""),
        # v12 硬约束：让学生看见「因为自己设的条件，哪些企业被排除了」。
        # 排除是硬性的（不满足即不出现），但必须可见，不能悄悄删。
        "hard_filter": {
            "raw_text": _conds.get("raw_text") or "",
            "parsed": {k: _conds["hard"].get(k) for k in
                       ("cities", "industries", "exclude_natures", "keywords", "note", "mode")},
            "accept_no_major": _conds.get("accept_no_major", False),
            "dropped": _dropped,
            "dropped_count": len(_dropped),
        },
        "province_map": R.CITY_PROVINCE,
        "sources": {
            "采集端": "careersafari.db / fair_profiles（fair_active 指定当前生效画像；"
                      "页面导出 JSON 或 fair_server POST /api/fair/profile 写库）",
            "企业端": "careersafari.db / company_jobs + jd_records + positions；booths.json（展位、人数）",
            "匹配端": "careersafari.db / llm_match（模型判定 hit / inferred / miss）"
                      " + 企业端事实校验与岗位名幻觉拦截",
            "现场回流": "careersafari.db / fair_feedback（招 -> 升级明确命中；不招 -> 顺延到队尾）",
        },
        "stats": {
            "companies": len(rows), "jobs": sum(len(c.get("jobs") or []) for c in companies.values()),
            "states": dict(Counter(r["state"] for r in rows)),
            "distinct_gap_sets": len(set(tuple(sorted(g["why"] for g in r["gaps"])) for r in rows)),
            "distinct_reasons": len(set(r["reason"] for r in rows)),
            "distinct_questions": len(set(tuple(q["q"] for q in r["questions"]) for r in rows)),
            "with_evidence_url": sum(1 for r in rows if r["evidence"]["source"]),
            "with_nearby": sum(1 for r in rows if r["nearby"]),
            "trust": dict(Counter(r["trust"] for r in rows)),
            "guard": dict(Counter(r["guard"]["action"] for r in rows)),
            "masked_job_names": sum(len(r["guard"]["bad_jobs"]) for r in rows),
            "state_fixes": sum(1 for r in rows if r["guard"]["state_fix"]),
            "feedback": dict(Counter((r.get("feedback") or {}).get("result", "none") for r in rows)),
            "realtime": True, "writeback": True,
            "active_fair_id": profile.get("fair_id", ""),
        },
        "intent_semantics": intent_sem,
        "rows": rows,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    o = build()
    print("已生成", OUT)
    print("  统计", json.dumps(o["stats"], ensure_ascii=False))
    print("  五项权重", json.dumps(o["weights"], ensure_ascii=False))
    print("\n  第 1 家报告字段:", sorted(o["rows"][0].keys()))
    print("  样例 gaps:", [g["why"] for g in o["rows"][0]["gaps"]])
    print("  样例提问:", [q["q"][:44] for q in o["rows"][0]["questions"]])
