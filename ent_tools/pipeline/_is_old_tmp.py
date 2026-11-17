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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "intent_semantics_cache.json")

PROMPT = """你是校招岗位语义匹配助手。
学生意向岗位：{intent}
下面是本次双选会企业校招公告里出现的岗位名（去重清单）：{titles}

请从清单里挑出与「{intent}」属于同一类校招工作、应届生可以同样投递的岗位名。

要求：
1. 只能写清单里出现过的原词，不要造新岗位名；最多 25 个，按相符程度从高到低排序；同一个岗位不要重复输出。
2. **先扫描整份清单**：凡是属于同一职能的岗位都要挑出来，不要只挑最先看到的几个；漏掉一个明确同类比多挑一个模糊岗位更糟。
3. 判定标准：岗位名是「{intent}」的另一种叫法（同义、简称），或日常工作职责与「{intent}」基本相同。
   不看学生学过哪些课程、具备哪些能力。仅在名字里共享一个通用词（专员 / 管培生 / 工程师 / 经理 / 助理 / 主管）不算同类。
4. **必须算同类**：
   a) 同义简称：「人事 / 人资 / 人力资源」同义；「会计 / 财务 / 审计 / 税务 / 资金」属同一职能；「采购 / 寻源 / 供应链 / 计划 / 物流 / 仓储 / 关务」属同一职能链；「法务 / 法律 / 合规」同义；「市场 / 营销 / 品牌 / 推广」属同一职能。
   b) **同一职能内**级别不同也算同类：「管培生 / 校招生 / 专员 / 助理 / 储备干部 / 主管」只是级别差异。注意必须先是同一职能——「IT 数字化管培生」「售前管培生」「教学管培生」「营销管培生」与「供应链管培生」只是都叫管培生，方向完全不同，不算同类。
   c) **细分技术方向**：当意向本身是软件开发 / 研发类岗位时，其细分方向算同类，包括「前端 / 后端 / 嵌入式 / 全栈 / 服务端 / 客户端 / Java / C++ / Python / PHP / Unity3d / 大模型应用 / 低代码」。
   d) 中小学教师：岗位名列出多个学科且**包含**意向学科，就算同类（如「初中学习机教师(语文、数学、英语)」对「中学语文教师」算同类）。
5. **明确不算同类**，务必排除：
   - 测试 / 运维 / DBA / 数据库内核 / 集成实施 / 售后服务岗；工艺 / 设备 / 生产 / 质检 / 车间 / 维修岗；
   - 销售类不算技术、专业、职能类意向；财务 / 会计 / 审计不算供应链、数据、人力、市场类意向；
   - 「战略管培生」不算「供应链管培生」；「-工艺方向 / -设备方向」不算「-运营方向」；
   - 教师须学科一致：意向语文时，只教数学 / 英语 / 化学 / 历史 / 体育 / 信息技术 / 理科竞赛的不算；
   - 专业名、学科名不是岗位（「电气工程及其自动化」「机械类」「电气类」）；招聘批次名不是岗位（「2027 届校园招聘」）；
   - 产品 / 产品经理不算临床医学、法务、人力资源这类专业岗；
   - 运营 / 企宣 / 公关 / 会展不算「市场营销专员」；质量 / 体系 / 解决方案 / 技术支持不算设计、研发岗；
   - 不同专业的设计岗不算同类：「机械设计工程师」不算「模拟 IC 设计 / 光学设计 / 逻辑设计 / 电磁设计」。
6. 对每个挑中的岗位写一句 reason：字面接近写「「XX」即「{intent}」的另一种说法」；字面差距大但职责相同，必须以「大模型判断二者类似工作内容：」开头。
7. 不要输出任何解释文字。严格输出 JSON：{{"titles":[{{"title":"岗位名","reason":"..."}}]}}
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


# 明显的「工程 / 研发」职责词：企业岗位名含这些词、而意向岗位不含时，
# 即使共享「数据」「供应链」等字也不能算同类（防模型按关键词凑数）。
ROLE_CONFLICT = (
    "内核", "测试", "DBA", "运维", "研发", "嵌入式", "前端", "后端",
    "驱动", "固件", "编译", "工艺", "设备", "生产", "质检", "研究员",
    "开发工程师", "集成工程师", "服务工程师",
)


# 不带任何专业 / 职能方向的泛岗位名。它们不能算作「供应链管培生」
# 「采购寻源专员」这类带方向意向的同类 —— 除非意向本身就是泛词。
GENERIC_JOB = {
    "管培生", "培训生", "管理培训生", "职能管培生", "运营培训生",
    "储备干部", "校招生", "专员", "助理", "实习生", "干部", "管培岗",
}


def _func_terms(title):
    """岗位名命中的职能标记集合（确定性，用于跨职能兜底，不依赖模型）。"""
    t = title or ""
    tags = set()
    if any(k in t for k in ("会计", "财务", "审计", "税务", "核算", "出纳")):
        tags.add("fin")
    if "战略" in t:
        tags.add("strat")
    if "数据库" in t:
        tags.add("db")
    if any(k in t for k in ("供应链", "物流", "采购", "寻源", "仓储", "关务", "计划")):
        tags.add("supply")
    if any(k in t for k in ("运营", "经营管理", "电商运营")):
        tags.add("ops")
    if any(k in t for k in ("数据分析", "商业分析", "量化", "分析", "数据")):
        tags.add("data")
    if any(k in t for k in ("工程师", "研发", "开发", "嵌入式", "前端", "后端", "测试", "算法")):
        if "data" not in tags:
            tags.add("eng")
    return tags


def _role_ok(intent, title):
    if title in GENERIC_JOB and intent not in GENERIC_JOB:
        return False
    # 专业名称 / 招聘批次名不是岗位
    if _is_major_name(title) or _is_campaign_name(title):
        return False
    # 学科：岗位列了多个学科且含意向学科 → 算同类；完全不含 → 拦。
    # （修「初中学习机教师(语文、数学、英语)」对「中学语文教师」的误杀）
    ts = {s for s in SUBJECTS if s in title}
    is_ = {s for s in SUBJECTS if s in intent}
    if is_ and ts and not (is_ & ts):
        return False
    # 销售岗不能算作非销售/非市场类意向（「临床&终端销售经理」≠ 临床医学专员）
    if "销售" in title and not any(k in intent for k in ("销售", "营销", "市场")):
        return False
    # 产品岗不算专业 / 职能岗（「产品专员」≠「临床医学专员」）
    if any(w in title for w in PROD_WORDS) and not any(
            k in intent for k in ("产品", "市场", "营销", "运营")):
        return False
    # 运营 / 企宣 / 公关不算市场类意向；但同职能族内的「-运营方向」要放行
    # （「供应链事业群管培生-供应链运营方向」不能被这条误杀）
    if any(w in title for w in OPS_WORDS) and "运营" not in intent \
       and not (_func_family(intent) & _func_family(title)):
        return False
    # 跨专业设计岗：「机械设计工程师」不算「模拟IC / 光学 / 电磁 / 逻辑设计」
    if "设计" in title and not (_DESIGN_FIELDS & set(_grams(title))):
        if any(f in title and f not in intent for f in DESIGN_FIELD_WORDS):
            return False
    # 跨职能硬错兜底：财务 / 战略 / 数据库 类岗位，意向不是该类时不算同类
    t_tags = _func_terms(title)
    i_tags = _func_terms(intent)
    if ("fin" in t_tags and "fin" not in i_tags) or \
       ("strat" in t_tags and "strat" not in i_tags) or \
       ("db" in t_tags and "db" not in i_tags):
        return False
    # 「运营培训生（综合柜员方向）」虽然字面共享「运营/培训」，但是银行柜面岗
    if "柜员" in title and "柜员" not in intent:
        return False
    # 职责冲突词：细分方向词只在「软件/开发类意向」放行；
    # 「研发 / 开发工程师」在意向与岗位同职能族时也放行（机械研发应用、电气研发）。
    soft = _is_soft_intent(intent)
    for k in ROLE_CONFLICT:
        if k in title and k not in intent:
            if soft and k in DEV_SUBS:
                continue
            if k in ("研发", "开发工程师") and _is_eng_intent(intent) and (
                    (_func_family(intent) & _func_family(title))
                    or (_grams(intent) - GEN_GRAMS) & _grams(title)):
                continue
            return False
    return True


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


# 「同一职能、换种叫法」的常见族。只用于救「字面零重合但确实是同类」的漏判，
# 例如「采购寻源专员」↔「供应链事业群管培生-供应链运营方向」。
# 不把 eng / mkt / hr 放进族表——那几类字面差异不大的误判风险高。
FUNC_FAMILY = {
    # 「计划」不能裸用：「启明星计划竞赛教练」会因此被当成供应链岗
    "supply": ("供应链", "物流", "采购", "寻源", "仓储", "关务", "库存", "配送",
               "物资", "PMC", "备件", "生产计划", "物料计划", "计划专员", "计划管培",
               "计划助理", "综合计划", "计划主管", "计划岗"),
    "data": ("数据分析", "商业分析", "量化", "BI", "数据科学", "统计"),
    "hr": ("人力资源", "人力", "人资", "人事", "招聘", "薪酬", "员工关系",
           "培训专员", "培训岗", "培训管培", "培训主管", "培训经理", "培训助理"),
    "fin": ("财务", "会计", "审计", "税务", "核算", "出纳", "资金"),
    "legal": ("法务", "法律", "合规", "知识产权", "诉讼"),
    "mkt": ("市场", "营销", "品牌", "推广"),
}

# 技术岗词：族相交放行时，这些词必须「意向本身也有」才放，
# 否则「物流工程师」「PMC 工程师」会被错算成「供应链管培生」。
TECH_WORDS = ("工程师", "研发", "开发", "算法", "研究员", "测试", "工艺", "设备", "质检")

# 学科词：双选会里「教语文」和「教化学」是两个完全不同的岗位，
# 模型（尤其 DeepSeek）会把同类学科都挑进来，必须按学科互斥硬拦。
SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史",
            "地理", "体育", "音乐", "美术", "信息技术", "理科", "文科", "科学",
            "心理健康", "道德与法治")

# 产品岗：不算临床医学、法务、人力资源这类专业岗（修「产品专员」↔「临床医学专员」）
PROD_WORDS = ("产品经理", "产品专员", "产品工程师", "产品岗", "产品规划", "产品运营")
# 运营 / 企宣 / 公关：不算「市场营销专员」等职能岗
OPS_WORDS = ("运营", "企宣", "公关", "会展", "会议策划")
# 只有软件 / 开发类意向才允许「前端 / 后端 / 嵌入式 …」这些细分方向词放行
SOFT_INTENT_WORDS = ("开发", "研发", "软件", "程序", "算法")
DEV_SUBS = ("前端", "后端", "嵌入式", "驱动", "固件", "编译", "全栈", "服务端", "客户端",
            "C++", "Java", "Python", "PHP", "研发", "开发工程师", "Unity3d", "C#")
DEV_TITLE_HINTS = ("前端", "后端", "嵌入式", "全栈", "服务端", "客户端", "Java", "C++", "C#",
                   "Python", "PHP", "Unity3d", "大模型应用", "低代码", "鸿蒙", "Android",
                   "IOS", "上位机", "软件工程师")
GEN_GRAMS = {"专员", "管培", "培生", "培训", "训生", "管理", "助理", "储干",
             "干部", "工程", "经理", "主管", "储备"}

# 明显是「专业名称」而非岗位名称的写法（如「电气工程及其自动化」被当岗位挑中）。
def _is_major_name(title):
    t = (title or "").strip()
    return t.endswith("及其自动化") or t.endswith("专业") or "及其自动化" in t


# 「2027 届校园招聘」「2027届秋季校园招聘」是招聘批次名，不是岗位名。
def _is_campaign_name(title):
    t = (title or "").strip()
    return "校园招聘" in t or bool(re.search(r"\d{4}\s*届", t))


def _is_soft_intent(intent):
    return any(k in (intent or "") for k in SOFT_INTENT_WORDS)


def _is_eng_intent(intent):
    """意向本身是不是工程 / 设计岗（决定「研发 / 开发工程师」能否算同类）。"""
    return any(k in (intent or "") for k in ("设计", "工程师", "研发", "开发", "机械", "电气"))


# 跨专业设计方向词：机械设计不吃 IC / 光学 / 电磁 这类设计岗
DESIGN_FIELD_WORDS = ("IC", "芯片", "光学", "光机", "电磁", "逻辑", "物理", "数字",
                      "模拟", "射频", "FPGA", "版图", "PCB")
_DESIGN_FIELDS = set()


def _grams(s):
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def _func_family(text):
    t = text or ""
    return {f for f, kws in FUNC_FAMILY.items() if any(k in t for k in kws)}


def _share_core(intent, title):
    """两道：① 共享 2 字核心词（字面同义）；② 或同属一个职能族且不是跨到技术岗。

    ② 是确定性兜底，专门修 ① 的字面盲区：采购寻源专员 与 供应链管培生 一个字都不重合，
    但确属同一职能。族相交只在「业务岗」上生效，技术岗词仍要求意向自身含该词，
    因此不会把「数据开发工程师」「药物分析研究员」（各自带 开发/研究员）放进来。
    """
    grams = _grams(intent)
    # 只共享「管培 / 专员 / 工程 / 经理」这类通用词不算同类，
    # 否则「IT 数字化管培生」「营销管培生」都会算进「供应链管培生」。
    if any(g in title for g in (grams - GEN_GRAMS)):
        return True
    if intent in GENERIC_JOB and any(g in title for g in grams):
        return True
    if not (_func_family(intent) & _func_family(title)):
        return False
    return not any(w in title and w not in intent for w in TECH_WORDS)


def _seeds(intent, title_set):
    """确定性召回：不指望模型「想起」字面同名的岗位。

    只认三种强证据：① 与意向同名/包含关系；② 同职能族；③ 软件类意向的细分方向。
    它们仍要过 _role_ok / _share_core，所以只补漏，不放宽语义。
    """
    fam = _func_family(intent)
    soft = _is_soft_intent(intent)
    out = []
    for t in title_set:
        strong = (intent in t) or (t in intent and len(t) >= 4)
        if strong or (fam & _func_family(t)) \
           or (soft and any(h in t for h in DEV_TITLE_HINTS)):
            out.append(t)
    return out


def _strength(intent, t, picked):
    """排序权重：字面同名 > 共享核心词 > 同职能族 > 模型挑中。"""
    igc = _grams(intent) - GEN_GRAMS
    s = 0
    if intent in t or (t in intent and len(t) >= 4):
        s += 10
    s += 3 * len(igc & _grams(t))
    if _func_family(intent) & _func_family(t):
        s += 2
    if _is_soft_intent(intent) and any(h in t for h in DEV_TITLE_HINTS):
        s += 4
    if picked:
        s += 1
    return s


def _sanitize(intent, tl, title_set, use_seed=True, cap=25):
    """去重 + 去幻觉 + 去职责冲突 + 补确定性种子 + 按强度排序截断。"""
    picked = {str(t).strip() for t in (tl or []) if str(t).strip()}
    pool = list(picked)
    if use_seed:
        pool += [t for t in _seeds(intent, title_set) if t not in picked]
    keep = []
    for t in pool:
        t = str(t).strip()
        if not t or t in keep or t not in title_set:
            continue
        if not _role_ok(intent, t) or not _share_core(intent, t):
            continue
        keep.append(t)
    keep.sort(key=lambda t: -_strength(intent, t, t in picked))
    return keep[:cap]


def _tag_reason(r):
    """确保「名字差距大、但干的活差不多」这类判定，reason 以
    「大模型判断二者类似工作内容：」开头——这是告诉学生「这是模型判定、不是字面同名」的关键。
    同义（即另一种说法）类不动；已带前缀的也不重复加。"""
    if not r:
        return r
    if r.startswith(("大模型判断", "大模型判定")):
        return r
    if any(k in r for k in ("干的活", "职责", "相近", "类似工作")):
        return "大模型判断二者类似工作内容：" + r
    return r


def _clean_map(mp, title_set):
    """缓存里的旧结果也走同一套净化，改规则后不必重调模型。"""
    cleaned = {}
    for k, v in (mp or {}).items():
        titles = _sanitize(k, v.get("titles") or [], title_set)
        reasons = {t: _tag_reason(r) for t, r in (v.get("reasons") or {}).items()
                   if t in titles and r}
        cleaned[k] = dict(v, titles=titles, reasons=reasons)
    return cleaned


def build_map(intended, all_titles, timeout=45):
    """返回 {意向岗位: {titles, terms, reason, via}}；失败返回上次判定或 {}。"""
    intended = [str(x).strip() for x in (intended or []) if str(x).strip()]
    titles = sorted({str(t).strip() for t in (all_titles or []) if str(t).strip()})
    if not intended or not titles:
        return {}
    title_set = set(titles)
    cfg = _load_env()
    # 模型名 / prompt 都进指纹：换模型或改提示词时旧缓存必须失效，
    # 否则页面会继续用上一个模型的结果。
    model_tag = (os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "")
    digest = hashlib.md5(
        ("|".join(sorted(intended)) + "||" + "|".join(titles) + "||" + model_tag
         + "||" + PROMPT).encode("utf-8")
    ).hexdigest()
    cached_map = {}
    if os.path.isfile(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            cached_map = cached.get("map") or {}
            if cached.get("key") == digest and cached_map:
                return _clean_map(cached_map, title_set)
        except Exception:
            pass

    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")):
        return _clean_map(cached_map, title_set)
    short = _candidate_titles(titles, intended)
    pool = "、".join(short or titles[:240])

    out = {}
    for it in intended:
        try:
            data = _call(cfg, PROMPT.format(intent=it, titles=pool), timeout)
            raw = (data or {}).get("titles") or []
            # 兼容旧缓存：可能是字符串列表，也可能是 {title, reason} 对象列表。
            pairs = []
            for x in raw:
                if isinstance(x, dict):
                    t = str(x.get("title") or "").strip()
                    r = _tag_reason(str(x.get("reason") or "").strip())
                else:
                    t = str(x).strip()
                    r = ""
                if t:
                    pairs.append((t, r))
            titles_only = [t for t, _ in pairs][:25]
            sani = _sanitize(it, titles_only, title_set)
            # 只在净化后仍存活的岗位名上保留其专属 reason，避免脏 key。
            reasons = {t: r for t, r in pairs if t in sani and r}
            # 种子岗（规则补回来的同类）没有模型 reason，补一句可解释说明，
            # 学生才知道这是「同职能不同叫法」而不是模型瞎猜。
            for t in sani:
                if t in reasons:
                    continue
                if it in t or (t in it and len(t) >= 4):
                    reasons[t] = "「%s」即「%s」的另一种说法" % (t, it)
                else:
                    reasons[t] = ("大模型判断二者类似工作内容：「%s」与「%s」同属一个职能，"
                                  "岗位名不同但职责同类" % (t, it))
        except Exception as exc:
            # 偶发超时/断网：沿用上一次成功的大模型判定，别让功能直接退化成字面匹配。
            print("[intent_semantics] %s 判定失败，沿用上次结果：%s" % (it, exc))
            return _clean_map(cached_map, title_set) or {}
        out[it] = {"titles": sani, "reasons": reasons, "terms": [],
                   "reason": "大模型判定为同类校招岗位", "via": "llm"}

    if not any(v["titles"] for v in out.values()):
        return _clean_map(cached_map, title_set) or {}
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"key": digest, "map": out}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out
