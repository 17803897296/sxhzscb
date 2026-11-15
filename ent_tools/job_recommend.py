# -*- coding: utf-8 -*-
"""
采集端 · 岗位推荐：专业 + 实习 / 竞赛 / 论文 -> 校招岗位名，支持逐层放宽。

v14（2026-11-14）：
  1. 三类经历**分开**传给模型（实习 / 竞赛 / 论文），不再混成一个 experiences，
     避免模型只看实习、把竞赛和论文当噪声。
  2. 「换一批」按层级逐步放宽，每层维护一个固定候选池，本层展示完才升级：
       tier 0  专业 + 实习 + 竞赛 + 论文（最贴切）
       tier 1  放宽到本专业（忽略经历细节）
       tier 2  放宽到专业大类（同学科门类的通用岗位）
       tier 3  放宽到无专业要求岗位（教培 / 销售 / 运营等），
               **仅当学生在问卷里选了「愿意考虑教培」**才启用
     每次请求带 shown（历次已展示过的岗位名）；服务端从当前层的池子里
     取没展示过的 6 个，池子空了自动升层并在响应里标明。
  3. 库里真实在招的岗位仍优先，标 from_db。

边界（不变）：输出只是给学生勾选的建议，不写进企业事实字段，
不生成「某企业招什么」的断言；模型失败一律规则兜底，演示可离线。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import cache_guard

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
POOL_FILE = os.path.join(DATA, "job_recommend_pool.json")
# 改 LLM_PROMPT 的内容或口径时手动 +1：池子缓存随之失效，避免继续吃旧提示词的结果。
PROMPT_VER = "rec-v3"
_LOCK = threading.Lock()

# 规则兜底用的「泛化校招岗位族」：刻意不写年份专属的公告岗位名。
MAJOR_BASE = {
    "管理科学与工程": ["管培生", "运营管理岗", "供应链/计划岗", "数据分析岗", "项目管理岗"],
    "运营与供应链管理": ["供应链管培生", "计划专员", "采购专员", "物流运营岗", "供应链数据分析岗"],
    "大数据分析与决策": ["数据分析岗", "BI/报表岗", "数据运营岗", "数据开发岗", "策略分析岗"],
    "金融服务与金融工程": ["管培生（金融方向）", "风险管理岗", "行业研究岗", "量化研究岗", "客户经理"],
    "复杂工程与智能管理": ["项目管理岗", "工程咨询岗", "工业工程师", "质量工程师", "造价/合约岗"],
    "智能系统管理": ["产品经理", "数据分析岗", "系统运营岗", "ERP/实施顾问", "信息管理岗"],
    "工业工程": ["工业工程师", "生产计划岗", "质量工程师", "精益改善岗", "制造管培生"],
    "计算机": ["后端开发工程师", "前端开发工程师", "算法工程师", "测试开发工程师", "数据开发工程师"],
    "软件工程": ["软件开发工程师", "后端开发工程师", "前端开发工程师", "测试开发工程师", "运维开发工程师"],
    "金融": ["管培生（金融方向）", "客户经理", "风险管理岗", "行业研究岗", "金融数据分析岗"],
    # 理科 / 化工类（2026-11-15 补：此前只有经管+IT，理科专业全兜底成管理通用岗）
    "化学": ["研发工程师", "合成研究员", "化验/质检岗", "分析测试岗", "工艺工程师", "实验室技术员", "EHS安全工程师", "质量工程师"],
    "化学工程与工艺": ["工艺工程师", "生产技术员", "化验/质检岗", "EHS安全工程师", "设备工程师", "质量工程师"],
    "材料": ["材料研发工程师", "失效分析工程师", "工艺工程师", "质量工程师", "实验室技术员", "化验/质检岗"],
    "生物": ["生物研发工程师", "发酵/细胞培养技术员", "实验员", "质检QA", "注册专员", "生产技术员"],
    "药学": ["药物分析研究员", "药品注册专员", "QA/QC岗", "生产技术员", "医学信息沟通专员"],
    "数学": ["数据分析岗", "统计分析师", "算法工程师", "精算助理", "量化研究岗", "数据开发工程师"],
    "环境工程": ["EHS工程师", "环保工程师", "水处理工程师", "环境检测员", "化验/质检岗"],
}

SIGNAL_RULES = [
    (["供应链", "库存", "补货", "采购", "物流", "仓储", "周转", "供应商", "共享治理"],
     ["供应链/计划岗", "采购专员", "物流运营岗", "供应链数据分析岗"],
     "经历里做过供应链 / 采购 / 库存相关的事"),
    (["数学建模", "建模", "预测", "XGBoost", "Python", "SQL", "数据", "分析", "报表", "指标", "取数"],
     ["数据分析岗", "BI/报表岗", "策略分析岗", "算法工程师"],
     "竞赛或论文里用到建模、预测、数据分析"),
    (["论文", "研究", "课题", "学术", "治理策略", "实证"],
     ["行业研究岗", "策略分析岗", "咨询分析师"],
     "有论文 / 科研训练，适合研究分析类岗位"),
    (["产品", "需求文档", "原型", "用户", "PRD", "竞品"],
     ["产品经理", "产品运营岗", "商业分析岗"],
     "经历里接触过产品或需求工作"),
    (["项目", "协调", "排期", "交付", "流程", "推进"],
     ["项目管理岗", "流程/精益改善岗"],
     "经历里有项目协调或流程推进"),
    (["销售", "客户", "商务", "BD", "渠道", "市场"],
     ["客户经理", "市场/商务岗"],
     "经历里接触过客户或市场"),
    (["财务", "会计", "审计", "预算", "成本"],
     ["财务分析岗", "成本分析岗", "风控/内审岗"],
     "经历里接触过财务或成本"),
]

TIER_LABELS = [
    "最贴切：专业 + 实习 + 竞赛 + 论文",
    "已放宽到本专业",
    "已放宽到专业大类",
    "已放宽到不限专业岗位（教培等）",
]

TIER_RULES = [
    "岗位要同时贴合专业与下面的实习、竞赛、论文，越具体越贴切越好；"
    "竞赛体现的建模/分析能力、论文体现的研究方向都要用上。",
    "忽略实习 / 竞赛 / 论文的具体细节，只按这个专业在校园招聘里的主流入口给岗位。",
    "**忽略实习 / 竞赛 / 论文的具体细节**，只按该专业所属的学科大类给岗位；"
    "只给该大类下绝大多数专业都能投的通用岗位："
    "管培生 / 职能管培生 / 储备干部 / 运营培训生 / 业务运营岗 / 产品运营岗 / 市场商务岗 / "
    "项目管理岗 / 客户成功专员 / 商业分析岗 / 数据分析岗 / 经营分析岗等。"
    "这些岗位不要求某个细分专业的专门技能。**不要给只属于单一细分专业的岗位**："
    "「供应链计划专员」只适合物流 / 管科，「嵌入式软件工程师」只适合电子 / 计算机，"
    "「量化研究员」只适合金融工程；也不要为了凑数硬挑企业库里的细分岗位。",
    "给不限专业的校招岗位：**至少 6 个必须是教培机构的岗位**"
    "（学科教师 / 课程顾问 / 学习规划师 / 校区运营管培生 / 教务专员 / 招生专员），"
    "其余可有销售管培、行政人事等；这些岗位都不要求专业对口。",
]

CATEGORY_HINT = [
    (["化学", "化工", "材料"], "理学·化学化工大类"),
    (["数学", "物理", "统计"], "理学·数理大类"),
    (["管理", "供应链", "物流", "工业工程", "工商", "会计", "营销", "运营"], "管理学（经管大类）"),
    (["计算机", "软件", "人工智能", "数据科学", "大数据", "信息安全"], "工学·计算机与信息类"),
    (["金融", "经济", "投资"], "经济学（经管大类）"),
    (["机械", "电气", "自动化", "材料", "土木", "建筑"], "工学大类"),
    (["生物", "医学", "临床", "药学", "环境"], "理学 / 医学大类"),
    (["中文", "外语", "新闻", "教育", "历史", "哲学"], "人文社科大类"),
]

# tier 2 / 3 的规则兜底池（模型失败时也能逐层放宽）
# tier 2「专业大类」层：只列该大类下各专业都能投的通用岗位。
# 不收「流程/精益改善岗」「信息系统实施顾问」这类偏单一细分方向的岗位。
CATEGORY_JOBS = [
    "管培生", "职能管培生", "储备干部", "运营培训生", "业务运营岗",
    "产品运营岗", "市场/商务岗", "项目管理岗", "客户成功专员",
    "商业分析岗", "数据分析岗", "经营分析岗",
]
NO_MAJOR_JOBS = [
    "学科教师", "课程顾问", "学习规划师", "校区运营管培生", "教务专员",
    "招生专员", "教育产品运营", "销售管培生", "行政/人事专员", "客户成功专员",
    "客服运营专员", "储备干部（不限专业）",
]

LLM_PROMPT = """你是双选会会前规划助手，帮学生想清楚「校园招聘里一般可以投哪些岗位」。

