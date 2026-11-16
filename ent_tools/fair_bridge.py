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
import collections
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP


INFER_JOB_KIND = "大模型推断"


def _clean_jobs(jobs):
    """只留真实岗位名：招聘批次名 / 专业类目名 / 招考名目不是岗位。

    真实数据里混进来的噪声：「2027 届校园招聘」（批次名）、「机械类」「材化类」
    （专业类目）、「公务员」「科学研究人员」（事业单位招考名目）。它们既不该
    出现在「本家岗位」里，也不该算进 N 岗，更不该被当成对口证据。

    画像补进来的岗位带 job_source_kind="大模型推断"，是"这类单位通常的编制"，
    不是这家企业今年的招聘计划。它们同样不进真实岗序列——否则 audit 的
    has_jd 会为真、模型给的岗位落进库里就从 inferred 升成明确命中，等于拿
    模型猜的岗位冒充真实 JD（详见 docs/采集流水线与并行调度.md 第 8 节）。
    """
    out = []
    for j in (jobs or []):
        if not isinstance(j, dict) or str(j.get("job_source_kind") or "") == INFER_JOB_KIND:
            continue
        t = str(j.get("title") or "").strip()
        if t and job_level.is_job_title(t):
            out.append(j)
    return out


def _inferred_jobs(jobs):
    """画像补进来的推断岗，单独给页面标注来源用，不参与任何命中判定。"""
    return [j for j in (jobs or []) if isinstance(j, dict)
            and str(j.get("job_source_kind") or "") == INFER_JOB_KIND]


def _round(x, n=1):
    """四舍五入（半上），与前端 JS toFixed 同口径。

    Python 内置 round() 是银行家舍入：round(6.25, 1) = 6.2，而 JS (6.25).toFixed(1)
    = "6.3"。分项贡献正好落在 .25/.75 上时两端会差 0.1 分，排序又按分数比较，
    必须统一，否则「前后端分数一致」这类校验会周期性冒红。
    """
    q = Decimal(1).scaleb(-n)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))

HERE = os.path.dirname(os.path.abspath(__file__))


def _extra_intents():
    """内置测试画像里勾过的意向岗位，用于把语义映射一次跑全。

    只读 data/test_users/*.json 的 answers.intended_jobs；文件缺失或损坏就返回空，
    不影响构建（大不了运行时再补判）。
    """
    out = []
    d = os.path.join(HERE, "data", "test_users")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                u = json.load(f)
            out += [str(x).strip() for x in
                    ((u.get("answers") or {}).get("intended_jobs") or []) if str(x).strip()]
        except Exception:
            continue
    return out
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
OUT = os.path.join(DATA, "fair_payload.json")
# 校招核实新库（build_campus_recheck.py 生成）。read_db 用它覆盖 90 家真实校招
# 企业的岗位与来源标签：岗位只留带 URL 的真实证据岗（推断补全岗不再进链路），
# jobSource 改「校招公告」让 source_class / unknown_infer 自动走校招口径。
CAMPUS_RECHECK = os.path.join(DATA, "campus_recheck.db")


def campus_recheck_enabled():
    """校招核实口径开关：默认开；设 FAIR_CAMPUS_RECHECK=0 可整段退回旧口径。"""
    return (os.environ.get("FAIR_CAMPUS_RECHECK") or "1").strip() != "0"

sys.path.insert(0, os.path.join(HERE, "demo_v2"))
import core as V2            # noqa: E402  复用五项权重 / 维度取值口径
import rules as R            # noqa: E402
import intent_semantics      # noqa: E402  岗位语义符合度（大模型推断）
import hit_verify            # noqa: E402  本地规则候选命中的大模型批量复核
import biz_recheck           # noqa: E402  画像×主营复检（2026-11-17 用户定口径）
import unknown_infer         # noqa: E402  无具体岗企业按意向岗位判定（未知1 / 未知2）
import biz_fit               # noqa: E402  按主营业务判企业与学生方向是否交叉（yes/ask/no）
import job_profile           # noqa: E402  方案一：先建企业岗位画像，再和意向比对
import job_direction         # noqa: E402  岗位方向：按主营推定「工艺工程师」是哪个方向
import hard_filter           # noqa: E402  v12 硬约束解析与教培类判定
import job_level             # noqa: E402  岗位层级：主管/资深 vs 管培生/专员
import cache_guard           # noqa: E402  缓存卫生：代码指纹 / 失效告警

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

# ── 145 家「按参会名单」v4 增补档案（2026-11-15 01:5x 交接包）────────────
# 这是目前唯一一份**按本次参会 145 家逐家重采**的榜单/成长/细分数据，
# 与 company_jobs.name 逐字同名 145/145；v4.2 那份 118 家只与参会名单重叠 22 家，
# 因此这里优先于 v4.2，缺失时才回退。
# 接入口径（照交接说明，勿自行加戏）：
#   · 打分仍读 city / nature / industry / scale_tier，公式一行不改；
#   · 行业地位只读**合并后**的 rank_tier（global_leader / national_leader / niche），
#     **不读 rank_dim**（已统一写成 merged，分族只是回溯用）；
#   · rank_hits / segment / evidence 只展示，绝不进 calcOne；
#   · 17 家非企业主体（学校/党政/事业单位）industry_rank_label=不适用、rank_checked=0，
#     既不能当「普通公司」压低分，也不能当 0 分参与行业地位排序 → 打分层直接跳过该维；
#   · scale_tier=未知 是采集口径选择（不硬凑），不拿 classify_scale 反推。
ENRICH_DB = os.path.join(DATA, "fair2026_enrich.db")
ENRICH_JSON_FIELDS = ("rank_hits", "growth_hits", "work_cities", "jobs_total",
                      "jobs_official", "jobs_platform", "jobs_websearch",
                      "matched_jobs", "inferred_jobs")
ENRICH_ALIAS = {"segment_leader_url": "segment_leader_source_url",
                "segment_leader_conf": "segment_leader_confidence"}


def _no_job_lead(jobs, js):
    """库里没有岗位公告时的引子；有公告就返回空串。"""
    if jobs:
        return ""
    return ("库里没有具体岗位公告，仅有粗略业务描述；" if js == "未知1"
            else "联网检索未取得该单位的具体岗位公告；")


def _fit_mode():
    """业务判定走哪条链路。默认方案一（企业岗位画像）。

    FAIR_FIT_MODE=bizfit 可切回旧链路（模型直接判这家接不接得住学生意向），
    用于对照与回滚 —— 两条链路的返回结构一致，调用方不用改。
    """
    return (os.environ.get("FAIR_FIT_MODE") or "profile").strip().lower()


def fit_all(rows, intended):
    if _fit_mode() == "bizfit":
        return biz_fit.judge_many(rows)
    return job_profile.fit_many(rows, intended)


def fit_one(name, industry="", nature="", segment="", scope="", job_src="",
            titles=None, intended=None):
    if _fit_mode() == "bizfit":
        return biz_fit.judge(name=name, industry=industry, nature=nature,
                             segment=segment, scope=scope, job_src=job_src,
                             titles=titles, intended=intended)
    return job_profile.judge(name=name, industry=industry, nature=nature,
                             segment=segment, scope=scope, job_src=job_src,
                             titles=titles, intended=intended)


def load_enrich():
    """读 145 家增补档案；文件缺失或空库时静默跳过，旧链路照常可跑。

    空库这一支是补的：某个只做探测的 sqlite3.connect() 会在路径上留下 0 字节文件，
    之后 `SELECT * FROM companies` 就抛 no such table，整条构建链在第一步崩掉，
    报错还指向这里而不是那个空文件。文件在但没表 = 等同于文件缺失。
    """
    if not os.path.exists(ENRICH_DB) or os.path.getsize(ENRICH_DB) == 0:
        return {}
    con = sqlite3.connect(ENRICH_DB)
    con.row_factory = sqlite3.Row
    try:
        out = {}
        try:
            rows = list(con.execute("SELECT * FROM companies"))
        except sqlite3.OperationalError:
            print("[fair_bridge] %s 里没有 companies 表，按无增补档案处理"
                  % os.path.basename(ENRICH_DB))
            return {}
        for row in rows:
            d = dict(row)
            for k in ENRICH_JSON_FIELDS:
                v = d.get(k)
                if isinstance(v, str) and v[:1] in "[{":
                    try:
                        d[k] = json.loads(v)
                    except Exception:
                        pass
            for a, b in ENRICH_ALIAS.items():
                if a in d and not d.get(b):
                    d[b] = d[a]
            raw = {}
            n = (d.get("nature") or "").strip()
            if n in NATURE_TO_BUCKET:
                raw["nature"] = n
                d["nature"] = NATURE_TO_BUCKET[n]
            d.setdefault("non_enterprise", 0)
            d["_raw"] = raw
            d["_src"] = "v4-enrich-20261115"
            out[d["name"]] = d
        return out
    finally:
        con.close()


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


def apply_campus_recheck(companies):
    """用校招核实新库覆盖真实校招企业的岗位与来源（只改内存对象，不写回产品库）。

    - 90 家校招企业：jobs 换成带 URL 的真实证据岗（推断补全岗自动消失），
      jobSource 改「校招公告」，让 source_class 判 campus、unknown_infer 不再
      触发按行业推断补全。
    - 原标「校招公告」但核实证据不足的：降级 websearch（岗位保留，只改来源判定）。
    - 其余 web/未知/社招企业：原样保留，继续走既有兜底。
    返回 (认校招家数, 删除推断岗数, 降级家数) 供验收核对。
    """
    if not campus_recheck_enabled() or not os.path.exists(CAMPUS_RECHECK):
        return 0, 0, 0
    # 库存在但读失败必须报错，不能静默吞掉退回旧口径 —— 之前治理过「静默异常」，
    # 这里若 except: pass， misleading tag 会悄悄复活且没人发现。
    rc = sqlite3.connect(CAMPUS_RECHECK)
    try:
        rc.row_factory = sqlite3.Row
        campus = {r["name"]: int(r["is_campus"])
                  for r in rc.execute("SELECT name,is_campus FROM campus_company")}
        jobs = collections.defaultdict(list)
        for r in rc.execute("SELECT company,title,source_url,channel FROM campus_jobs"):
            jobs[r["company"]].append({
                "title": r["title"], "requirements": "", "headcount": "未知",
                "urgency": "未知", "group": "", "salary": "未知",
                "source_url": r["source_url"], "channel": r["channel"],
            })
    finally:
        rc.close()
    changed = removed = demoted = 0
    for name, c in companies.items():
        is_campus = campus.get(name)
        if is_campus:
            # 认定为校招：岗位只留真实证据岗（推断补全岗消失），改校招标签
            old_n = len(c.get("jobs") or [])
            c["jobs"] = jobs.get(name, [])
            c["jobSource"] = "校招公告"
            c["jobEvidence"] = "校招核实：岗位均带真实来源URL"
            c["verified_web"] = 1
            changed += 1
            removed += max(0, old_n - len(c["jobs"]))
        elif c.get("jobSource") == "校招公告":
            # 原标校招公告但核实后证据不齐（<3岗或无证据）：降级 websearch。
            # 岗位保留（可能是真岗，只是证明不了齐全），只改来源判定 —— 降级≠删岗。
            c["jobSource"] = "websearch"
            c["note"] = str(c.get("note") or "") + "；校招核实证据不足，降级websearch(2026-11-16)"
            demoted += 1
    return changed, removed, demoted


