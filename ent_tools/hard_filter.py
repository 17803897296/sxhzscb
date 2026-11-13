# -*- coding: utf-8 -*-
"""
采集端硬约束 -> 结构化过滤条件（2026-11-13 新增）。

学生只写一句自由文本（「只要南京的工作」「只看互联网」「不要外企」），
这里把它解析成 {cities, industries, exclude_natures, keywords}，
再对 122 家企业做**硬过滤**——不满足的直接从名单里消失，不是降权。

两级策略：
  1. 大模型解析（主路径）。给模型企业库里真实存在的城市 / 行业枚举，
     让它把口语映射到库里的值域，避免「互联网」对不上「IT」。
  2. 规则兜底。按城市名 / 行业名 / 否定词做字面匹配，模型不可用时仍能工作。

结果缓存到 data/hard_filter_cache.json，同一句话不重复调模型。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "hard_filter_cache.json")

# 常见的「不限」表述，视为不设限
_NONE_PAT = re.compile(r"^(没有|无|不设限|暂无|随便|都行|不限|没有要求)$")

LLM_PROMPT = """把学生的硬性要求，翻译成筛选条件。只输出 JSON，不要解释。

学生原话：{text}

企业库中实际存在的城市：{cities}
企业库中实际存在的行业：{industries}
企业库中实际存在的企业性质：{natures}

请输出：
{{"cities":[], "industries":[], "exclude_natures":[], "keywords":[], "note":"一句话复述你理解的条件"}}

规则：
1. cities：提到具体城市就填库里的标准城市名（如「南京」「上海」）。说「省内」「北方」这类无法对应到具体城市的，不要填。
2. industries：提到行业就映射到库里的**行业标准值**。注意口语映射：互联网/科技/软件 → IT；金融/银行 → 金融；
   制造业/工厂 → 制造；医药/医疗 → 医药。无法映射的不要填。