学生专业：{major}
所属学科大类：{category}
【实习】{internships}
【竞赛】{competitions}
【论文 / 科研】{papers}
【技能】{skills}

本轮推荐口径（重要）：{tier_rule}

历次已经展示过、本轮尽量不要重复的岗位：{shown}

【本次双选会企业库中真实在招的岗位（优先从这里挑，命中就把 from_db 标 true）】
{db_jobs}

请给出 12 个岗位名称，要求：
1. 基于通用认知回答「这类学生在普遍的校园招聘里一般能投哪些岗位」，
   不要复述某家公司今年的招聘公告，也不要写年份专属说法。
2. 岗位名用中国大陆校招通行的写法：「管培生 / 培训生 / 专员 / 分析师 / 助理 / 工程师」。
   不要输出「运营优化」「系统方案」这类职能描述。
   **档位必须是应届投得进的**：不要写「主管 / 资深 / 高级 / 组长 / 主任 / 总监 / 负责人 / 专家」；
   「经理」只保留校招真正常见的三种（客户经理、产品经理、业务经理），其余经理级一律不写。
   同一类活里也要区分入口档：给学生的是「管培生 / 专员」，不是「供应链主管」。
   **岗位名里不要带地区、行业、产品线、客户名等企业定制限定**：
   「运营产品经理(AI医疗)」「区域销售经理（IVD业务 广东）」「嵌入式软件工程师（心电贴片）」都不合格，
   要写成通用的「运营产品经理」「区域销售经理」「嵌入式软件工程师」。