def read_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        companies = {}
        for r in con.execute("SELECT name,item_json FROM company_jobs"):
            companies[r["name"]] = json.loads(r["item_json"])
        apply_campus_recheck(companies)
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
        # 学生一个意向都没勾时会回退到 DEFAULT_PROFILE 的意向（让 demo 能跑）。
        # 但那不是学生选的：命中判定必须知道这件事，否则「数据分析师 / 供应链计划」
        # 这些默认岗会被当成学生亲口勾的，判出「明确命中」——学生根本没填过它们。
        "intents_defaulted": not any(str(x).strip() for x in (ans.get("intended_jobs") or [])),
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
        # 成长性按用户口径「没数据就记 0」，不关停、不回填权重；
        # rebalance_weights 在 GROWTH_ENABLED=True 时是空操作，保留只为应急开关。
        return R.rebalance_weights(w)
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
    return R.rebalance_weights(w)


def dynamic_gaps(c, llm_row, booth):
    """按这家企业真实缺什么生成 gaps，取代模型那 6 种模板。"""
    g = []
    jobs = _clean_jobs(c.get("jobs"))

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
    # 作息：数本家岗位原文里「双休」的出现次数，别再写死 0/277（换名单后就不成立了）
    _rest = sum(1 for j in jobs if "双休" in str(j.get("requirements", "")))
    put("作息", "公开 JD 中「双休」出现 %d/%d" % (_rest, len(jobs)), 0.2)
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

    # —— 城市 / 离家近：都按「全部工作城市」取最优，与页面 calcOne 的 workCities 一致 ——
    # 校招公告常写多个工作地（如 上海/天津/南京），只认主城市会把同一家企业算低。
    # 「未知」不能当城市用：以前它被带进 wcs，导致 city 维算不出（被移出分母），
    # 而 home 维又拿「未知」算出个 0.3 —— 同一份缺失数据两套处理，属于 bug。
    wcs = [x for x in (c.get("work_cities") or [])
           if x and str(x).strip() not in ("", "未知")]
    _c0 = str(c.get("city") or "").strip()
    if not wcs and _c0 and _c0 != "未知":
        wcs = [_c0]
    cr = profile.get("city_rank") or dict(R.CITY_RANK_DEFAULT)
    best = None
    for cc in wcs:
        for cat in R.city_categories(cc):
            if cat in cr and (best is None or cr[cat] < best[1]):
                best = (cat, cr[cat], cc)
    if best:
        best_cat, best_rank, best_city = best
        v = R.rank_value(best_rank, len(R.CITY_CATS))
        vals["city"] = (v, "工作城市 %s 命中「%s」(你的第%d优先)" % (best_city, best_cat, best_rank))
    else:
        vals["city"] = (0.0, "工作城市未查到，本项记 0")

    # —— 离家近：同城 / 同省 / 邻省 / 其他，多城市取最近 ——
    home = profile.get("home_city")
    if home and wcs:
        best_h = None
        for cc in wcs:
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
            if best_h is None or v > best_h[0]:
                best_h = (v, cc)
        vals["home"] = (best_h[0], "离家最近的工作地 %s / 家乡 %s" % (best_h[1], home))
    else:
        vals["home"] = (0.0, "无有效工作城市，离家无法计算，本项记 0")

    # —— 企业类型：直接用画像对「央企/国企/民企/外企/事业单位」的真实排序 ——
    # 2026-11-15 修 bug：以前把 nature 归一成「国央企」三桶再去查画像键，
    # 而画像键是央企/国企/事业单位 5 档 → 桶名查不到，24 家国企央企事业单位整维丢失；
    # 民企则按 3 档算（rank 3 of 3 = 0.4），实际是 5 档第 3（0.7）→ 全体低估 4.5 分。
    ctr = profile.get("company_type_rank") or dict(R.COMPANY_TYPE_RANK_DEFAULT)
    nat = str(c.get("nature") or "").strip()
    if nat in ctr:
        v = R.rank_value(ctr[nat], len(ctr))
        vals["company_type"] = (v, "性质 %s（你的第%d优先）" % (nat, ctr[nat]))
    elif nat and nat != "未知":
        # 混合所有制等未进排序的写法：按末档，不臆造优先
        vals["company_type"] = (R.rank_value(len(ctr) + 1, len(ctr) + 1),
                                "性质 %s（不在你的排序内，按末档）" % nat)
    else:
        vals["company_type"] = (0.0, "企业性质未查到，本项记 0")

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
    # '未知' 以前会被 or 短路掉、根本不走推断（14 家非企业主体就这样丢了一整维）
    st = str(c.get("scale_tier") or "").strip()
    if st not in R.SCALE_TIERS:
        st = R.classify_scale(c.get("nature"), c.get("name", ""), c.get("industry"))
    if st in R.SCALE_TIERS:
        vals["scale"] = (R.SCALE_TIERS[st], "规模档 %s（体制/上市/集团客观判定）" % st)
    else:
        vals["scale"] = (0.0, "规模档无法判定，本项记 0")

    # —— 行业地位 / 成长性：读增补档案合并后的档位 ——
    # 用户口径：**榜单里没有的就是 0**，不给中性底板分。
    # 2026-11-15 修 bug：以前非企业主体「跳过该维 → pool 不含其权重 → 按其余维度归一」，
    # 结果只有 2~3 维的企业（#85/#86）用 25 分池归一，34 分其实等于「两维平均 34」，
    # 与 7 维满池的企业分数不可比。现在一律记 0、留在分母，只在依据里写明原因。
    _ne = int(c.get("non_enterprise") or 0)
    rt = str(c.get("rank_tier") or "").strip()
    if rt in R.INDUSTRY_STATUS_TIERS:
        vals["industry_status"] = (R.INDUSTRY_STATUS_TIERS[rt],
                                   "行业地位 %s（第三方榜单 / 500强）" % rt)
    elif _ne:
        vals["industry_status"] = (R.INDUSTRY_STATUS_FLOOR,
                                   "非企业主体（学校/党政/事业单位），无第三方榜单，本项记 0")
    else:
        vals["industry_status"] = (R.INDUSTRY_STATUS_FLOOR, "无公开榜单数据，本项记 0")

    gt = str(c.get("growth_tier") or "none").strip()
    if gt in R.GROWTH_TIERS:
        vals["growth"] = (R.GROWTH_TIERS[gt], "成长性 %s" % gt)
    elif gt == "unknown":
        vals["growth"] = (R.GROWTH_FLOOR, "成长性未查到（记 0，不臆造）")
    elif _ne:
        vals["growth"] = (R.GROWTH_FLOOR,
                          "非企业主体，无成长类榜单，本项记 0")
    elif R.GROWTH_ENABLED:
        vals["growth"] = (R.GROWTH_FLOOR, "无成长性数据，本项记 0")
    else:
        vals["growth"] = (0.0, "无成长性数据（本项不计分，权重已回填其他维度）")

    # 分母恒为画像权重总和：漏掉的维度按 0 分计入，**不再归一放大**。
    total = sum(W.values()) or 100
    out = []
    for k, w in ((d["id"], W.get(d["id"], 0)) for d in R.DIMENSIONS):
        v, basis = vals.get(k, (0.0, "本项无数据，记 0"))
        out.append({"id": k,
                    "name": next(x["name"] for x in R.DIMENSIONS if x["id"] == k),
                    "value": _round(v, 2), "weight": w,
                    "contribution": _round(w / total * v * 100, 1), "basis": basis})
    return {"score": _round(sum(x["contribution"] for x in out), 1),
            "effective_weight": _round(total, 1),
            "parts": out, "pool": _round(total, 1)}


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


# 工商信息页（爱企查 / 企查查 / 天眼查 / 启信宝 / 国家企业信用公示）。
# 这些页面能查到企业名称、经营范围、注册地址，但**不是招聘公告**：
# 从上面抓来的「岗位名」只是经营范围或简介里的词，不等于这家在招这个岗。
# 中建一局曾凭 aiqicha 工商页拿到 state=hit / trust=high，页面显示「真实 JD 命中」，
# 实际没有任何招聘原文。这里统一按来源域名挡一次，不删企业、不清岗位，
# 只降证据等级、改成推断待核实，让「有真实 JD」这组名副其实。
_NON_JD_HOSTS = ("aiqicha.baidu.com", "qcc.com", "tianyancha.com",
                 "qixin.com", "11315.com", "gsxt.gov.cn")


def _is_non_jd_source(c):
    """证据 URL 指向工商信息页（不是招聘公告）时为真。"""
    u = str(c.get("source") or "").lower()
    return any(h in u for h in _NON_JD_HOSTS)


def audit(c, m, all_titles=None, by_title=None):
    """企业端事实校验：把模型结论与企业真实 JD / 行业 / 全库岗位名对一遍。"""
    notes, trust = [], "high"
    all_titles = all_titles or {}
    jobs = _clean_jobs(c.get("jobs"))
    titles = {j.get("title", "") for j in jobs}
    ind = c.get("industry", "未知")

    if _is_non_jd_source(c) and titles:
        notes.append("岗位记录来自工商信息页（非招聘公告），只作线索，请现场核实是否真招")
        if trust == "high":
            trust = "medium"

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


def _dedup(seq):
    """保序去重。企业岗位表里同名岗位常有重复行（金田「工艺工程师/主管/经理」两条），
    同一岗位可能同时命中多个意向（睿创「品质」），展示前必须去重。"""
    out, seen = [], set()
    for x in seq or []:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


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
    # 只认真实岗位：大模型推断岗（job_source_kind=大模型推断）不是 JD 证据，
    # 否则补过推断岗的 0 真实岗企业会被误判「有 JD」，hit 不降级（2026-11-16）。
    titles = {j.get("title", "") for j in _clean_jobs(c.get("jobs"))}
    # 工商信息页不是招聘公告：即便上面挂着几条「岗位」，也不算真实 JD，
    # 否则 hit 组会混进「标成明确命中、点开却是工商页」的企业（中建一局那类）。
    has_jd = bool(titles) and not _is_non_jd_source(c)
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
        if titles and _is_non_jd_source(c):
            parts.append("岗位记录只来自工商信息页、不是招聘公告，模型标「明确命中」依据不足，已下调为推断待核实")
        else:
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


_SOURCE_LABEL = {
    "社招信息": "社招信息（招聘平台）",
    # v17：联网搜到的岗位记录统一标 websearch（不再是「未知」）；
    # 早期「主营业务推测」已按用户口径在库里降为「未知」并清空岗位。
    "websearch": "websearch",
    # v18 两类「无具体岗」：走大模型按意向岗位判断的推断分支，不进有岗位文案
    "未知1": "未知1（有粗略业务描述）",
    "未知2": "未知2（联网检索无岗位）",
}


