# -*- coding: utf-8 -*-
"""
采集端 · 岗位推荐：专业 + 实习/简历 -> 几个对口的校招岗位名

2026-11-13 重构（用户口径）：
  旧做法是「我事先把 2027 届校招公告里的具体岗位名硬编码进 MAJOR_BASE」，
  这等于把某一年的某几份公告当成事实，换一年、换一所学校就过时。
  新做法：
    1. **主路径交给大模型**，问的是「这个专业在普遍的校园招聘里，一般会有哪些岗位」，
       要模型的通用判断，不要它复述某份公告。
    2. **企业库里有真实岗位的优先推荐**：把库里实际存在的岗位名（742 条去重后）
       传给模型做「优先选项」，命中就排在前面，并标注「企业库在招」。
    3. 规则兜底降级为**泛化岗位族名**（不再硬编码年份具体的公告岗位），
       宁可粗一点也不假精确。

边界（不变）：
  - 输出是岗位名（供学生勾选的建议），不写进企业事实字段，不生成「某企业招什么」的断言。
  - 失败一律兜底，不把错误抛给学生；演示时刻可离线。
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

# 规则兜底用的「泛化校招岗位族」。
# 刻意不写「2027届校招公告」里那种精确岗位名——那会随年份过时，
# 也会把某家公司的一次招聘当成行业通例。这里只给专业方向上的通用入口。
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
}

# 实习/技能信号 -> 加权方向（仍是泛化岗位族，不做具体公告名）
SIGNAL_RULES = [
    (["供应链", "库存", "补货", "采购", "物流", "仓储", "周转", "供应商"],
     ["供应链/计划岗", "采购专员", "物流运营岗"],
     "实习里做过供应链/库存相关的实际工作"),
    (["SQL", "Python", "数据", "分析", "预测", "报表", "指标", "建模", "取数"],
     ["数据分析岗", "BI/报表岗", "策略分析岗"],
     "实习里用到取数、分析或预测"),
    (["产品", "需求文档", "原型", "用户", "PRD", "竞品"],
     ["产品经理", "产品运营岗", "商业分析岗"],
     "实习里接触过产品或需求工作"),
    (["项目", "协调", "排期", "交付", "流程", "推进"],
     ["项目管理岗", "流程/精益改善岗"],
     "实习里有项目协调或流程推进经历"),
    (["销售", "客户", "商务", "BD", "渠道", "市场"],
     ["客户经理", "市场/商务岗"],
     "实习里接触过客户或市场"),
    (["财务", "会计", "审计", "预算", "成本"],
     ["财务分析岗", "成本分析岗", "风控/内审岗"],
     "实习里接触过财务或成本"),
]

LLM_PROMPT = """你是双选会会前规划助手，帮学生想清楚「校园招聘里一般可以投哪些岗位」。

学生专业：{major}
实习 / 经历：{experiences}
技能：{skills}
这一批需要避开的岗位：{exclude}

【本次双选会企业库中真实在招的岗位（优先从这里挑，写完在 from_db 标 true）】
{db_jobs}

请给出 5-6 个岗位名称，要求：
1. 请你**基于通用认知**回答「这个专业的应届生在普遍的校园招聘里一般会招哪些岗位」。
   不是复述某一家公司今年的招聘公告，也不要写成年份专属的说法。
2. 岗位名用中国大陆校招通行的写法：「管培生 / 培训生 / 专员 / 分析师 / 助理 / 工程师」。
   不要输出「运营优化」「系统方案」「流程改善」这类职能描述；也不要写社招资深岗（如「高级经理」）。
   **不要给出与该专业明显不对口的岗位**（例：管理类不要推研发/算法工程师，除非学生简历里有硬核编程经历）。
3. **如果「企业库中真实在招的岗位」里有与本专业对口的，务必优先选中**，
   岗位名照抄库里的原文，并在 from_db 里写 true；其余自己生成的写 false。
   这一步很重要：库里有的岗位意味着学生今天在会场真能投。