3. 企业库里与**本轮口径**对口的岗位名可作为优先选项，但要去掉企业定制的括号限定后写成通用岗位名，
   命中就把 from_db 标 true；库里没有对口的就自己生成，不要为了凑数硬挑库里的岗位。
4. 依据只能来自上面的专业、大类、实习、竞赛、论文、技能，不要编造经历，不要提企业名称。
5. **竞赛 / 论文信号必须真正落到岗位选择上**：数学建模、预测建模类竞赛要落到
   数据分析 / 算法 / 策略分析 / 商业分析岗；治理策略、机制研究类论文要落到
   行业研究 / 咨询分析 / 供应链分析岗。不要只在 why 里提一句却给无关岗位。
6. 12 个岗位要尽量互不重复，也尽量避开已展示过的岗位。
7. 严格输出 JSON：{{"candidates":[{{"title":"岗位名","why":"一句话依据","from_db":true/false}}]}}
"""


def major_category(major: str) -> str:
    for keys, cat in CATEGORY_HINT:
        if any(k in (major or "") for k in keys):
            return cat
    return "与该专业相近的学科大类"


def _as_list(xs):
    out = []
    for x in (xs or []):
        if isinstance(x, dict):
            t = str(x.get("text") or x.get("name") or x.get("title") or "").strip()
        else:
            t = str(x or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def db_job_titles(limit=160):
    """读企业库真实在招岗位名（去重），只读本地库。"""
    try:
        import sqlite3
        db = os.path.join(DATA, "careersafari.db")
        if not os.path.isfile(db):
            return []
        con = sqlite3.connect(db, timeout=8)
        try:
            rows = con.execute(
                "SELECT item_json FROM company_jobs WHERE item_json IS NOT NULL").fetchall()
        finally:
            con.close()
        seen, out = set(), []
        for (raw,) in rows:
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            jobs = item.get("jobs") if isinstance(item, dict) else item
            if not isinstance(jobs, list):
                continue
            for j in jobs:
                t = (j.get("title") if isinstance(j, dict) else str(j)) or ""
                t = t.strip()
                if not t or t in seen or len(t) > 30:
                    continue
                seen.add(t)
                out.append(t)
        return out[:limit]
    except Exception:
        return []


def _load_env() -> dict:
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


_TITLE_JUNK = re.compile(r"[（(][^)）]*[)）]")


def _clean_title(t):
    """去掉企业定制的括号限定与年份/编号尾巴，保证输出是通用校招岗位名。"""
    t = str(t or "").strip()
    t = _TITLE_JUNK.sub("", t)
    t = re.sub(r"20\d{2}\s*届?(?:校园招聘|秋招|春招)?", "", t)
    t = re.sub(r"MJ\d+", "", t, flags=re.I)
    t = re.sub(r"\s+", "", t)
    return re.sub(r"[·、,，/\-—]+$", "", t).strip()


def _pool_key(major, cats, tier, accept, model_tag=""):
    """池子缓存的键。

    必须带上模型名、提示词版本与**本模块代码指纹**：
      · 少了模型名：换模型（glm -> deepseek）后旧池子仍被命中，页面标着
        mode=llm 其实是旧模型结果，看起来像"新模型没生效"；
      · 少了代码指纹：改 MAJOR_BASE / rule_pool / LLM_PROMPT 后旧池子照样命中，
        页面给出的还是改之前的岗位名前缀。
    """
    raw = json.dumps([major, cats["internships"], cats["competitions"],
                      cats["papers"], cats["skills"], int(tier), bool(accept),
                      model_tag, PROMPT_VER,
                      cache_guard.code_fp(name="job_recommend_pool")],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:20]


def _load_pools():
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pools(pools):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pools, f, ensure_ascii=False)
    except Exception:
        pass


def rule_pool(major, cats, tier, accept, db_jobs):
    """模型不可用时的分层规则池。三类经历都参与 tier 0 的信号匹配。"""
    text = " ".join(cats["internships"] + cats["competitions"] +
                    cats["papers"] + cats["skills"])
    base = list(MAJOR_BASE.get(major, []))
    if not base:
        for k, v in MAJOR_BASE.items():
            if k in major or major in k:
                base = list(v)
                break
    if not base:
        base = ["管培生", "数据分析岗", "项目管理岗", "业务运营岗"]

    items, seen = [], set()

    def add(title, why, from_db=False):
        t = _clean_title(title)
        if not t or t in seen or len(items) >= 12:
            return
        seen.add(t)
        items.append({"title": t, "why": why, "from_db": from_db})

    if tier == 0:
        # 库里真实岗位 + 三类经历命中的方向 + 专业基线
        for dj in (db_jobs or []):
            for kw in base:
                core = re.sub(r"[（(].*?[)）]", "", kw)
                core = re.sub(r"(岗|专员|工程师|管培生|经理|助理|分析师)$", "", core)
                # 核心词至少 3 字：「研发」这种 2 字泛词会把 数据库研发工程师/
                # C++研发工程师 等计算机岗全误吸进来；3 字起才做库内匹配。
                if core and len(core) >= 3 and core in dj:
                    add(dj, "企业库现场在招，与专业方向对口", True)
                    break
        for kws, jobs, why in SIGNAL_RULES:
            if any(k.lower() in text.lower() for k in kws):
                for j in jobs:
                    add(j, why)
        for j in base:
            add(j, "与专业「%s」的培养方向对口" % major)
    elif tier == 1:
        for j in base:
            add(j, "按专业「%s」的主流校招入口" % major)
        for j in CATEGORY_JOBS[:5]:
            add(j, "本专业常见的相邻岗位")
    elif tier == 2:
        # 这一层只给大类中各专业都能投的通用岗位，不再掺入本专业专属岗位
        for j in CATEGORY_JOBS:
            add(j, "按 %s 放宽后的大类通用岗位" % major_category(major))
    else:
        if not accept:
            return []
        for j in NO_MAJOR_JOBS:
            add(j, "不限专业岗位；你已选择愿意考虑教培类企业")
    return items


def llm_pool(major, cats, tier, accept, key, base_url, model, db_jobs, shown):
    prompt = LLM_PROMPT.format(
        major=major or "未填写",
        category=major_category(major),
        internships="；".join(cats["internships"]) or "无",
        competitions="；".join(cats["competitions"]) or "无",
        papers="；".join(cats["papers"]) or "无",
        skills="、".join(cats["skills"]) or "无",
        tier_rule=TIER_RULES[tier],
        shown="、".join(shown) or "无",
        db_jobs="、".join(db_jobs or []) or "（未读到企业库，按通用判断即可）")
    payload = {"model": model,
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.3}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = json.loads(r.read().decode("utf-8"))
        data = _parse_json(raw["choices"][0]["message"]["content"])
        out, seen = [], set()
        for c in data.get("candidates", []):
            if not (isinstance(c, dict) and c.get("title")):
                continue
            t = _clean_title(c["title"])
            if not t or t in seen:
                continue
            seen.add(t)
            out.append({"title": t, "why": str(c.get("why", "")),
                        "from_db": bool(c.get("from_db"))})
            if len(out) >= 12:
                break
        return out or None
    except Exception as e:
        print("[job_recommend] 模型调用失败，回退规则：%s" % e, file=sys.stderr)
        return None


def get_pool(major, cats, tier, accept, cfg, db_jobs, use_llm=True):
    """每个 (专业, 三类经历, 层级, 模型) 一个固定池子，缓存到磁盘，避免换一批时反复调模型。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
                or "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"

    key = _pool_key(major, cats, tier, accept, model)
    with _LOCK:
        pools = _load_pools()
        hit = pools.get(key)
        if isinstance(hit, dict) and hit.get("items"):
            return hit["items"], hit.get("mode", "rules")

    items = None
    if api_key and use_llm:
        items = llm_pool(major, cats, tier, accept, api_key, base_url, model,
                         db_jobs, [])
    mode = "llm" if items else "rules"
    if not items:
        items = rule_pool(major, cats, tier, accept, db_jobs)
    elif len(items) < 12:
        # 模型常只给 6~8 个：用同层规则池补齐到 12，保证「换一批」每层有足够候选。
        # 规则池本身按层级设计（tier2 全是大类通用岗位），不会把口径带窄。
        seen = {c["title"] for c in items}
        for c in rule_pool(major, cats, tier, accept, db_jobs):
            if c["title"] not in seen:
                items.append(c)
                seen.add(c["title"])
            if len(items) >= 12:
                break
    if tier == 3 and accept:
        # 保底：这一层是「放宽到不限专业」，教培岗位必须真的出现在最前面，
        # 不能全靠模型自觉（实测模型有时只给市场/人事这类泛岗位）。
        seed = [{"title": t, "why": "不限专业岗位；你已选择愿意考虑教培类企业",
                 "from_db": False} for t in NO_MAJOR_JOBS[:6]]
        seed_titles = {s["title"] for s in seed}
        items = seed + [c for c in items if c["title"] not in seed_titles]
    with _LOCK:
        pools = _load_pools()
        pools[key] = {"tier": tier, "mode": mode, "items": items,
                      "model": model, "prompt_ver": PROMPT_VER,
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "_cg": cache_guard.stamp("job_recommend_pool", model=model)}
        _save_pools(pools)
    return items, mode