def source_class(company, jobs):
    """岗位信息来源分类，返回 (kind, 可读来源名)。

    kind='campus'：校招公告，岗位列表视为齐全 —— 里面没有对口岗位就判定不对口。
    kind='other' ：websearch / 社招 / 平台转载等，不保证齐全 —— 保留并提示现场确认。
    「未知」表示岗位信息已按未知处理（jobs 为空）；此时走 0 岗推断分支。
    没有整级来源标注时，用首条岗位的 source_url 域名给出可读来源名。
    """
    js = str(company.get("jobSource") or "").strip()
    if js == "校招公告":
        return "campus", "校招公告"
    if js and js != "未知":
        return "other", _SOURCE_LABEL.get(js, js)
    for j in (jobs or [])[:5]:
        u = str((j or {}).get("source_url") or "").lower()
        if not u:
            continue
        if "mp.weixin.qq.com" in u:
            return "other", "微信公众号推文"
        if any(k in u for k in ("51job", "zhaopin", "liepin", "zhipin", "boss")):
            return "other", "招聘平台"
        if any(k in u for k in (".edu.cn", "91job", "iguopin", "wllm", "bys")):
            return "other", "高校就业网 / 就业平台"
        return "other", "企业官网或公开页面"
    if "交付库" in str(company.get("jobEvidence") or ""):
        return "other", "交付库的官方 / 转载页面"
    return "other", "公开渠道汇总"


def load_dir_hint():
    """读 job_dir_hint 表 → {企业名: {岗位名: {"dirs": [簇], "cn": 中文}}}。

    这张表由 `_dir_fill.py` 预先算好（岗位方向按主营业务推定），好处是**学生打开
    页面时方向已经在那儿**，不需要现场问模型，也就没机会被模型的迎合倾向污染。
    表不存在 / 没跑过时返回 {}，调用方会退回按 `c.biz_scope` 现算（同一套词表，
    结果一致，只是多花几毫秒）。
    """
    out = {}
    try:
        con = sqlite3.connect(DB)
        for name, title, d, cn, alts in con.execute(
                "SELECT name,title,dir,dir_cn,alts FROM job_dir_hint"):
            if not d:
                continue
            out.setdefault(name, {})[title] = {
                "dirs": [d] + [x for x in str(alts or "").split("、") if x],
                "cn": cn or "", "src": "biz"}
        con.close()
    except Exception:
        return {}
    return out


def dir_hints_for(name, titles, biz_text, book=None):
    """给一家企业的岗位名准备方向表；表里没有的现算（同一套 job_direction）。"""
    book = book or {}
    per = book.get(name) or {}
    out = {}
    for t in titles or []:
        t = str(t or "").strip()
        if not t:
            continue
        h = per.get(t)
        if h:
            out[t] = h
            continue
        r = job_direction.resolve(t, biz_text)
        if r["dirs"]:
            out[t] = {"dirs": [c for c, _k in r["dirs"]], "cn": r["cn"],
                      "src": r["src"]}
    return out


# ----------------------------------------------------------- build() 的零件
#
# build() 原来 736 行，其中最大的一个 for 循环占 418 行。它是这个项目里
# 最典型的「改一行不知道会坏在哪」——但拆它也是风险最高的活：任何一点语义
# 变化，页面上的分数 / 命中 / 问句都会跟着变，而这类错误**不会报错**。
#
# 所以拆的规则只有一条：**整段搬进函数，一行逻辑都不改**。
# 唯一允许的改动是去掉 `_c` `_v` 这类下划线前缀（它们本来就是函数内私有的）。
# 注释原样跟着搬 —— 那些注释记的是踩过的坑，比代码本身值钱。
#
# 验收方式固定（`docs/改动守则.md` 第 5 条）：
#     PYTHONHASHSEED=0 python fair_bridge.py
# 连跑两遍、抹掉时间戳后 payload 逐字相同，才算搬对了：
#     python -X utf8 scratch/_payload_diff.py 旧.json 新.json


def _merge_sources(companies, enrich, v42):
    """把 145 家增补档案 / v4.2 的字段并进活库企业对象（原地改），返回来源计数。

    数据来源优先级：145 家增补档案（同名单、逐字同名）> v4.2（22 家重叠）。
    覆盖 4 个打分字段 + 两维档位 + 多城市；其余字段只作展示追加。
    被覆盖的旧值留在 _enrich_diff 里，页面/日志可逐条核对，改错能看见。
    """
    src_count = {"enrich": 0, "v42": 0}
    for name, c in companies.items():
        e = enrich.get(name)
        v = e or v42.get(name)
        if not v:
            continue
        src_count["enrich" if e else "v42"] += 1
        diff = {}
        for k in ("city", "nature", "scale_tier",
                  # 榜单/成长档位也是打分字段（无值即 0），必须带进 c
                  "rank_tier", "growth_tier",
                  # 多城市招聘：页面按 work_cities 取最优，后端必须同口径
                  "work_cities"):
            new = v.get(k)
            # 「未知」不是值：档案里 city/scale 的「未知」只是没查到，不能覆盖
            # 活库已查到的旧值（电科55所「贵阳」曾被档案「未知」冲掉 → 城市维
            # 全 0）。与 save_item 的「未知不覆盖有值」同一原则。
            if not new or str(new).strip() in ("", "未知"):
                continue
            old = c.get(k)
            if old and str(old) != str(new):
                diff[k] = [old, new]
            c[k] = new
        # 行业**不整表覆盖**：增补档案走工商门类口径（风电/光伏→制造、地产→建筑），
        # 本项目的 industry 是招聘/业务口径，整表覆盖会重演「华为 IT→制造」的错判。
        # 只补空值，原值保留；档案口径原样存 industry_raw 供展示与回溯。
        ind_new = (v.get("industry") or "").strip()
        if ind_new:
            c["industry_raw"] = ind_new
            if not (c.get("industry") or "").strip() or c.get("industry") == "未知":
                c["industry"] = ind_new
            elif c.get("industry") != ind_new:
                diff["industry(工商口径,仅展示)"] = [c.get("industry"), ind_new]
        c["non_enterprise"] = int(v.get("non_enterprise") or 0)
        c["data_src"] = v.get("_src") or ("v4.2" if not e else "v4-enrich-20261115")
        if diff:
            c["_enrich_diff"] = diff
    return src_count


def _apply_hard_constraints(companies, raw_profile):
    """v12（2026-11-13 用户要求）：硬约束过滤，返回 (企业, 被删清单, 解析结果)。

      ① 学生那句自由填空（「只要南京」「只看互联网」）由大模型解析成条件，
         不满足的企业**直接从名单里删除**，不是降权。
      ② 教培类企业默认剔除，学生明确表示「愿意考虑」才保留。
    过滤发生在后续所有计算之前，被删的企业不会出现在 rows 里。
    """
    conds = hard_filter.build_conditions(raw_profile, list(companies.values()))
    dropped = []
    if conds["hard"].get("mode") not in (None, "none") or conds["drop_training"]:
        keep = {}
        for name, c in companies.items():
            if conds["drop_training"] and hard_filter.is_training_biz(c):
                dropped.append({"name": name, "why": "教培类（学生未选择接受）"})
                continue
            if not hard_filter.match(c, conds["hard"]):
                dropped.append({"name": name, "why": "不满足硬约束：" + (conds["raw_text"] or "")})
                continue
            keep[name] = c
        companies = keep
    return companies, dropped, conds


def _drop_miss(companies, llm):
    """v15：岗位不对口的企业不进入榜单（不是降权，是直接删除）。

    只排除 llm_match 明确判定 state=miss 的；0 岗（没查到）与 inferred（推断待核实）
    一律保留，且**不降权、不顺延**：score 只衡量城市 / 性质 / 规模 / 行业地位等
    硬件条件，与「今年招什么岗」无关。0 岗只是没抓到岗位信息，不等于不招；
    只要按行业推断出了可能的岗位，高分就说明客观条件高度适配，应该优先去展位
    问一句碰运气，而不是先去找分数更低的企业（「没查到 ≠ 不招」是既有红线）。
    前提：这类企业的报告必须带「显著提示 + 现场提问」，把不确定性讲清楚。
    """
    miss_names = []
    keep_matched = {}
    for name, c in companies.items():
        # 未知1 / 未知2 一律不被旧画像的 miss 结论排除：它们没有具体岗位，
        # 「没查到 ≠ 不招」，必须交给匹配端大模型按当前意向岗位判断。
        if str(c.get("jobSource") or "") in ("未知1", "未知2"):
            keep_matched[name] = c
            continue
        if (llm.get(name) or {}).get("state") == "miss":
            miss_names.append(name)
            continue
        keep_matched[name] = c
    return keep_matched, miss_names


def _drop_campus_unfit(companies, llm):
    """v17：来源是完整校招公告、却没查到对口岗位的企业 → 判定不对口，直接排除。

    校招公告的岗位列表视为齐全，里面都没有对口岗位就不用再去现场碰运气；
    其他来源（websearch / 社招 / 平台转载）不保证齐全，保留并在报告里如实说明。
    早期「主营业务推测」的岗位已在库里统一降为「未知」并清空，这类企业走 0 岗推断。
    """
    campus_names = []
    keep_src = {}
    for name, c in companies.items():
        # 只认真实岗位：推断岗不能当「校招公告岗位齐全」的证据，否则补过推断岗的
        # 企业会被误判「公告里没有对口岗」而整家排除（2026-11-16）。
        jobs = _clean_jobs(c.get("jobs"))
        if ((llm.get(name) or {}).get("state") == "inferred" and jobs
                and source_class(c, jobs)[0] == "campus"):
            campus_names.append(name)
            continue
        keep_src[name] = c
    return keep_src, campus_names


def _index_titles(companies):
    """岗位名 → 有这个名字的企业集合。证据归属、幻觉校验都靠它。

    只索引真实岗位：推断岗进了索引，幻觉校验就会把模型编的岗位名
    「核对」成推断岗的名字，等于自己给自己作保（2026-11-16）。
    """
    by_title = {}
    for c in companies.values():
        for j in _clean_jobs(c.get("jobs")):
            by_title.setdefault(j.get("title", ""), set()).add(c.get("name", ""))
    return by_title


def _prefetch_fit(companies, llm, enrich, v42, profile):
    """业务判定预取（并发），返回 {企业名: fit_info}。

    原先是循环里逐家同步调模型：31 家 × 0.7~1.0s ≈ 30s，全压在首屏。
    这里先粗筛出「可能判 inferred」的企业，一次性并发判完；循环里改为查表。
    粗筛只多不少（宁可多算几家进缓存），最终是否采用仍由循环里的
    m_eff["state"] 决定，判定结果与串行逐家完全一致。
    """
    fit_rows = []
    for name, c in companies.items():
        m0 = llm.get(name) or {}
        st0 = m0.get("state", "miss")
        if (st0 == "miss" and str(c.get("jobSource") or "") in ("未知1", "未知2")
                and not c.get("jobs")):
            st0 = "inferred"
        if st0 != "inferred":
            continue
        fit_rows.append({
            "name": name,
            "industry": c.get("industry", ""),
            "nature": c.get("nature", ""),
            "segment": (v42_view(enrich.get(name) or v42.get(name)) or {}).get("segment", ""),
            "scope": c.get("biz_scope", ""),
            "job_src": str(c.get("jobSource") or ""),
            "titles": [j.get("title", "") for j in _clean_jobs(c.get("jobs"))],
            "intended": list(profile.get("intended_jobs") or []),
        })
    if not fit_rows:
        return {}
    return fit_all(fit_rows, list(profile.get("intended_jobs") or []))


