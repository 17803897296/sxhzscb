# -*- coding: utf-8 -*-
"""
采集端 · 岗位推荐：专业 + 实习/简历 -> 几个对口的具体岗位名

对应 README 3.1.1 提议 1：「学生说不清想要什么岗位时，依据专业、简历信息
或口述的实习经历给出『推荐岗位』候选，由学生勾选若干。」

边界（这次别再搞反）：
  - 输出是**具体岗位名**（供应链计划 / 数据分析师），不是岗位族、不是分类 ID。
  - 这是**给学生的建议**，允许模型开放生成；学生勾选即人工确认。
  - 结果只写进用户画像的 intended_jobs[]，**绝不写进企业事实字段**，
    也不生成「某企业招什么岗位」的断言。第 12 节①守在企业侧。
  - 采集阶段可以联网；红线是「演示/匹配那一刻必须能离网跑」，
    匹配端读已落盘的画像文件，不调用本模块。
  - 没配 key / 断网 / 模型失败 -> 规则兜底，页面照样能演示。

用法：
    python -X utf8 job_recommend.py --demo
    python -X utf8 job_recommend.py --major "管理科学与工程" \
        --intern "某电商平台供应链实习：用 SQL 做库存周转分析"
    python -X utf8 job_recommend.py --serve --port 8100
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# 专业 -> 校招基线候选。**所有专业统一口径**：只写校园招聘公告里应届可投的
# 具体岗位名（管培生 / 培训生 / 专员 / 分析师 / 助理 / 校招工程师 / 顾问），
# 不写「运营优化」「系统方案」「流程改善」这类岗位族或职能描述。
# 池子留大一点，供采集端「换一批」轮换。依据：2027 届校招公告常见职位名。
MAJOR_BASE = {
    "管理科学与工程": [
        "管培生（运营/综合方向）", "供应链管理类管培生", "项目管理类管培生",
        "数据分析岗", "商业分析岗", "经营分析岗", "供应链计划专员",
        "需求计划专员", "采购管培生", "数据智能培训生", "BI/数据分析培训生",
        "咨询分析师", "风控/合规专员", "ERP实施顾问",
    ],
    "运营与供应链管理": [
        "供应链管培生", "供应链计划专员", "需求计划专员", "采购管培生",
        "物流运营专员", "库存计划专员", "生产计划专员", "运营管理培训生",
        "供应商管理专员", "仓储管理专员", "物控专员", "供应链数据分析岗",
    ],
    "大数据分析与决策": [
        "数据分析岗", "数据分析培训生", "BI分析师", "商业分析岗",
        "数据开发工程师（校招）", "数据运营专员", "策略分析岗",
        "数据产品经理（校招）", "算法工程师（校招）", "经营分析岗",
        "数据治理专员", "用户研究分析师",
    ],
    "金融服务与金融工程": [
        "行业研究助理", "量化研究岗", "风险管理培训生", "投行承做岗（校招）",
        "金融数据分析岗", "管培生（金融方向）", "金融市场岗", "合规专员",
        "财富顾问培训生", "信用分析岗", "交易员助理", "金融科技培训生",
    ],
    "复杂工程与智能管理": [
        "项目管理培训生", "工程咨询助理", "数据分析岗", "流程改善专员",
        "系统方案工程师（校招）", "项目经理助理", "造价/合约专员",
        "工业工程师", "质量工程师", "智能制造培训生",
        "BIM工程师（校招）", "供应链计划专员",
    ],
    "智能系统管理": [
        "产品经理（校招）", "数据分析岗", "算法产品经理（校招）",
        "系统运营专员", "决策支持分析岗", "产品运营培训生", "信息管理专员",
        "ERP实施顾问", "数据产品经理（校招）", "用户体验研究员（校招）",
        "BI分析师", "咨询分析师",
    ],
    "工业工程": [
        "工业工程师", "生产计划专员", "工艺改善工程师", "质量工程师",
        "运营分析岗", "供应链管培生", "精益改善专员", "制造管培生",
        "设备工程师（校招）", "IE工程师", "物控专员", "项目管理培训生",
    ],
    "计算机": [
        "后端开发工程师（校招）", "算法工程师（校招）", "数据分析岗",
        "测试开发工程师（校招）", "前端开发工程师（校招）",
        "客户端开发工程师（校招）", "运维开发工程师（校招）",
        "安全工程师（校招）", "数据开发工程师（校招）",
        "AI工程师（校招）", "软件开发工程师（校招）", "技术支持工程师（校招）",
    ],
    "软件工程": [
        "软件开发工程师（校招）", "后端开发工程师（校招）", "算法工程师（校招）",
        "测试开发工程师（校招）", "前端开发工程师（校招）",
        "移动端开发工程师（校招）", "DevOps工程师（校招）",
        "数据开发工程师（校招）", "质量工程师（校招）", "产品工程师（校招）",
        "嵌入式软件工程师（校招）", "技术支持工程师（校招）",
    ],
    "金融": [
        "行业研究助理", "量化研究岗", "财富顾问培训生", "风险管理培训生",
        "投行承做岗（校招）", "管培生（金融方向）", "客户经理培训生",
        "金融数据分析岗", "信贷管理专员", "合规专员",
        "运营培训生（银行方向）", "金融市场岗",
    ],
}

# 实习/技能关键词 -> 加权岗位（命中越靠前越优先），同样只用校招具体岗位名。
SIGNAL_RULES = [
    (["供应链", "库存", "补货", "采购", "物流", "仓储", "周转", "供应商"],
     ["供应链计划专员", "需求计划专员", "采购管培生", "物流运营专员"],
     "实习里做过供应链/库存相关的实际工作"),
    (["SQL", "Python", "数据", "分析", "预测", "报表", "指标", "建模", "取数"],
     ["数据分析岗", "商业分析岗", "BI分析师", "数据运营专员"],
     "实习里用到取数、分析或预测"),
    (["产品", "需求文档", "原型", "用户", "PRD", "竞品"],
     ["产品经理（校招）", "产品运营培训生", "商业分析岗"],
     "实习里接触过产品或需求工作"),
    (["项目", "协调", "排期", "交付", "流程", "推进"],
     ["项目管理培训生", "流程改善专员", "项目经理助理"],
     "实习里有项目协调或流程推进经历"),
    (["销售", "客户", "商务", "BD", "渠道", "市场"],
     ["客户经理培训生", "市场分析岗", "商务专员（校招）"],
     "实习里接触过客户或市场"),
    (["财务", "会计", "审计", "预算", "成本"],
     ["财务分析岗", "成本分析专员", "内审/风控专员"],
     "实习里接触过财务或成本"),
]

LLM_PROMPT = """你是双选会会前规划助手，帮学生想清楚「今年校招可以投哪些岗位」。