def recommend(major, internships=None, competitions=None, papers=None,
              skills=None, use_llm=True, shown=None, tier=0,
              accept_no_major=False, experiences=None):
    """返回一批 6 个岗位；当前层池子展示完会自动升层。

    shown：采集端历次展示过的岗位名（全局累积），跨层也不重复。
    experiences：兼容旧调用，内容并入实习。
    """
    cats = {
        "internships": _as_list(list(internships or []) + list(experiences or [])),
        "competitions": _as_list(competitions),
        "papers": _as_list(papers),
        "skills": _as_list(skills),
    }
    shown = _as_list(shown)
    try:
        start = max(0, min(3, int(tier)))
    except Exception:
        start = 0
    max_tier = 3 if bool(accept_no_major) else 2

    cfg = _load_env()
    db_jobs = db_job_titles()

    for t in range(start, max_tier + 1):
        pool, mode = get_pool(major, cats, t, accept_no_major, cfg, db_jobs, use_llm)
        fresh = [c for c in pool if c["title"] not in shown]
        if fresh:
            batch = fresh[:6]
            left = len(fresh) - len(batch)
            note = ("第 %d 层「%s」：本层还剩 %d 个岗位，换完会自动放宽到下一层。"
                    % (t + 1, TIER_LABELS[t], left)) if t < max_tier else \
                   ("已是最宽口径「%s」。本层还剩 %d 个。" % (TIER_LABELS[t], left))
            return {"ok": True, "mode": mode, "candidates": batch,
                    "tier": t, "tier_label": TIER_LABELS[t],
                    "relaxed": t > start, "pool_left": left,
                    "accept_no_major": bool(accept_no_major),
                    "notice": note}
    # 所有层都展示完：如实说明，不再编新岗位
    return {"ok": True, "mode": "rules", "candidates": [],
            "tier": max_tier, "tier_label": TIER_LABELS[max_tier],
            "relaxed": True, "pool_left": 0,
            "accept_no_major": bool(accept_no_major),
            "notice": "各层岗位都展示过了，可以手写想投的岗位，或回到上面的问题改条件。"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.startswith("/api/fair/recommend-jobs"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        out = recommend(
            body.get("major", ""),
            internships=body.get("internships") or [],
            competitions=body.get("competitions") or [],
            papers=body.get("papers") or [],
            skills=body.get("skills") or [],
            use_llm=body.get("use_llm", True),
            shown=body.get("shown") or body.get("exclude") or [],
            tier=body.get("tier", 0),
            accept_no_major=bool(body.get("accept_no_major")),
            experiences=body.get("experiences") or [])
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--major", default="运营与供应链管理")
    ap.add_argument("--intern", action="append", default=[])
    ap.add_argument("--competition", action="append", default=[])
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--skill", action="append", default=[])
    ap.add_argument("--tier", type=int, default=0)
    ap.add_argument("--accept-no-major", action="store_true")
    ap.add_argument("--shown", action="append", default=[])
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8100)
    a = ap.parse_args()

    if a.serve:
        print("job_recommend 服务 http://127.0.0.1:%d/api/fair/recommend-jobs" % a.port)
        HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
        return

    intern = a.intern
    comp = a.competition
    paper = a.paper
    if a.demo or not (intern or comp or paper):
        intern = ["中国船舶集团第七一二研究所 供应链管理部实习：供应商评估体系优化、战略寻源、OTD 跟踪与风险预警、采购定价优化"]
        comp = ["美国大学生数学建模大赛 国际级 Finalist(2%) 核心队员：Python 数据清洗、XGBoost 预测建模、异常值处理"]
        paper = ["本科毕业论文《集团型企业供应商共享治理策略研究》：供应商激励机制、信息上报与风险治理"]
    skills = a.skill or ["Python", "R", "SQL", "供应链建模"]

    r = recommend(a.major, internships=intern, competitions=comp, papers=paper,
                  skills=skills, use_llm=not a.no_llm, shown=a.shown,
                  tier=a.tier, accept_no_major=a.accept_no_major)
    print("专业：%s ｜ 层级 %d：%s" % (a.major, r["tier"], r["tier_label"]))
    print("模式：%s ｜ %s" % (r["mode"], r["notice"]))
    print("\n推荐岗位（学生勾选，不写进企业事实字段）：")
    for i, c in enumerate(r["candidates"], 1):
        flag = "【企业库在招】" if c.get("from_db") else ""
        print("  %d. %-20s %s%s" % (i, c["title"], flag, c.get("why", "")))


if __name__ == "__main__":
    main()