def _inferred_text(state, fit, fit_info, basis, fj, evsuf, jobs, js, src_label,
                   intended_ask):
    """推断企业那句「显著提示」正文。纯格式化，不碰任何数据。

    分三种口径：
      - 库里有岗位相关记录（websearch / 社招 / 平台转载）：如实说有 N 条
        记录、未查到对口证据、来源不保证齐全 → 按意向岗位建议现场确认。
      - 未知1 / 未知2（两类「无具体岗」）：匹配端一视同仁，由大模型按
        学生当前勾选的意向岗位挑出可能招的 X；判空就如实说关联较弱，
        绝不把假岗位或别的画像结论当成事实。
      - 其他 0 岗：中性兜底。
    校招公告来源且无对口岗位的企业已在过滤阶段直接排除。
    """
    # 「这家行业里对应的是哪类岗」——判 yes/ask 的依据都摊开给学生看。
    if state != "inferred":
        return ""
    if fit == "no":
        # 只说「这次别排队」，不说「人家不招」——0 岗 ≠ 不招仍是红线
        return ("%s。这个展位建议跳过——不是不招人，是方向与你不交叉。"
                % (basis or "主营在另一个领域"))
    if fit == "yes":
        # 方案一的结论来自「这类单位的岗位构成」，不是这家单位今年的招聘公告 ——
        # 必须写明来源，否则等于把模型的行业常识摆成企业的话。
        src = ("（岗位构成为模型按行业惯例所列，非该单位招聘公告）"
               if fit_info.get("mode") == "profile" else "")
        return ("%s%s%s%s。建议现场确认是否招%s。"
                % (_no_job_lead(jobs, js), basis or ("主营与「%s」方向有交叉" % fj),
                   evsuf, src, fj))
    if fit_info.get("mode") == "profile":
        # 方案一的 ask 只有一种原因：画像没列全（岗位数不够或写不出链条），
        # 不是「业务沾边但方向不明」。旧句「可能与 X 沾边」在党政机关上尤其
        # 别扭（组织部不会招自动化工程师），换成中性说法。
        return ("%s%s。建议现场问一句是否招%s。"
                % (_no_job_lead(jobs, js),
                   basis or "主营与意向是否交叉不好判断", fj))
    if jobs:
        return ("库里有 %d 条岗位相关记录（其中部分来自%s），"
                "没有与%s明确对得上的；%s%s。方向不确定，值得问一句。"
                % (len(jobs), src_label, intended_ask,
                   basis or "主营与意向只是沾边", evsuf))
    lead = ("库里没有具体岗位公告，仅有粗略业务描述；" if js == "未知1"
            else "联网检索未取得该单位的具体岗位公告；")
    return ("%s%s%s。可能与%s沾边，建议问一句是否招。"
            % (lead, basis or "主营与意向只是沾边", evsuf, fj))


def _questions(gaps, W, state, fit, infer_text, ask_question):
    """现场提问列表：权重 × (1 - 证据可信度)，推断岗位固定排第一。"""
    qs = []
    if state == "inferred":
        # 判 no 时不能还跟一句「现场问是否招 X」——前面刚说「建议跳过」，
        # 后面又问「招不招」，学生照问就等于把 no 又当成不确定。
        ask_suffix = (("（这个展位已按方向不交叉折叠；若还想确认，可问：" + ask_question + "）")
                      if fit == "no" else ("（现场问一句：" + ask_question + "）"))
        qs.append({"q": "【显著提示】" + infer_text + ask_suffix,
                   "priority": 999, "infer": True})
    for g in gaps:
        wmap = {"岗位": W["industry"], "行业": W["industry"], "职责": W["scale"],
                "阶段": W["scale"], "薪资": W["scale"], "人数": W["scale"],
                "城市": W["city"], "性质": W["company_type"], "作息": W["city"], "证据": W["scale"]}
        qs.append({"q": g["question"], "priority": round(wmap.get(g["kind"], 10) *
                   (1 - g["confidence"]), 1), "infer": False,
                   "why": g["why"]})
    qs.sort(key=lambda x: -x["priority"])
    return qs


def _hits_and_misses(matched_jobs, jobs):
    """命中 / 未对口两栏的展示清单（截断）+ 去重后的真实条数。

    命中岗位：以判定结果 matched_jobs 为准。
    旧写法是「在 JD 前 6 条里取交集」——排到第 7 位之后的命中会被整段吞掉，
    于是出现「对口理由写了 2 个岗位、命中岗位只显示 1 个」，两栏加起来也不等于
    判定结果（睿创微纳的「测试」排第 12 位就这样消失）。列表只做数量截断，
    并如实记录总数，页面据此标「等 N 个」，不再是悄悄少显示。
    """
    seen_h, hits_all = set(), []
    for t in matched_jobs:
        t = (t or "").strip()
        if t and t not in seen_h:
            seen_h.add(t)
            hits_all.append(t)
    hits = hits_all[:6]
    # 未对口：只作「这家还招别的什么方向」的示例，按 JD 原序取前 5 条，
    # 与命中互斥（同一岗位名不会同时出现在两栏）。
    seen_m, misses_all = set(), []
    for j in jobs:
        t = (j.get("title") or "").strip()
        if not t or t in seen_h or t in seen_m:
            continue
        seen_m.add(t)
        misses_all.append(t)
    misses = misses_all[:5]
    title_total = len({(j.get("title") or "").strip()
                       for j in jobs if (j.get("title") or "").strip()})
    return hits, hits_all, misses, misses_all, title_total


def _final_state_notes(m, m_eff, trust, check_notes, fbrow, dropped_jobs, dir_notes,
                       real, asks, level_blocked=None):
    """v7 现场核实回流 + 证据说明，返回 (最终状态, 可信度, 说明清单, 是否现场确认招)。

    「现场确认招了」要同时做三件事：状态从推断升为明确命中、可信度抬一档、
    把「显著提示」那条问句撤掉（那是在问「招不招」，已经知道答案了）。
    撤问句这一步交给调用方，这里只回一个标记。
    """
    state_final = m_eff["state"]
    trust_final, notes_final = trust, list(check_notes)

    # 对齐剔除的岗位名要在页面上说清楚，否则学生看到「明确命中」却少了几个岗位会以为丢数据。
    # 剔除清单里混着两类：「不同职能」和「同职能但档位太高（主管/资深）」。
    # 旧版一律说成「不同职能」，学生看到「结构工程师（资深）被剔」会以为专业不对口，
    # 实则是校招投不进那一档 —— 分开说，档位那类直接引用 job_level 的原文理由。
    _lvl_jobs = {a.get("job") for a in (level_blocked or [])}
    # 部分职级岗在 align_jobs 阶段就被剔了，没走到 job_level（level_blocked 为空），
    # 于是「高级药物分析研究员」「资深有机合成研究员」「项目主管工程师」被算进
    # 「不同职能」——学生看到会以为自己专业不对口，实则是校招投不进那一档。
    # 岗名里带明确职级前缀的，从「不同职能」清单里摘出来，按档位说。
    _LV_STRICT = re.compile(r"高级|资深|主管|副经理")
    _lvl_named = {t for t in dropped_jobs if _LV_STRICT.search(t)}
    _lvl_dropped = [t for t in dropped_jobs if t in _lvl_jobs or t in _lvl_named]
    _func_dropped = [t for t in dropped_jobs if t not in _lvl_jobs and t not in _lvl_named]
    if _func_dropped:
        notes_final.append(
            "已按你选的意向剔除 %d 个不同职能的岗位名（%s）；企业仍照常参与排序"
            % (len(_func_dropped), "、".join(_func_dropped[:3])))
    if _lvl_dropped:
        _why = next((a.get("why") for a in (level_blocked or [])
                     if a.get("job") in _lvl_dropped and a.get("why")), "")
        notes_final.append(
            "另有 %d 个同职能岗位属资深/主管档、校招一般投不进（%s）%s；企业仍照常参与排序"
            % (len(_lvl_dropped), "、".join(_lvl_dropped[:3]),
               "：" + _why if _why else ""))
    # 命中的岗位名里，有些本身不带方向（「工艺工程师」「质量工程师」）。方向是按
    # 这家主营推定的，必须写明，否则学生会以为企业自己写了「化工工艺」。
    dir_hit = [d for d in dir_notes if d["job"] in real]
    if dir_hit:
        exact = [d for d in dir_hit if d.get("exact")]
        coarse = [d for d in dir_hit if not d.get("exact")]
        if exact:
            jobs_seen, cn_seen = [], []
            for d in exact:
                if d["job"] not in jobs_seen:
                    jobs_seen.append(d["job"])
                if d["cn"] and d["cn"] not in cn_seen:
                    cn_seen.append(d["cn"])
            notes_final.append(
                "「%s」这个岗位名本身不带方向，方向按这家主营业务推定为%s"
                % ("、".join(jobs_seen[:3]), "、".join(cn_seen[:2])))
        if coarse:
            cr_jobs = []
            for d in coarse:
                if d["job"] not in cr_jobs:
                    cr_jobs.append(d["job"])
            notes_final.append(
                "「%s」不带方向，这家主营是%s方向；这一支的工艺岗也收相邻专业，"
                "算可投，但方向不完全一致，投递前问清具体做什么"
                % ("、".join(cr_jobs[:3]), coarse[0]["cn"]))
    abstain_ask = [a for a in asks if a.get("kind") == "abstain"]
    if m["state"] == "hit" and m["matched_jobs"] and not real:
        if abstain_ask:
            _names = "、".join("「%s」" % a.get("job") for a in abstain_ask[:3])
            notes_final.append(
                "原「明确命中」的岗位名与你的意向是相邻说法，复核未下结论，"
                "已改为推断待核实；%s留作现场问一句（不是不同职能、也没说这家不招）" % _names)
        elif _lvl_dropped:
            # 降级原因是「同职能但档位太高」，不是职能不对口 —— 别再误称不同职能。
            notes_final.append(
                "原「明确命中」的岗位是资深/主管档、校招一般投不进，"
                "已改为推断待核实（不是职能不对口）")
        elif _func_dropped:
            notes_final.append("原「明确命中」的岗位名与你的意向不同职能，已改为推断待核实")
        else:
            # 对齐后没剩任何对口证据，但也说不清是职能/职级/弃权哪一类
            # （例如只是同名跨方向），用中性话术，不硬扣「不同职能」的帽子。
            notes_final.append("原「明确命中」的岗位对齐后没有留下对口证据，已改为推断待核实")
    # 岗位名不带方向、这家按主营是别的方向：不假装对口，把方向差异写出来。
    # （align_jobs_detail 的 dir 类 ask，与「管培生轮岗」那类 ask 分开说）
    dir_ask = [a for a in asks if a.get("kind") == "dir"]
    if dir_ask and state_final != "hit" and len(notes_final) < 6:
        notes_final.append(dir_ask[0]["why"])
    confirmed = False
    if fbrow and fbrow["result"] == "yes":
        if state_final == "inferred":
            state_final = "hit"
        trust_final = "high" if trust_final != "low" else "medium"
        notes_final.append("现场已确认招此岗位（%s），推断升级为明确命中" % fbrow["updated_at"])
        confirmed = True
    elif fbrow and fbrow["result"] == "no":
        notes_final.append("现场确认不招（%s），已顺延，排在队列末尾" % fbrow["updated_at"])
    return state_final, trust_final, notes_final, confirmed, dir_hit