学生专业：{major}
实习 / 经历：{experiences}
技能：{skills}
这一批需要避开的岗位：{exclude}

请给出 5-6 个**校园招聘公告里真实会写的具体岗位名称**，按该专业校招主流程度排序。要求：
1. 必须是「应届可投的校招岗位」：优先「管培生 / 培训生 / 专员 / 分析师 / 校招工程师 / 助理」这类叫法；
   不要输出「运营优化」「系统方案」「流程改善」这类岗位族或职能描述，也不要写成社招资深岗。
2. 请按**学生自己的专业**挑该方向的主流校招入口，不要套用某个固定专业的清单。
   参考：管科/工业工程 → 各类管培生、数据分析与商业分析、供应链计划与采购、项目与质量工程师；
   计算机/软件 → 前后端、算法、测试开发、数据开发等校招工程师；
   金融/金融工程 → 行业研究、量化研究、风控、投行承做、财富顾问与客户经理培训生；
   数据/智能系统方向 → BI分析师、数据产品、策略与经营分析、产品运营培训生。
   请结合专业与实习经历挑选，**不要只围绕某一段实习现编一个岗位名**。
3. 依据只能来自上面给出的专业 / 实习 / 技能，不要编造经历，不要提到任何企业名称或招聘信息。
4. 尽量避开「需要避开的岗位」里列出的岗位，给出与上一批不同的选择。
5. 严格输出 JSON：{{"candidates":[{{"title":"岗位名","why":"依据"}}]}}
"""


def rule_recommend(major: str, experiences: list, skills: list, exclude=None) -> dict:
    """离线兜底：专业给基线，实习关键词加权。不编造、每条可解释。

    exclude：采集端「换一批」已经展示过的岗位，本轮尽量不重复。
    """
    excl = set(exclude or [])
    text = " ".join(list(experiences) + list(skills))
    base = list(MAJOR_BASE.get(major, []))
    if not base:
        for k, v in MAJOR_BASE.items():
            if k in major or major in k:
                base = list(v)
                break
    if not base:
        base = ["数据分析师", "运营优化", "商业分析", "项目管理"]

    boosted = []
    for kws, jobs, why in SIGNAL_RULES:
        if any(k.lower() in text.lower() for k in kws):
            boosted.extend((j, why) for j in jobs)

    out, seen = [], set()
    tail = [(j, f"与专业「{major}」的培养方向对口") for j in base]
    pool = boosted + tail
    # 先避开已经展示过的岗位；实在凑不满 6 个再放宽，保证「换一批」永远有内容。
    for relax in (False, True):
        for title, why in pool:
            if not relax and title in excl:
                continue
            if title in seen:
                continue
            seen.add(title)
            out.append({"title": title, "why": why})
            if len(out) >= 6:
                break
        if len(out) >= 6:
            break
    return {"ok": True, "mode": "rules", "candidates": out,
            "notice": "模型这次没返回（可能限流或网络问题），已用本地规则兜底；稍后重试可走模型"}


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
    """兼容各家模型：可能返回裸 JSON，也可能包在 ```json 代码块里。"""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def llm_recommend(major, experiences, skills, key, base, model, exclude=None):
    """采集阶段在线调用；失败一律回退规则，不把错误抛给学生。"""
    prompt = LLM_PROMPT.format(
        major=major or "未填写",
        experiences="；".join(experiences) or "无",
        skills="、".join(skills) or "无",
        exclude="、".join(exclude or []) or "无")
    # 不传 response_format：智谱 GLM-4-flash 等模型不支持该参数
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read().decode("utf-8"))
        data = _parse_json(raw["choices"][0]["message"]["content"])
        cands = [c for c in data.get("candidates", [])
                 if isinstance(c, dict) and c.get("title")][:6]
        if not cands:
            return None
        return {"ok": True, "mode": "llm", "model": model, "candidates": cands,
                "notice": "模型生成，仅供参考；请勾选你真正想投的岗位"}
    except Exception as e:
        print("[job_recommend] 模型调用失败，回退规则：%s" % e, file=sys.stderr)
        return None


def recommend(major, experiences, skills, use_llm=True, exclude=None):
    cfg = _load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"
    if key and use_llm:
        got = llm_recommend(major, experiences, skills, key, base, model, exclude)
        if got:
            return got
    return rule_recommend(major, experiences, skills, exclude)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.startswith("/api/fair/recommend-jobs"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        out = json.dumps(recommend(body.get("major", ""), body.get("experiences", []),
                                   body.get("skills", []), body.get("use_llm", True),
                                   body.get("exclude") or []),
                         ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--major", default="管理科学与工程")
    ap.add_argument("--intern", action="append", default=[])
    ap.add_argument("--skill", action="append", default=[])
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
    if a.demo or not intern:
        intern = ["某电商平台供应链实习：用 SQL 做库存周转分析，把补货周期从 14 天压到 9 天",
                  "校级科研项目：用 Python 处理十万级订单数据，产出需求预测模型"]
    skills = a.skill or ["SQL 取数", "Python 数据处理"]

    r = recommend(a.major, intern, skills, use_llm=not a.no_llm)
    print("专业：%s" % a.major)
    print("实习：%s" % "；".join(intern))
    print("模式：%s（%s）" % (r["mode"], r["notice"]))
    print("\n推荐岗位（学生勾选，不写进企业事实字段）：")
    for i, c in enumerate(r["candidates"], 1):
        print("  %d. %-18s %s" % (i, c["title"], c.get("why", "")))


if __name__ == "__main__":
    main()