4. 依据只能来自上面的专业 / 实习 / 技能，不要编造经历，不要提任何企业名称。
5. 尽量避开「需要避开的岗位」，与上一批给出不同选择。
6. 严格输出 JSON：{{"candidates":[{{"title":"岗位名","why":"一句话依据","from_db":true/false}}]}}
"""


def db_job_titles(limit=160):
    """读企业库里真实在招的岗位名（去重），供模型做「优先推荐」候选。

    只读本地库，不联网；库里没有或读不到就返回空列表，不影响主流程。
    """
    try:
        import sqlite3
        db = os.path.join(HERE, "data", "careersafari.db")
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
                if not t or t in seen:
                    continue
                if len(t) > 30:
                    continue
                seen.add(t)
                out.append(t)
        return out[:limit]
    except Exception:
        return []


def rule_recommend(major: str, experiences: list, skills: list, exclude=None,
                   db_jobs=None) -> dict:
    """离线兜底：专业泛化方向 + 实习关键词加权 + 库里真实岗位优先。

    exclude：采集端「换一批」已经展示过的岗位，本轮尽量不重复。
    db_jobs：企业库真实岗位名，命中就提前并标注。
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
        base = ["管培生", "数据分析岗", "项目管理岗", "业务运营岗"]

    boosted = []
    for kws, jobs, why in SIGNAL_RULES:
        if any(k.lower() in text.lower() for k in kws):
            boosted.extend((j, why, False) for j in jobs)

    # 库里真实岗位里，与本专业方向字面相近的，优先推荐（学生今天真能投）。
    db_hits = []
    for dj in (db_jobs or []):
        for kw in base + [b[0] for b in boosted]:
            core = re.sub(r"[（(].*?[)）]", "", kw)
            core = re.sub(r"(岗|专员|工程师|管培生|经理|助理|分析师)$", "", core)
            if core and len(core) >= 2 and core in dj:
                db_hits.append((dj, "企业库中现场在招，与本专业方向对口", True))
                break

    pool = db_hits + boosted + [(j, f"与专业「{major}」的培养方向对口", False) for j in base]
    out, seen = [], set()
    for relax in (False, True):
        for title, why, from_db in pool:
            if not relax and title in excl:
                continue
            if title in seen:
                continue
            seen.add(title)
            out.append({"title": title, "why": why, "from_db": from_db})
            if len(out) >= 6:
                break
        if len(out) >= 6:
            break
    return {"ok": True, "mode": "rules", "candidates": out,
            "notice": "模型这次没返回（可能限流或网络问题），已用通用岗位方向兜底；稍后重试可走模型"}


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


def llm_recommend(major, experiences, skills, key, base, model, exclude=None,
                  db_jobs=None):
    """采集阶段在线调用；失败一律回退规则，不把错误抛给学生。"""
    prompt = LLM_PROMPT.format(
        major=major or "未填写",
        experiences="；".join(experiences) or "无",
        skills="、".join(skills) or "无",
        exclude="、".join(exclude or []) or "无",
        db_jobs="、".join(db_jobs or []) or "（未读到企业库，按通用判断即可）")
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
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = json.loads(r.read().decode("utf-8"))
        data = _parse_json(raw["choices"][0]["message"]["content"])
        cands = []
        for c in data.get("candidates", []):
            if not (isinstance(c, dict) and c.get("title")):
                continue
            cands.append({"title": str(c["title"]),
                          "why": str(c.get("why", "")),
                          "from_db": bool(c.get("from_db"))})
        cands = cands[:6]
        if not cands:
            return None
        return {"ok": True, "mode": "llm", "model": model, "candidates": cands,
                "notice": "模型按通用校招口径生成，标注「企业库在招」的是今天现场真能投的；请勾选你真正想投的"}
    except Exception as e:
        print("[job_recommend] 模型调用失败，回退规则：%s" % e, file=sys.stderr)
        return None


def recommend(major, experiences, skills, use_llm=True, exclude=None):
    cfg = _load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"
    jobs = db_job_titles()
    if key and use_llm:
        got = llm_recommend(major, experiences, skills, key, base, model, exclude, jobs)
        if got:
            return got
    return rule_recommend(major, experiences, skills, exclude, jobs)


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
        flag = "【企业库在招】" if c.get("from_db") else ""
        print("  %d. %-18s %s%s" % (i, c["title"], flag, c.get("why", "")))


if __name__ == "__main__":
    main()