def _evidence(name, c, m, intent_sem, hit_verdicts, by_title, dir_book, intended,
              profile, W, bm):
    """一家企业的「对口证据」：对齐 → 命中复核 → 幻觉脱敏 → 可信度 → gaps → 打分。

    这是整条链路里最要命的一段，也是在 build() 里藏了最久的 79 行。它干的事：

      1. 把行级 matched_jobs 按当前画像对齐（只收紧岗位名，不删企业）；
      2. 用 hit_verdicts 否决「只共享研发/工程这类词」的假命中；
      3. hallucination_guard 把念不出来的岗位名脱敏，并对「原来说命中、其实没证据」
         的行降级；
      4. 反向：真实岗位名里确实对口的，升为明确命中（只降不升会让前后端打架）；
      5. 重跑一次 audit，再生成 gaps 与五项分。

    返回一个 dict 而不是十几个返回值 —— 这里的产出全都被 payload 的一行用到，
    拆成 13 个位置参数只会让调用点变成一列看不懂的逗号。
    """
    # 行级 matched_jobs 是早期按别的画像跑的匹配结果，常含「管培生」「业务推动」
    # 「区域总监助理」这类只共享通用词、或干脆跨职能的岗位名；先按与语义层同一套
    # 确定性规则对齐到当前意向。只收紧岗位名，不删企业（0 岗≠不招）。
    # 注意：变量名不能叫 _dropped —— 那会覆盖上面硬约束/教培剔除的企业清单，
    # 导致 payload.hard_filter.dropped 永远显示 0（被剔除的 12 家教培无处可查）。
    # 判断「大类岗位名能不能算对口」要看公司业务：供应链公司的「管培生」
    # 可以算「供应链管培生」，新材料公司的「管培生」只能问一句。
    biz_text = " ".join(str(x or "") for x in (
        name, c.get("industry"), c.get("segment"), c.get("biz_scope"),
        c.get("job_brief")))
    # 只用真实岗位名做证据：推断岗（job_source_kind=大模型推断）混进 title_all
    # 会被 align 收进 matched_jobs，页面「对口岗位」念出模型猜的岗（电科55所坐实）。
    title_all = [j.get("title", "") for j in _clean_jobs(c.get("jobs"))]
    # 岗位方向表：让真实公告里不带方向的「工艺工程师」「质量工程师」也能判方向
    # （化工厂的工艺岗是化工，汽车零部件厂的是注塑），而不是只判「同一个职能」。
    # 方向由主营推定，与学生意向无关 —— 这里只负责把企业侧的方向准备好。
    dir_hint = dir_hints_for(name, title_all, biz_text, dir_book)
    dir_notes = []
    # ① 原口径：只对齐 LLM 当时挑出的岗位名。丢弃清单用它生成——页面那句
    #    「已按你的意向剔除 N 个岗位名」说的是「模型报的岗位里不对口的」，
    #    不能拿全量岗位去算，否则每家都会报「剔除 25 个」。
    _aligned, dropped_jobs = intent_semantics.align_jobs(
        intended, m["matched_jobs"], intent_sem, biz_text,
        dir_hint=dir_hint, dir_notes=dir_notes)
    # ② 统一候选集（2026-11-15 用户拍板）：命中的证据只看**企业真实岗位名**，
    #    不再受「LLM 当时挑出了哪几个」限制。原来前端按 job_titles 全量判、
    #    后端只看 matched_jobs，出现「江苏东成有同名『质量工程师』仍显示推断」
    #    这类前后端各说各话。岗位名都来自库里的 jobs，不是模型编的。
    aligned_all, _, lvl_blocked, asks = intent_semantics.align_jobs_detail(
        intended, title_all, intent_sem, biz_text,
        dir_hint=dir_hint, dir_notes=dir_notes)
    # 复核否决（0）不算命中：只共享「研发 / 工程」这类词的跨方向岗位不写进证据。
    # 弃权（0.5）也不算命中证据——「不确定」不冒充复核过的对口——但按 hit_verify
    # 的三态设计，它**不是否决**：不删、不记成「不同职能」，转成一条现场待问。
    # 原实现把 0.5 和 0 一起 blocks 掉，QC/QR 这类质量岗同义说法被静默剔除，
    # 企业从 hit 降成 inferred 却只说「不同职能」，与复核结论自相矛盾。
    # 判过的才算；没判过的（模型不可用）保持原样并在前端标「未复核」。
    abstain_pairs = []
    if hit_verdicts:
        _kept, abstain_pairs = [], []
        for pair in aligned_all:
            v = hit_verdicts.get(str(pair[1]) + "||" + pair[0])
            if v == 0.5:
                abstain_pairs.append(pair)
            elif not hit_verify.blocks(v):
                _kept.append(pair)
        aligned_all = _kept
    _abstain_titles = {t for t, _ in abstain_pairs}
    for t, it in abstain_pairs:
        if any(a.get("job") == t for a in asks):
            continue
        asks.append({"job": t, "intent": it, "kind": "abstain",
                     "why": ("「%s」与你的「%s」是相邻说法，复核没敢下结论；"
                             "不算明确命中，现场问一句是不是同一个岗。" % (t, it))})
    # 弃权岗名不是「不同职能」：从剔除清单里摘掉，否则页面一边说「待现场核实」、
    # 一边又把它列进「按你的意向剔除的不同职能岗位」，两套话打架。
    if _abstain_titles:
        dropped_jobs = [t for t in dropped_jobs if t not in _abstain_titles]
    # 同职能、只是写着主管 / 资深 / 经理 → 校招投不进。单独列出来给报告写一句
    # 「有同职能的主管岗，可现场问是否按应届招」，既不假装对口，也不说人家不招。
    lvl_blocked = intent_semantics.level_blocked(intended, title_all)
    # 只写大类名（管培生 / 储备干部 / 研究员 / 专员）的岗位 → 不算对口，
    # 但在提问里给一句「这家有管培生，问一句是否按你的方向培养」。
    ask_blocked = intent_semantics.ask_jobs(intended, title_all, biz_text,
                                            dir_hint=dir_hint)
    m_al = dict(m)
    m_al["matched_jobs"] = [t for t, _ in aligned_all]
    # 企业端有 JD 时，「证据」必须是本家 JD 里真实存在的岗位名；把意向岗位名
    # 直接写进 matched_jobs（「供应链管培生」本身就是学生勾的意向）不是证据。
    jd_titles = {j.get("title", "") for j in _clean_jobs(c.get("jobs"))}
    # 「jd_titles 空就放宽」是给老 0 岗企业留的口子：它们没查到公告，模型给的
    # 岗位权且当推断线索（后面按 state 降成 inferred）。但补过画像推断岗的企业
    # 不同——它们的 job_titles 空是"真实岗确实没有、只有大模型猜的编制"，
    # 若还放宽，matched 里的推断岗就会被当成真实证据把企业升成明确命中。
    # 这类企业一律不认 real，命中的岗位只能进 inferred 的现场问句。
    _has_infer = bool(_inferred_jobs(c.get("jobs")))
    real = [t for t, _ in aligned_all
            if ((not jd_titles) and not _has_infer)
            or any(_same_title(t, x) for x in jd_titles)]
    # 画像×主营复检（2026-11-17 用户定口径）：明确命中不能只看「岗位名 ↔ 意向」
    # 字面，还要看「企业主营业务 + 岗位名」是否对得上「意向岗位 + 专业」。
    # 反面案例：金融公司的「管培生」 × 物流专业学生选的「管培生」—— 字面全同，
    # 但金融公司培养不出物流方向。岗位名自带方向词（仓储物流、供应链管理储备
    # 干部）的直接放行（本家公告比主营更硬）；岗位名不带方向的，主营文本里
    # 必须出现学生方向词，否则降为现场待问并写明「主营与方向对不上」。
    _dom_kw, _dom_fields = biz_recheck.student_domain(
        intended, (profile or {}).get("major") or "")
    real, _biz_blocked = biz_recheck.filter_real(
        real, _dom_kw, _dom_fields, biz_text)
    for _bt, _bi in _biz_blocked:
        if any(a.get("job") == _bt for a in asks):
            continue
        asks.append({"job": _bt, "intent": _bi, "kind": "biz",
                     "why": biz_recheck.blocked_why(
                         _bt, _bi, biz_text, (profile or {}).get("major") or "",
                         (profile or {}).get("intended_jobs") or [])})
    trust0, _ = audit(c, m_al, by_title, by_title)
    guard = hallucination_guard(c, m_al, trust0, by_title)
    # 对外一律用脱敏后的岗位名（问句、命中列表、显著提示都不再念幻觉岗位）
    m_eff = dict(m)
    m_eff["matched_jobs"] = guard["display_jobs"]
    if guard["state_fix"]:
        m_eff["state"] = "inferred"
    # 原本标「明确命中」、但对齐后一个同职能岗位都不剩（证据只有「管培生」这类
    # 通用名、跨职能岗、或干脆是学生自己的意向名）-> 降为推断待核实，
    # 岗位名清空，改问学生自己的意向。
    if m["state"] == "hit" and m["matched_jobs"] and not real:
        m_eff["state"] = "inferred"
        m_eff["matched_jobs"] = []
    # 反向：LLM 当时没挑出、甚至标了 miss，但企业真实岗位名里确实有对口的 → 升为明确命中。
    # 这是统一候选集的另一半：只降不升的话，页面按全量岗位重算说「明确命中」、
    # payload 仍写「推断待核实」，两边永远对不上（江苏东成同名「质量工程师」就是这样）。
    # 前提是证据来自真实岗位名（real 非空），且不是幻觉修正过的行。
    if real and m_eff["state"] != "hit" and not guard["state_fix"]:
        m_eff["state"] = "hit"
        m_eff["matched_jobs"] = _dedup(real)
    # 学生没勾任何意向、上面按 DEFAULT_PROFILE 的默认意向判出的 hit 不算「明确命中」：
    # 默认意向（供应链计划 / 数据分析师 / 商业分析）只是让 demo 有结果的兜底，
    # 不是学生选的岗位。一律降成推断待核实并写明原因；默认意向仍留在 profile
    # 供现场提问，但绝不能伪装成「你的意向岗位命中了」。
    # 现场已确认招（feedback=yes）的企业会在 _final_state_notes 里按事实升回 hit，
    # 那是企业亲口说的，不是拿默认意向推断的，不在这里压。
    _intents_defaulted = bool(profile.get("intents_defaulted")) and m_eff["state"] == "hit"
    if _intents_defaulted:
        m_eff["state"] = "inferred"
        m_eff["matched_jobs"] = []
    # 证据等级修正后重跑一次事实校验，让行业不吻合检测也能生效
    m_audit = dict(m)
    m_audit["state"] = m_eff["state"]
    trust, check_notes = audit(c, m_audit, by_title, by_title)
    if _intents_defaulted:
        check_notes.append("未填写意向岗位，命中按默认方向推断、不算明确命中")
    m_gap = dict(m_eff)
    # 0 岗 / 岗位已按未知处理的企业没有可念的岗位名，改念学生自己勾选的意向岗位：
    # 既不出现「对口岗位」这种空占位，也不再念早期从主营业务推测出来的假岗位。
    gap_jobs = guard["ask_jobs"]
    if gap_jobs == ["对口岗位"]:
        gap_jobs = list(profile.get("intended_jobs") or [])[:2] or ["对口岗位"]
    m_gap["matched_jobs"] = gap_jobs
    guard["ask_jobs"] = gap_jobs   # payload 里也不再留「对口岗位」空占位
    return {
        "dir_notes": dir_notes, "dropped_jobs": dropped_jobs, "aligned_all": aligned_all,
        "lvl_blocked": lvl_blocked, "ask_blocked": ask_blocked, "real": real, "asks": asks,
        "guard": guard, "m_eff": m_eff, "trust": trust, "check_notes": check_notes,
        "gaps": dynamic_gaps(c, m_gap, bm.get(name)), "sc": score_parts(c, W, profile),
    }