3. exclude_natures：出现「不要外企」「不接受民企」这类否定，填排除的性质（外企/民企/央企/国企/事业单位）。
4. keywords：无法归到上面三类的限定词（如「上市」「500强」）填这里。
5. 如果学生原话是「没有限制」「都可以」这类，四个数组全部返回空。
"""


def is_none(text: str) -> bool:
    t = (text or "").strip()
    return (not t) or bool(_NONE_PAT.match(t))


def _load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(d: dict):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _env() -> dict:
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
    i = text.find("{")
    if i > 0:
        text = text[i:]
    return json.loads(text)


def rule_parse(text: str, cities: list, industries: list) -> dict:
    """规则兜底：字面命中城市 / 行业 + 否定词识别。"""
    t = (text or "")
    hit_city = [c for c in cities if c and c in t]
    hit_ind = [c for c in industries if c and c in t]
    # 口语 -> 库内行业
    alias = {"互联网": "IT", "科技": "IT", "软件": "IT", "计算机": "IT", "程序员": "IT",
             "银行": "金融", "证券": "金融", "券商": "金融",
             "工厂": "制造", "制造业": "制造", "医疗": "医药", "制药": "医药"}
    for k, v in alias.items():
        if k in t and v in industries and v not in hit_ind:
            hit_ind.append(v)
    excl = []
    for nat in ("外企", "民企", "央企", "国企", "事业单位"):
        if re.search(r"(不要|不考虑|排除|拒绝|不看)\s*" + nat, t):
            excl.append(nat)
    return {"cities": hit_city, "industries": hit_ind, "exclude_natures": excl,
            "keywords": [], "note": "规则解析（模型未启用）", "mode": "rules"}


def parse(text: str, cities: list, industries: list, natures: list) -> dict:
    """入口：返回结构化的硬约束条件。空/不限 -> 全空。"""
    if is_none(text):
        return {"cities": [], "industries": [], "exclude_natures": [], "keywords": [],
                "note": "未设限", "mode": "none"}

    cache = _load_cache()
    if text in cache:
        return cache[text]

    cfg = _env()
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"

    out = None
    if key:
        try:
            prompt = LLM_PROMPT.format(
                text=text,
                cities="、".join(cities[:60]) or "（未知）",
                industries="、".join(industries) or "（未知）",
                natures="、".join(natures) or "（未知）")
            payload = {"model": model, "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}
            req = urllib.request.Request(
                base.rstrip("/") + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = json.loads(r.read().decode("utf-8"))
            d = _parse_json(raw["choices"][0]["message"]["content"])
            # 防呆：模型的 note 或字段里若把「全部候选」当成了条件（曾把 4 个城市
            # 全填进 cities，等于删光了其他城市的企业），视为该项未设限。
            _cities = [c for c in (d.get("cities") or []) if c]
            _inds = [c for c in (d.get("industries") or []) if c]
            if len(_cities) >= max(3, len(cities)) and len(_cities) >= len(set(_cities)):
                _cities = []
            if len(_inds) >= max(3, len(industries)):
                _inds = []
            out = {
                "cities": _cities,
                "industries": _inds,
                "exclude_natures": [c for c in (d.get("exclude_natures") or []) if c],
                "keywords": [c for c in (d.get("keywords") or []) if c],
                "note": str(d.get("note") or text),
                "mode": "llm",
            }
            # 兜底：模型没解析出任何条目的城市/行业，就用规则再补一遍
            if not out["cities"] and not out["industries"] and not out["exclude_natures"]:
                _r = rule_parse(text, cities, industries)
                if _r["cities"] or _r["industries"] or _r["exclude_natures"]:
                    _r["mode"] = "llm+rules"
                    out = _r
        except Exception as e:
            print("[hard_filter] 模型解析失败，回退规则：%s" % e, file=sys.stderr)
            out = None

    if out is None:
        out = rule_parse(text, cities, industries)

    cache[text] = out
    _save_cache(cache)
    return out


def match(company: dict, cond: dict) -> bool:
    """该企业是否满足硬约束。任一条件不满足即 False（硬剔除）。"""
    if not cond:
        return True
    cities = cond.get("cities") or []
    inds = cond.get("industries") or []
    excl = cond.get("exclude_natures") or []

    if cities:
        city = str(company.get("city") or "")
        if not any(c and c in city for c in cities):
            return False
    if inds:
        ind = str(company.get("industry") or "")
        if not any(i == ind or (i and i in ind) or (ind and ind in i) for i in inds):
            return False
    if excl:
        nat = str(company.get("nature") or "")
        if any(n and n in nat for n in excl):
            return False
    return True


# ---------------------------------------------------------------- 教培类判定
# 判据不是「行业=教育」——库里教育行业 20 家里混着贵州师范大学、清华附中这类
# 正规学校（有编制、招教学科研/行政岗），还有绍兴集成电路设计中心（挂在教育下
# 但实际招芯片工程师）。真正该被「不接受教培」筛掉的是**主营 K12 学科培训**的机构。
#
# 三步判据，任一强信号即可定，避免单靠名称误判：
#   A. 名称含「培训」且不含高校/附中特征 → 教培（「蓝天教育培训学校」属此列）
#   B. 名称含知名教培品牌 → 教培
#   C. 岗位里 2/3 以上是学科教师岗 → 教培
_EDU_HARD_NEG = ("大学", "学院", "附中", "附小", "研究所", "研究院",
                 "工程中心", "设计中心", "师范大学", "少年宫", "图书馆")
# 「XX学校」既可能是公办也可能民办，但都不是教培机构：「诸暨荣怀学校」
# 「上海长宁区乐读线上教育培训学校」——后者带「培训」二字才判教培。
_EDU_SCHOOL = ("学校", "外国语学校", "实验小学", "附属")
_EDU_POS = ("教育科技", "教育培训", "培训学校", "培训中心", "培训有限",
            "学而思", "新东方", "高途", "作业帮", "猿辅导", "好未来",
            "竞赛教练", "培优", "网校", "在线教育")
# 岗位层面：主营是学科培训 = 教培的典型特征。
# 注意只认「中小学学科教师」，不认「讲师/教练」以外的泛教师词，
# 也不认「算法工程师」「教育管理」这类非培训岗——那是有正常业务的公司。
_TEACH_JOB = ("中小学教师", "中学教师", "小学教师", "高中教师", "初中教师",
              "学科教师", "语文教师", "数学教师", "英语教师", "物理教师",
              "化学教师", "生物教师", "幼儿教师", "幼师",
              "学科主讲", "主讲教师", "主讲老师", "辅导老师",
              "竞赛教练", "奥数", "教研老师",
              # 岗位名常写成「数学老师(小初高)」「小初高理科老师」「学科老师」
              "老师", "小初高", "高中理科", "初高中")


def _is_teach_title(t: str) -> bool:
    """岗位名是否属于中小学学科教学岗。要先剥掉括号补充说明，
    避免「研发工程师(java、Android)」因为括号里的字被误判。"""
    core = re.sub(r"[（(][^）)]*[)）]", "", t)
    if any(k in core for k in ("研发工程师", "算法工程师", "开发工程师",
                               "人力资源", "市场营销", "销售", "视频剪辑",
                               "商务", "测试", "产品经理", "运营")):
        return False
    return any(k in t or k in core for k in _TEACH_JOB)


def is_training_biz(company: dict) -> bool:
    """是否「教培类（主营业务为学科培训、不要求专业对口）」企业。

    判据以**岗位内容**为主、名称为辅。原因是工商登记的行业经常与实际经营不符：
    库里有「北京育达东方软件科技」（登记 IT，实际只招高中教师）、
    「上海新舟投资管理」（登记金融，只招小学教师和竞赛教练）、
    「苏州萘思尔科技」（登记 IT，只招学科教师）——本质上就是教培机构。

    但反过来要克制：海亮教育虽然名字带教育，也招竞赛教练，同时招算法工程师
    和教育管理精英，是有正常业务的公司，不该因为「不接受教培」就被删掉。
    所以只认「**岗位几乎全部是中小学学科教师**」这一条硬判据。
    """
    name = company.get("name") or ""

    # 高校 / 附中 / 研究院 / 事业单位性质的机构，任何情况都不算教培
    if any(k in name for k in _EDU_HARD_NEG):
        return False

    # 名称直接带「培训」→ 教培。这条必须排在「学校」判断之前：
    # 「深圳市蓝天教育培训学校」「乐读线上教育培训学校」名字里有「学校」，
    # 但本质是培训机构。
    if "培训" in name:
        return True

    # 名称就是学校（含民办）→ 按学校对待
    if any(k in name for k in _EDU_SCHOOL):
        return False

    jobs = company.get("jobs") or []
    titles = [(j.get("title") or "") for j in jobs if isinstance(j, dict)]
    if not titles:
        return any(k in name for k in ("学而思", "新东方", "高途", "作业帮",
                                       "猿辅导", "好未来"))

    teach = sum(1 for t in titles if _is_teach_title(t))
    non_teach = len(titles) - teach

    # 全部岗位都是中小学学科教学岗 → 教培
    if non_teach == 0:
        return True
    # 知名教培品牌 → 教培
    if any(k in name for k in ("学而思", "新东方", "高途", "作业帮", "猿辅导", "好未来")):
        return True
    # 行业标为教育、且教学岗占多数（教研/运营/分校骨干这类配套岗不算主业）→ 教培
    if (company.get("industry") or "") == "教育" and teach * 2 >= len(titles):
        return True
    # 其余（含海亮/未来校这类有真实技术岗的教育公司）保留
    return False


def build_conditions(raw_profile: dict, companies: list) -> dict:
    """从画像里取出硬约束 + 教培接受度，返回可执行的过滤条件。

    返回 {"hard": <结构化条件>, "drop_training": bool, "note": str}
    """
    ans = (raw_profile or {}).get("answers") or {}
    text = (ans.get("hard_filter") or "").strip()
    accept_no_major = ans.get("accept_no_major") is True

    cities = sorted({(c.get("city") or "").strip() for c in companies if c.get("city")})
    inds = sorted({(c.get("industry") or "").strip() for c in companies if c.get("industry")})
    natures = sorted({(c.get("nature") or "").strip() for c in companies if c.get("nature")})

    hard = parse(text, cities, inds, natures) if text else {
        "cities": [], "industries": [], "exclude_natures": [], "keywords": [],
        "note": "未设限", "mode": "none"}
    return {"hard": hard, "drop_training": not accept_no_major,
            "raw_text": text, "accept_no_major": accept_no_major}