def build():
    # 缓存体检先跑：代码一改，相关缓存自动判失效（读取方会重建），
    # 这里把结果打进 payload，页面「更多设置」里能直接看到本版用的是哪一代结果。
    # 缓存体检：代码一改，相关缓存自动判失效。指纹对不上的旧文件先挪进
    # data/_stale_cache/（可回溯），不在原地冒充本版结果；重建完再体检一次，
    # 结果打进 payload，页面「更多设置」里能直接看到本版用的是哪一代结果。
    _moved = cache_guard.purge_invalid()
    if _moved:
        print("已归档旧缓存：" + "、".join("%s→%s" % (n, os.path.basename(p))
                                        for n, p in _moved))
    companies, llm, raw_profile = read_db()
    _dir_book = load_dir_hint()
    v42 = load_v42()
    enrich = load_enrich()
    _src_count = _merge_sources(companies, enrich, v42)
    profile = profile_from(raw_profile)

    companies, _dropped, _conds = _apply_hard_constraints(companies, raw_profile)
    companies, _miss_names = _drop_miss(companies, llm)
    companies, _campus_names = _drop_campus_unfit(companies, llm)

    # 用大模型判断「学生勾选的意向岗位」与「企业真实岗位名」是否语义相符。
    # 结果放进 payload 顶层，前端据此显示语义命中；模型不可用时返回 {}，前端退回字面匹配。
    _all_titles = sorted({j.get("title", "").strip()
                          for c in companies.values()
                          for j in _clean_jobs(c.get("jobs"))
                          if j.get("title")})
    # 只用当前画像里学生真正勾选的意向岗位；确实没有画像时才回退默认示例。
    # 不把「管科 + 供应链计划/数据分析师/商业分析」硬塞进每个用户的语义映射。
    _intended = list(dict.fromkeys(
        list(profile.get("intended_jobs") or []) or list(DEFAULT_PROFILE["intended_jobs"])))
    # 语义映射顺带把内置测试画像的意向也跑一遍：学生切画像 / 打开 test_users 页时
    # 直接用 payload 里的结果，不必现场等模型，也就不会因为连不上服务而退回本地规则。
    # 注意：只扩 build_map 的入参，_intended 本身（对齐 / 层级 / 提问 / unknown_infer）
    # 必须保持「当前画像」，否则会把别的专业的岗位问到这个学生头上。
    _intended_sem = list(dict.fromkeys(_intended + _extra_intents()))
    intent_sem = intent_semantics.build_map(_intended_sem, _all_titles)
    # 复核：本地规则（共享 2 字核心词 / 同职能族）会把「食品研发工程师」判成
    # 「药品研发工程师」的命中——只共享「研发」。这里把候选对成批送模型判一次，
    # 结果写进 payload 的 hit_verdicts，前端判命中时按它否决/确认。
    # 只判候选对（当前 126 对，实测一次调用约 2.4s），且 pair 级缓存；
    # 模型不可用时返回空表，前端按「未复核」如实标注，不编结论。
    hit_verdicts, hit_verify_meta = hit_verify.build_verdicts(_intended_sem, _all_titles)
    # v18：未知1（有粗略业务描述）/ 未知2（联网检索仍无岗）两类「无具体岗」
    # 企业，匹配端一视同仁，交给大模型按学生当前勾选的意向岗位判断可能招什么。
    # 结果按 (意向岗位 + 企业清单指纹) 缓存，模型不可用时退回中性话术。
    unknown_map = unknown_infer.build_map(list(companies.values()), _intended)
    W = weights_from(raw_profile)
    bm = booths()
    fb = read_feedback()

    c_list = list(companies.values())
    by_title = _index_titles(companies)
    fam = V2.build_family_map(c_list)
    payload, _ = V2.build_payload(c_list, bm, fam)
    by_name = {r["name"]: r for r in payload}

    _fit_map = _prefetch_fit(companies, llm, enrich, v42, profile)

    # 学生方向（画像×主营复检用）：方向词 + 领域集合，循环里只读不重算
    _dom_kw, _dom_fields = biz_recheck.student_domain(
        profile.get("intended_jobs") or [], profile.get("major") or "")

    rows = []
    # 校招公告来源、但公告里没有与意向对口岗位的企业：不进榜单，也不推断。
    # 依据「公告视为齐全 → 有就是有，没有就是没有」，与 websearch / 社招（清单不齐全，
    # 保留并提示现场确认）区别对待。清单单独存档，可回溯，不是静默丢弃。
    _campus_nofit = []
    for name, c in companies.items():
        # 成长档位 vs 证据自洽性检查：档案里 4 家写 rising_star 却 growth_hits 为空，
        # 其中 3 家备注直接写「未检索到」。不改档（改档要人拍板），先在报告里标出来。
        _erec = enrich.get(name) or {}
        _growth_flag = ""
        if str(c.get("growth_tier") or "") == "rising_star" and not (_erec.get("growth_hits") or []):
            _growth_flag = "档案标 rising_star，但 growth_hits 为空、备注为「%s」——证据不足，待核" % (
                (_erec.get("growth_note") or "空")[:40])
        m = llm.get(name) or {"state": "miss", "priority": 1, "matched_jobs": [],
                              "reason": "", "gaps": [], "verify_question": "", "has_jd": bool(c.get("jobs"))}
        # 安全兜底：无具体岗企业（未知1/未知2）即使缺 llm_match 行，也按 inferred 处理，
        # 交给 unknown_infer 动态判定，绝不直接 miss 出榜（match_fill 已跳过这类企业）。
        if (str(c.get("jobSource") or "") in ("未知1", "未知2")) and not c.get("jobs") and m["state"] == "miss":
            m = dict(m)
            m["state"] = "inferred"
        rec = by_name.get(name, {})
        # 行级 matched_jobs 是早期按别的画像跑的匹配结果，常含「管培生」「业务推动」
        # 「区域总监助理」这类只共享通用词、或干脆跨职能的岗位名；先按与语义层同一套
        # 确定性规则对齐到当前意向。只收紧岗位名，不删企业（0 岗≠不招）。
        # 注意：变量名不能叫 _dropped —— 那会覆盖上面硬约束/教培剔除的企业清单，
        # 导致 payload.hard_filter.dropped 永远显示 0（被剔除的 12 家教培无处可查）。
        # 判断「大类岗位名能不能算对口」要看公司业务：供应链公司的「管培生」
        # 可以算「供应链管培生」，新材料公司的「管培生」只能问一句。
        ev = _evidence(name, c, m, intent_sem, hit_verdicts, by_title, _dir_book,
                       _intended, profile, W, bm)
        m_eff, guard = ev["m_eff"], ev["guard"]
        _dropped_jobs, _aligned_all = ev["dropped_jobs"], ev["aligned_all"]
        _lvl_blocked, _ask_blocked = ev["lvl_blocked"], ev["ask_blocked"]
        _real, _dir_notes, _asks = ev["real"], ev["dir_notes"], ev["asks"]
        trust, check_notes = ev["trust"], ev["check_notes"]
        gaps, sc = ev["gaps"], ev["sc"]
        booth = bm.get(name, {})

        # v18 推断话术分三种：
        #  - 库里有岗位相关记录（websearch / 社招 / 平台转载）：如实说有 N 条
        #    记录、未查到对口证据、来源不保证齐全 → 按意向岗位建议现场确认。
        #  - 未知1 / 未知2（两类「无具体岗」）：匹配端一视同仁，由大模型按
        #    学生当前勾选的意向岗位挑出可能招的 X；判空就如实说关联较弱，
        #    绝不把假岗位或别的画像结论当成事实。
        #  - 其他 0 岗：中性兜底。
        # 校招公告来源且无对口岗位的企业已在过滤阶段直接排除。
        jobs = _clean_jobs(c.get("jobs"))
        ask_jobs = "、".join(guard["ask_jobs"][:2]) or "对口岗位"
        # 「是否招 X」的 X 只能是学生自己勾选的意向岗位，不是企业库里已有的岗位名
        intended_ask = "、".join(list(profile.get("intended_jobs") or [])[:2]) or ask_jobs
        _src_kind, _src_label = source_class(c, jobs)
        _js = str(c.get("jobSource") or "")
        # 0 岗且来源不保证齐全的，全部交给 unknown_infer 判过；这三种来源都算
        # 「没有岗位公告」，不能只认 未知1/未知2（「未知」这一批曾因此掉进模板兜底）。
        _unknown = _js in unknown_infer.NO_JOB_SOURCES and not jobs
        _u = unknown_map.get(name) or {}
        _likely = [t for t in (_u.get("likely") or [])
                   if t in (profile.get("intended_jobs") or [])][:2]
        _askX = "、".join(_likely) if _likely else intended_ask
        # —— 业务判定：这家单位的主营业务能不能容纳学生的方向 ——
        # 旧版这一段是纯模板：把「学生自己勾的意向」写成「模型推断可能招 X」，
        # 一个字都没读主营，于是房建总包被写成「可能招产品工程师」，学校、
        # 财务公司、商业综合体也被写成「可能招机械设计工程师」。现在改由
        # biz_fit 逐家判 yes/ask/no，依据必须落到主营产品与生产环节上。
        fit_info = {"fit": "ask", "jobs": [], "evidence": [], "basis": "",
                    "note": "", "via": "fallback"}
        if m_eff["state"] == "inferred":
            # 优先用上面的并发预取结果；粗筛没覆盖到的（原 hit 被 guard 降级成
            # inferred 等）才单条补判，补齐后照样进缓存。
            fit_info = _fit_map.get(name) or fit_one(
                name=name,
                industry=c.get("industry", ""),
                nature=c.get("nature", ""),
                # 主营有两个来源：v42 的 segment（细分业务，118 家基本都有）
                # 和联网补采的 biz_scope（只有一部分有）。旧版只喂后者，
                # 于是 segment 明明写着「奥数竞赛辅导」「商业综合体招商运营」的企业，
                # 模型却判成「主营未明，方向不确定」。
                segment=(v42_view(enrich.get(name) or v42.get(name)) or {}).get("segment", ""),
                scope=c.get("biz_scope", ""),
                job_src=_js,
                titles=[j.get("title", "") for j in jobs],
                intended=list(profile.get("intended_jobs") or []))
        # 模型没给出结论（无 key / 超时 / 格式异常）→ 中性档，不替用户判「跳过」
        _fit = fit_info["fit"] if fit_info["via"] == "llm" else "ask"
        _fj = "、".join(fit_info["jobs"]) or _askX
        _basis = (fit_info["basis"] or "").strip().rstrip("。")
        # 「这家行业里对应的是哪类岗」——判 yes/ask 的依据都摊开给学生看。
        # 措辞必须写「模型据此认为」而不是「这类单位的岗位」：这些岗位名是模型写的，
        # 有的真实存在（工艺工程师），有的是它把业务词和岗位词拼出来的（家纺工艺工程师），
        # 后者企业公告里根本没有。写成事实句，等于把模型的字当成企业的话。
        # 拼接名已在 biz_fit._plain_evidence 退回意向原词，这里再把「等于学生自己
        # 意向」的那些滤掉——「模型据此认为对口的是：设备工程师」这种句子等于没说，
        # 剩下的就是真·行业侧叫法（机械设计工程师、仪表工程师），才值得占一句话。
        _intended_now = list(profile.get("intended_jobs") or [])
        # 方案一的 basis 里已经写了岗位名（「业务链条上设自动化工程师、控制工程师这类岗」），
        # 再跟一句「（模型据此认为对口的是：同两个岗名）」就是同义反复。所以
        # 「等于意向原词」和「basis 里已出现」的都不再重复展示。
        _ev = "、".join(e for e in (fit_info.get("evidence") or [])
                        if e and e not in _intended_now and e not in _basis)
        _evsuf = ("（模型据此认为对口的是：%s）" % _ev) if _ev else ""
        infer_text = _inferred_text(m_eff["state"], _fit, fit_info, _basis, _fj,
                                    _evsuf, jobs, _js, _src_label, intended_ask)

        # 推断命中对（仅 inferred）：业务链推断岗（fit jobs）+ 画像推断岗
        # × 学生勾选的意向。口径刻意收紧为「同名或互含」：推断命中本来就是
        # 提示学生去现场核实的参考，宁可少标不错标——pair_verdict 的职能族
        # 兜底会把「配方工程师 × 工艺工程师」「高分子研发 × 配方」也放进来，
        # 对推断岗这种非公告原文的证据太松。互含（工艺工程师 ⊂ 化工工艺工程师）
        # 是最硬的字面证据，两端 JS 镜像也只做这一条，parity 天然一致。
        # 每个意向最多一对，业务链岗优先于画像推断岗。
        _infer_pairs = []
        if m_eff["state"] == "inferred":
            _ipool = list(dict.fromkeys(
                [str(t or "").strip() for t in (fit_info.get("jobs") or []) if str(t or "").strip()]
                + [str(j.get("title") or "").strip()
                   for j in _inferred_jobs(c.get("jobs")) if j.get("title")]))
            _seen_it = set()
            for t in _ipool:
                for it in _intended_now:
                    if it in _seen_it or not t:
                        continue
                    if t == it or t in it or it in t:
                        _infer_pairs.append((t, it))
                        _seen_it.add(it)
                        break
            # 画像×主营复检（2026-11-17）：推断命中对同样要主营对得上方向。
            # 推断岗名是模型按画像猜的，不能自证方向 —— 主营文本（行业/细分/
            # 主营范围）里必须出现学生方向词；主营为空/纯「未知」一律不标，
            # 没有业务依据就不该推断（教育出版推断「物流专员」这类就这么拦下）。
            _infer_pairs = biz_recheck.filter_infer(
                _infer_pairs, _dom_fields, biz_recheck.biz_text_of(name, c))

        # 现场问句也念学生自己的意向岗位。llm_match 里的 verify_question 是
        # 早期按别的画像生成的（如「是不是招供应链管理专员」），与当前意向
        # 岗位不符，再念一次就变成「模型编的岗位 ≠ 你的岗位、却又说可能招
        # 你的岗位」的循环论证 —— 推断企业一律改用下面这句。
        # 大模型判「都不合适」时不硬塞意向岗位，改成中性的现场问句
        if _unknown and not _likely:
            ask_question = "请问今年招哪些岗位、是否接受我这个方向？"
        else:
            ask_question = "请问今年是否招%s？" % _askX

        # 岗位名只在现场问句里念一次：infer_text 正文曾自带「建议现场确认是否招X」，
        # _questions 又拼「（现场问一句：请问今年是否招Y）」，X 取自模型给的 fit.jobs、
        # Y 取自学生勾的意向，两者常不是一组（华海：正文「原料药质量研究员」、
        # 问句「原料药质量研究员、医学专员」）。显著提示里抹掉正文那句，岗位统一由
        # ask_question 给出，学生不会看到同一件事问了两遍、还问出两个不同的岗位。
        import re as _re
        infer_text = _re.sub(r"建议现场确认是否招[^。]+。$", "", infer_text)
        infer_text = _re.sub(r"建议现场问一句是否招[^。]+。$", "", infer_text)
        # 现场提问：权重 × (1 - 证据可信度)，取前 2；推断岗位固定排第一
        qs = _questions(gaps, W, m_eff["state"], _fit, infer_text, ask_question)

        jobs = _clean_jobs(c.get("jobs"))
        hits, hits_all, misses, misses_all, _title_total = _hits_and_misses(
            m_eff["matched_jobs"], jobs)

        # ---- v7 现场核实回流：反向修正企业端证据等级 ----
        fbrow = fb.get(name)
        (state_final, trust_final, notes_final,
         _confirmed, _dir_hit) = _final_state_notes(
            m, m_eff, trust, check_notes, fbrow, _dropped_jobs, _dir_notes, _real, _asks,
         _lvl_blocked)

        if _confirmed:
            qs = [q for q in qs if not q.get("infer")]

        # —— 校招公告「没有就是没有」——
        # 校招公告的岗位清单视为齐全：公告里没有对口岗位，就是这家今年（公告范围内）
        # 不招这个方向，不该再以「推断可能招」的口吻出现在报告里。
        #
        # 这条必须放在**语义降级之后**判断。llm_match 里有 11 家早期按更宽口径判成
        # hit（普洛药业、比特大陆、古茗、圣奥…），公告里其实一个对口岗都没有；上面
        # 826 行那个排除只认「原始 state == inferred」，对它们无效，于是它们被语义层
        # 降级成 inferred 后，反而带着「建议现场确认是否招数据分析师」出场 ——
        # 与「有就是有，没有就是没有」正好相反。
        # 现场已确认招（fbrow=yes）的企业不在此列：那是事实，不是推断。
        if (state_final == "inferred" and jobs
                and not (fbrow and fbrow["result"] == "yes")
                and source_class(c, jobs)[0] == "campus"):
            _campus_nofit.append({
                "name": name,
                "why": ("校招公告里 %d 个岗位，没有与意向对口的；公告视为齐全，不再推断"
                        % _title_total),
                "sample": [j.get("title", "") for j in jobs][:6],
            })
            continue

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
            # 推断态不展示任何具体岗位名：那些名字是按行业猜的，不是企业证据
            # （旧数据 llm_match 里就有「state=inferred 却带岗位名」的自相矛盾行）。
            # 原值保留在 original_matched_jobs 供核对。
            "matched_jobs": (_dedup(m_eff["matched_jobs"]) if state_final == "hit" else []),
            "original_matched_jobs": m["matched_jobs"],
            # 对齐时被剔除的岗位名（只影响展示与「明确命中」判定，企业不删除）
            "dropped_jobs": _dropped_jobs,
            # llm_match 里的 reason 是早期按别的画像生成的（常写「学生专业为
            # 管理科学与工程」），且会把「管培生」「业务推动」说成「干的活相近」；
            # 明确命中的理由改为按**对齐后仍存活**的岗位重写，推断企业换成
            # 与画像无关、只讲证据状态的中性理由。
            "reason": ((_u.get("reason") or "按行业与主营业务判断，未查到具体岗位公告，建议现场确认。")
                       if _unknown else
                       (("按「%s」行业推断，尚未找到与意向岗位明确对口的证据，建议现场确认。"
                         % c.get("industry", "未知")) if state_final == "inferred"
                        else (intent_semantics.align_reason(
                            [p for p in _aligned_all if p[0] in _real], intent_sem) or m["reason"]))),
            "verify_question": ask_question if state_final == "inferred" else m["verify_question"],
            # hits/misses 是截断后的展示清单；*_total 是去重后的真实条数，
            # 页面按它标「等 N 个 / 示例 x/y」，避免学生以为企业只招这几类。
            "hits": hits, "misses": misses, "job_count": len(jobs),
            # 后端按主营业务方向判定的命中岗位（岗位名不带方向）。前端「岗位语义
            # 命中」标签采信它，否则两栏打架：命中岗位里有、语义命中里没有。
            "dir_hit_jobs": _dedup([d["job"] for d in _dir_hit]),
            # 后端裁决的语义命中（统一候选集对齐 + 泛意向方向对齐 + 复核过滤之后、
            # 只留真实岗位名证据）。前端当前画像未改动时直接采信这份，本地重算只
            # 兜底「学生现场改勾选」场景——消除前后端各算一套的打架（2026-11-16）。
            "intent_matches_backend": ([{"intent": it, "job": t, "via": "backend",
                                         "reason": "后端语义对齐：与「%s」同类（真实岗位名证据）" % it}
                                        for t, it in _aligned_all if t in set(_real)]
                                       if state_final == "hit" else []),
            # 推断命中对（2026-11-17 用户要求）：与真实命中同一箭头格式，但显式
            # 标「推断」。前端未改画像时直接采信，换画像走本地同源规则。
            "infer_matches_backend": ([{"intent": it, "job": t, "via": "infer",
                                        "reason": "大模型按主营业务推断可能招（非招聘公告原文），现场核实"}
                                       for t, it in _infer_pairs]
                                      if state_final == "inferred" else []),
            "hit_total": len(hits_all), "miss_total": len(misses_all),
            "job_title_total": _title_total,
            # v18：无具体岗企业的粗略描述、标签、大模型按意向岗位挑出的可能岗位
            "job_brief": c.get("job_brief", ""),
            "source_kind": _js,
            # 非校招企业联网补采的主营范围 / 主要产品（只展示，不进打分）。
            # 报告里以「大模型判断依据：……」一句呈现，解释模型凭什么判断它可能招什么。
            "biz_scope": c.get("biz_scope", ""),
            "llm_basis": (("大模型判断依据：" + str(c["biz_scope"]).strip())
                          if (str(c.get("biz_scope") or "").strip() and _js != "校招公告")
                          else ""),
            "infer_likely": (fit_info["jobs"] or _likely),
            "infer_via": (("job-profile" if fit_info.get("mode") == "profile" else "biz-fit")
                          if (fit_info["via"] == "llm" and m_eff["state"] == "inferred")
                          else (_u.get("via", "") if _unknown else "")),
            # 业务交叉判定：yes / ask / no（no 的页面收进「与你的方向无关」折叠组，
            # 数据不删、展位号不变、导逛路线不变；0 岗 ≠ 不招）。
            "fit": (_fit if m_eff["state"] == "inferred" else ""),
            "biz_fit": ({"fit": _fit, "basis": _basis,
                         "jobs": fit_info["jobs"],
                         # 行业侧岗位名：yes 的岗位级证据（第三栏）。空 = 模型答不出
                         # 这个行业里谁做这类岗，已由 biz_fit._settle 降成 ask，
                         # 只存下来供核对，页面不直接展示。
                         "evidence": fit_info.get("evidence") or [],
                         "note": fit_info.get("note", ""),
                         "via": fit_info["via"]}
                        if m_eff["state"] == "inferred" else {}),
            # 企业真实岗位名（前端做语义命中用；不只是模型预生成的 matched_jobs）
            "job_titles": [j.get("title", "") for j in jobs if j.get("title")],
            # 画像补进来的岗位：大模型按主营推断的常规编制，不是招聘公告原文。
            # 单独给页面标注来源，绝不进 job_titles / 命中判定（见 _clean_jobs）。
            "inferred_jobs": [str(j.get("title") or "") for j in _inferred_jobs(c.get("jobs"))
                              if j.get("title")][:20],
            "inferred_job_notice": (("另有 %d 个岗位为大模型按主营业务推断（非招聘公告原文），"
                                     "仅作现场提问参考" % len(_inferred_jobs(c.get("jobs"))))
                                    if _inferred_jobs(c.get("jobs")) else ""),
            # 岗位层级分布 + 只差层级被挡下的岗位（主管 / 资深 / 经理）。
            # 只展示、只影响「算不算对口」，不进 calcOne 打分。
            "level_counts": dict(Counter(
                job_level.LEVEL_NAMES.get(job_level.level_of(j.get("title", "")), "未识别")
                for j in jobs if j.get("title"))),
            "level_blocked": _lvl_blocked,
            # 大类岗位名（管培生 / 储备干部 / 研究员）：不是对口证据，
            # 但值得现场问一句「是不是我这个方向」。
            "ask_jobs": _ask_blocked,
            "gaps": gaps, "questions": qs[:2],
            "score": sc["score"], "effective_weight": sc["effective_weight"],
            "parts": sc["parts"],
            "evidence": {"source": c.get("source", ""), "date": c.get("date", ""),
                         "jobSource": c.get("jobSource", "未知"),
                         "jobEvidence": str(c.get("jobEvidence", "未知"))[:80]},
            "infer_notice": infer_text if state_final == "inferred" else "",
            # 展示层：行业地位(多榜) / 成长 / 细分领域 / 4 列岗位来源。
            # 数据源优先 145 家增补档案（同名 145/145），缺失回退 v4.2。
            # rank_hits / segment / evidence 只展示，不进 calcOne。
            "v42": v42_view(enrich.get(name) or v42.get(name)),
            "data_src": c.get("data_src") or "",
            # 非企业主体（学校/党政/事业单位）：榜单两维不存在，打分层已跳过
            "non_enterprise": int(c.get("non_enterprise") or 0),
            # 增补档案覆盖了哪些旧值（[旧, 新]），页面可核对，不自作主张
            "enrich_diff": c.get("_enrich_diff") or {},
            # 成长档位与证据不一致时的告警（不静默改档，先标出来给人看）
            "growth_flag": _growth_flag,
        })

    # 名次只由排序分决定。
    # 可信度 / 待核实 / 岗位状态一律不进名次比较——它们是展示标注，不是排序依据。
    # 0 岗与 inferred 也不顺延：分数高就说明硬件条件更适配，值得先去问一句。
    # 唯一例外：「现场确认不招」属于事实出局，仍顺延到队尾。
    # 同分并列时要给确定性次序，否则顺序取决于数据库返回顺序（页面上同分企业
    # 谁在前谁在后会莫名其妙地变）。规则：命中 > 推断，再按展位号，最后按名称。
    # 顺延到队尾的两类：现场确认不招（事实出局）、fit=no（主营方向与意向不交叉，
    # 页面已收进「与你的方向无关」折叠组）。fit=no 此前只折叠展示、名次仍靠前，
    # 主榜第 6 位却出现在页面底部折叠组里，导逛顺序和卡片名次对不上。
    # 注意：fit=no 只顺延，不删数据、不动展位号、不进 excluded —— 0 岗≠不招仍是红线。
    rows.sort(key=lambda r: (
        1 if (r.get("feedback") or {}).get("result") == "no" else 0,
        1 if (r.get("fit") == "no" and (r.get("biz_fit") or {}).get("via") == "llm"
             and r["state"] == "inferred") else 0,
        -r["score"],
        0 if r["state"] == "hit" else 1,
        str(r.get("booth") or "999"),
        r["name"]))
    by_booth = sorted([r for r in rows if r["booth"]], key=lambda r: str(r["booth"]))
    booth_pos = {r["name"]: i for i, r in enumerate(by_booth)}
    for i, r in enumerate(rows):
        r["next"] = rows[i + 1]["name"] if i + 1 < len(rows) else ""
        r["rank"] = i + 1
        r["rank_reason"] = ("第 %d 位：排序分 %s / %s"
                            % (i + 1, r["score"],
                               {"hit": "有真实 JD 命中", "inferred": "按行业推断待核实",
                                "miss": "无对口岗位"}[r["state"]]))
        if (r.get("feedback") or {}).get("result") == "yes":
            r["rank_reason"] += "；现场已确认招，升级为明确命中"
        if (r.get("feedback") or {}).get("result") == "no":
            r["rank_reason"] += "；现场确认不招，已顺延（保留在队尾供核对）"
        if (r.get("fit") == "no" and (r.get("biz_fit") or {}).get("via") == "llm"
                and r["state"] == "inferred"):
            r["rank_reason"] += "；主营方向与你的意向不交叉，已顺延到队尾"
        p = booth_pos.get(r["name"])
        r["nearby"] = ([by_booth[p - 1]["name"]] if p and p > 0 else []) + \
                      ([by_booth[p + 1]["name"]] if p is not None and p + 1 < len(by_booth) else [])

    _cstat = cache_guard.status(verbose=True)
    # 成长性可用家数（只统计本页，用于解释权重为何被关闭）
    _growth_avail_n = sum(1 for r in rows
                          if ((r.get("v42") or {}).get("growth_tier") in R.GROWTH_TIERS))
    # 产物这一项在 build 里必然显示"待重建"（文件还是上一版）。本次写完就是最新，
    # 这里直接标成本次生成，避免页面把正常状态当成告警。
    _cstat["fair_payload"] = {"ok": True, "kind": "artifact", "desc": "页面数据产物",
                             "why": "本次 build 生成（%s）" % time.strftime("%Y-%m-%d %H:%M:%S")}
    out = {
        "version": "v7-tri-2026-11-10", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile, "weights": W,
        # 被关闭权重的维度：写明原因与实测覆盖，页面据此解释「为什么这维是 0 分权重」。
        "weights_disabled": ({} if R.GROWTH_ENABLED else {
            "growth": ("本页 %d 家里 %d 家有成长信号；成长性的权重已按比例回填其他维度，"
                       "这一维仍作展示，不再记账。%s"
                       % (len(rows), _growth_avail_n,
                          "（145 家增补档案已到位，若数据足够应把开关改回 True）"))
        }),
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
        # 校招公告来源、公告里无对口岗位而被排除的企业（不进榜单、不推断）。
        # 与 hard_filter 分开：那些是学生自己设的条件，这些是「人家公告里没有」。
        "campus_no_fit": {
            "count": len(_campus_nofit),
            "note": ("来源是完整校招公告、公告里没有与意向对口岗位的企业："
                     "按「公告没有就是没有」不进榜单，也不做「可能招」的推断。"
                     "清单保留在此供核对。"),
            "items": _campus_nofit,
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
            "companies": len(rows),
            # 与卡片上的「N 条岗位记录」同口径：批次名 / 专业类目 / 招考名目不算岗位
            "jobs": sum(len(_clean_jobs(c.get("jobs"))) for c in companies.values()),
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
            # 两维覆盖：145 家增补档案到位后，这两维不再是「集体扣分」。
            # 非企业主体（学校/党政/事业单位）不参与这两维，单独计数，别混进分母。
            "rank_cov": sum(1 for r in rows
                            if not r.get("non_enterprise")
                            and (r.get("v42") or {}).get("rank_tier") in R.INDUSTRY_STATUS_TIERS),
            "growth_cov": sum(1 for r in rows
                              if not r.get("non_enterprise")
                              and (r.get("v42") or {}).get("growth_tier") in R.GROWTH_TIERS),
            "growth_unknown": sum(1 for r in rows if not r.get("non_enterprise")
                                  and (r.get("v42") or {}).get("growth_tier") == "unknown"),
            "non_enterprise": sum(1 for r in rows if r.get("non_enterprise")),
            "data_src": dict(Counter(r.get("data_src") or "无" for r in rows)),
            "enrich_diff_companies": sum(1 for r in rows if r.get("enrich_diff")),
        },
        "intent_semantics": intent_sem,
        # 命中复核表：{"意向||企业岗位名": 0|1}。0=大模型复核否决（不算命中），
        # 1=确认同类。键不存在 = 未复核（模型不可用 / 该对不在候选集），
        # 前端遇到未复核的对会如实标「本地规则，未复核」，不冒充复核结论。
        "hit_verdicts": hit_verdicts,
        "hit_verify_meta": hit_verify_meta,
        # 岗位层级口径（应届校招）：只用于判断「算不算对口」，不进打分。
        "level_rules": {
            "levels": {str(k): v for k, v in job_level.LEVEL_NAMES.items() if k},
            "campus_max": job_level.CAMPUS_MAX,
            "note": ("同一职能里主管 / 资深 / 经理和管培生 / 专员是两档人。校招应届只把"
                     "实习、校招初入（管培生/培训生/专员/助理）、常规经验岗算作可投；"
                     "主管以上的岗位不会算成「对口」，企业照常留在榜单里，"
                     "报告里会注明「有同职能主管岗，可现场问是否按应届招」。"),
        },
        # 缓存体检：每条结果是否是「本版代码」算出来的。失效的不静默使用，
        # 页面明示，避免再出现「规则改了但页面还是旧结论」的误判。
        "cache_status": {k: {"ok": bool(v["ok"]), "why": v["why"], "desc": v["desc"]}
                         for k, v in _cstat.items()},
        # 被剔出榜单的企业（供报告页明示，删了不能不说）
        "excluded_miss": {"count": len(_miss_names), "names": _miss_names},
        # 校招公告里明确没有对口岗位的企业（来源齐全，无需现场碰运气）
        "excluded_campus": {"count": len(_campus_names), "names": _campus_names},
        # 保留下来的推断企业里，岗位来源是否齐全的分类统计
        "infer_source": {
            "with_jobs_partial": sum(1 for r in rows if r["state"] == "inferred" and r["job_count"]),
            "zero_jobs": sum(1 for r in rows if r["state"] == "inferred" and not r["job_count"]),
        },
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
