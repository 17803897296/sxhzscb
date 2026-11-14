# -*- coding: utf-8 -*-
"""
匹配端 · 大模型直接判断岗位对口与推断（不用岗位族词表）

按用户要求：忽略原文档里「不许编 / 必须离线 / 契约锁定」等硬约束，先实现功能。
模型同时做两件事：
  1. 有 JD：从真实岗位标题里挑和学生意向对口的岗位，给理由；
  2. 无 JD：按行业 / 企业名**推断**可能招什么岗位，明确标 inferred，并生成现场核实问句。

输出可直接喂给页面：排序、命中项、没查到什么、现场问什么。
结果落盘 data/match_llm.json；--serve 提供 POST /api/fair/llm-match。

用法：
    python -X utf8 llm_match.py --limit 12        # 小样本验证
    python -X utf8 llm_match.py --all            # 全量 118 家
    python -X utf8 llm_match.py --serve --port 8101
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

DEFAULT_PROFILE = {
    "major": "管理科学与工程",
    "experiences": ["某电商平台供应链实习：用 SQL 做库存周转分析，把补货周期从 14 天压到 9 天",
                    "校级科研项目：用 Python 处理十万级订单数据，产出需求预测模型"],
    "skills": ["SQL 取数", "Python 数据处理"],
    "intended_jobs": ["供应链计划", "数据分析师", "商业分析"],
    "region": "南京",
    "home_city": "南京",
    "weights": {"城市条件": 25, "离家近": 15, "企业类型": 20,
                "行业": 20, "行业地位": 20},
}

PROMPT = """你是双选会会前规划助手。请判断「这位学生和每家企业是否岗位对口」，并给出行动顺序建议。

学生想投的岗位：{intended_jobs}
期望城市：{region}；家乡：{home_city}

企业列表（每行一家，字段用 | 分隔）：
{companies}
格式：序号 | 企业名 | 行业 | 性质 | 城市 | 已查到的岗位标题（逗号分隔；「无岗位记录」表示网上没查到）

判定岗位对口的标准只有一条：
   - 企业岗位的名称是意向岗位的同义说法（另一种叫法），例如「数据分析专员」就是「数据分析师」的另一种说法；
   - 或者企业岗位干的活（日常工作职责）和意向岗位差不多，例如「经营分析」干的活和「商业分析」相近。
   满足上面任意一条就算对口。字面不同但含义相同也算；职责相近也算。
   ⚠️ 不要根据学生「专业学过哪些课程」「具备哪些能力」来推断对口——那是另一回事，不算岗位对口的依据。

⚠️ 判定纪律（既要防乱判，也别漏掉真正对口的）：
   先把企业岗位归到「职能类」，再看是否和意向岗位同职能类：
        - 供应链类：含 供应链 / 物流 / 采购 / 寻源 / 计划 / 仓储 / 关务；
        - 数据类：含 数据 / 分析 / 商业分析 / 算法 / 量化；
        - 财务类：含 会计 / 财务 / 审计 / 税务 / 核算；
        - 运营类：含 运营 / 经营管理 / 电商运营；
        - 战略类：含 战略（战略管培生；注意「策略分析师」属数据类，不是战略类）。
   规则：
   1. 同职能类内可以互为对口，应当判 hit，不要漏。例如：
        - 「供应链事业群管培生-供应链运营方向」「供应链主管」「采购工程师」「物流专员」「计划管培生」都算「供应链管培生 / 供应链计划专员 / 采购寻源专员」的对口；
        - 「数据分析师」「商业分析」「经营分析」「量化研究员」都算「数据分析岗」的对口。
   2. 跨职能类一律 miss，即使共享「管培生 / 专员 / 工程师」后缀也不算同类，例如：
        - 会计 / 会计信息化管培生 ≠ 供应链计划专员（财务 ≠ 供应链）；
        - 会计 ≠ 数据分析岗（财务 ≠ 数据分析）；
        - 战略管培生 ≠ 供应链管培生（职能方向不同，仅共享「管培生」后缀不算同类；但「供应链方向管培生」是同类，算对口）；
        - 数据库产品工程师 ≠ 数据分析岗（产品 / 工程 ≠ 分析）；
        - 前端 / 后端 / 嵌入式 / 算法研发工程师 ≠ 数据分析岗（研发 ≠ 分析），除非岗位名明确含「数据分析」；
        - 销售 / 市场 / 人事 / 行政 类岗位 ≠ 上述任何意向（除非意向本身就是该类）。
   3. 「另一种说法」只用于真正同岗异名（如「供应链管培生」=「供应链管理培训生」=「供应链方向管培生」）；不要把不同职能的岗位说成另一种说法（如把会计说成供应链的另一种说法）。
   4. 跨类职能即便有表面相似（都坐办公室 / 都要细心 / 都用电脑 / 都是管培生轮岗），也不算「干的活差不多」。
   5. 若企业岗位一个都不对口，state 必须是 "miss"、matched_jobs 为空数组 []；但同职能类内只要有岗位就应当判 hit，不要漏掉。

请为每家企业输出一个对象，字段：
  "i": 序号（必须和输入一致）
  "state": "hit"（有 JD 且岗位对口）| "inferred"（无 JD，按行业推断可能有对口岗位）| "miss"（明确不对口）
  "matched_jobs": 具体岗位名数组（有 JD 时只能从给出的岗位标题里选；推断时写推断出的岗位名）
  "reason": 一句话说明为什么对口 / 为什么不对口，给学生看的依据。只写岗位名关系，分两种：
   - 若企业岗位名是意向岗位的同义说法，写「「XX」即「YY」的另一种说法」；
   - 若**企业岗位名字面差距较大、但干的活（职责）差不多**，必须明确写「大模型判断二者类似工作内容：「XX」干的活与「YY」相近（具体：……）」，让学生明白名字不同也对口；
   不要写「学生专业为 XX」「具备 XX 能力」这类按专业/能力推断的话。
  "gaps": 还没查到的信息数组（如薪资、双休、具体职责、是否真的招这个岗位）
  "verify_question": 现场问 HR 的一句话（只问最影响决策的那一件）
  "priority": 1-5 整数，5 最值得先去（综合对口度、岗位明确程度、城市）

规则：
1. 有 JD 的企业：只能从给出的岗位标题里选 matched_jobs，不要发明岗位名；一个都不对口就 state=miss。
2. 无岗位记录的企业：**必须**按行业和企业名推断可能招什么岗位，state 必须是 "inferred"，
   不允许判成 "miss"（只有行业为「未知」时才允许 miss）；
   reason 必须写明「网上未查到岗位，按行业推断」；verify_question 要问「是不是招 XX 岗位」。
   推断出的岗位名写进 matched_jobs，并在 gaps 里注明「岗位为推断，需现场确认」。
3. 不要编造薪资、人数、城市之外的企业事实；不知道的写进 gaps。
4. 严格输出 JSON 数组，不要任何解释文字、不要 markdown 标题。
"""


def load_config():
    cfg = {}
    path = os.path.join(HERE, ".env")
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return {
        "key": os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", ""),
        "base": (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
                 or "https://api.deepseek.com").rstrip("/"),
        "model": os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat",
    }


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    for ch, idx in (("[", text.find("[")), ("{", text.find("{"))):
        if idx > 0 and (ch == "[" or text.find("[") < 0):
            text = text[idx:]
            break
    return json.loads(text)


def _tag_reason(r):
    """确保「名字差距大、但干的活差不多」这类对口判定，reason 以
    「大模型判断二者类似工作内容：」开头——这是告诉学生「这是模型判定、不是字面同名」的关键。
    同义（即另一种说法）类、未查到岗位的推断类、不对口类都不动；已带前缀的也不重复加。"""
    if not r:
        return r
    if r.startswith(("大模型判断", "大模型判定")):
        return r
    if any(k in r for k in ("干的活", "职责", "相近", "类似工作")):
        return "大模型判断二者类似工作内容：" + r
    return r


# ---- 代码级职能兜底：修正模型跨职能误判（假阳性）与漏判（假阴性）----
# glm-4-flash 偶尔会把「供应链事业群管培生」判成不对口、把「会计」判成供应链对口，
# 这类明确错误不能依赖模型，用确定性职能分类兜底。
_FIN_KW = ("会计", "财务", "审计", "税务", "核算", "出纳")
_STRAT_KW = ("战略",)
_DB_KW = ("数据库",)
_SUPPLY_KW = ("供应链", "物流", "采购", "寻源", "仓储", "关务", "计划")
_OPS_KW = ("运营", "经营管理", "电商运营")
_DATA_KW = ("数据分析", "商业分析", "量化", "分析", "数据")
# 这些职能类：只要学生意向里没有该类，企业岗位就算「误判」必须剔除。
_DROP_IF_NOT_INTENDED = ("fin", "strat", "db", "eng", "sales", "hr", "admin")


def _func_terms(title):
    """岗位名命中的职能标记集合（确定性，不依赖模型）。"""
    t = title or ""
    tags = set()
    if any(k in t for k in _FIN_KW):
        tags.add("fin")
    if any(k in t for k in _STRAT_KW):
        tags.add("strat")
    if any(k in t for k in _DB_KW):
        tags.add("db")
    if any(k in t for k in _SUPPLY_KW):
        tags.add("supply")
    if any(k in t for k in _OPS_KW):
        tags.add("ops")
    if any(k in t for k in _DATA_KW):
        tags.add("data")
    if any(k in t for k in ("工程师", "研发", "开发", "嵌入式", "前端", "后端", "测试", "算法")):
        if "data" not in tags:
            tags.add("eng")
    if any(k in t for k in ("销售", "市场", "招商", "商务")):
        tags.add("sales")
    if any(k in t for k in ("人事", "人力", "招聘")):
        tags.add("hr")
    if any(k in t for k in ("行政", "文秘")):
        tags.add("admin")
    return tags


def _class_label(tags):
    for k, lab in (("supply", "供应链"), ("data", "数据"), ("ops", "运营"),
                   ("fin", "财务"), ("strat", "战略"), ("db", "数据库")):
        if k in tags:
            return lab
    return "相关"


def guard_matches(matched_jobs, intents, company_jobs):
    """返回修正后的 matched_jobs（去重保序），并给出是否应翻成 hit。

    - 剔除跨职能误判：财务 / 战略 / 数据库 / 研发 / 销售 / 人事 / 行政 类岗位，
      只要学生意向不含该类，一律剔除（例如会计 ≠ 供应链、战略管培生 ≠ 供应链管培生、
      数据库产品工程师 ≠ 数据分析岗、算法研发工程师 ≠ 数据分析岗）；
    - 补回漏判：企业真实岗位名明确含某职能词、且该词也出现在学生意向里（同职能类），
      模型却漏掉的，确定性补回，避免漏掉「供应链事业群管培生」这类明确对口岗。
    """
    intent_tags = set()
    for it in (intents or []):
        intent_tags |= _func_terms(it)
    titles = []
    for j in (company_jobs or []):
        t = j.get("title") if isinstance(j, dict) else j
        if t:
            titles.append(t)

    kept, seen = [], set()
    for mj in (matched_jobs or []):
        mj = (mj or "").strip()
        if not mj or mj in seen:
            continue
        tags = _func_terms(mj)
        if any(tag in tags and tag not in intent_tags for tag in _DROP_IF_NOT_INTENDED):
            continue
        kept.append(mj)
        seen.add(mj)

    for t in titles:
        if t in seen:
            continue
        tags = _func_terms(t)
        if tags & intent_tags and not (tags & set(_DROP_IF_NOT_INTENDED)):
            kept.append(t)
            seen.add(t)
    return kept


def _rescue_reason(matched, intents):
    ints = list(intents or [])
    parts = []
    for mj in matched[:4]:
        mtags = _func_terms(mj)
        best = next((it for it in ints if (mtags & _func_terms(it))), ints[0] if ints else "")
        lab = _class_label(mtags)
        if best:
            parts.append("「%s」与「%s」同属%s类岗位，干的活相近" % (mj, best, lab))
        else:
            parts.append("「%s」与意向岗位同属%s类" % (mj, lab))
    return "大模型判断二者类似工作内容：" + "；".join(parts) if parts else ""


def company_line(c, i):
    jobs = [j.get("title", "") for j in (c.get("jobs") or []) if j.get("title")]
    seen, uniq = set(), []
    for t in jobs:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    jt = "，".join(uniq[:12]) if uniq else "无岗位记录"
    return "%d | %s | %s | %s | %s | %s" % (
        i, c.get("name", ""), c.get("industry", "未知"),
        c.get("nature", "未知"), c.get("city", "未知"), jt)


def call_model(prompt, cfg, timeout=60):
    payload = {"model": cfg["model"],
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.1}
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["key"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return parse_json(raw["choices"][0]["message"]["content"])


INFER_PROMPT = """这些企业网上没有查到岗位记录，请按「行业 + 企业名」推断它们今年可能招什么岗位，
供学生现场问一句确认。

学生想投的岗位：{intended_jobs}

企业列表（序号 | 企业名 | 行业 | 城市）：
{companies}

要求：
 判断只基于行业 / 企业性质与岗位名的对应关系，不要按学生专业学过哪些课程来推断。
1. 每家**必须**给出 1-3 个可能的具体岗位名（像招聘启事里写的那种），不要写「无」「无法判断」。
2. reason 写明「网上未查到岗位，按行业 X 推断」；verify_question 写成「请问今年是不是招 XX 岗位？」。
3. gaps 里写明「岗位为推断，需现场确认」。
4. 严格输出 JSON 数组：[{{"i": 序号, "matched_jobs": [...], "reason": "...", "verify_question": "...", "gaps": [...]}}]
"""


def infer_missing(profile, rows, cfg, workers=5, verbose=True):
    """模型第一批没给推断的无 JD 企业，再单独让模型按行业推断一次。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = [r for r in rows if not r.get("has_jd") and r["state"] == "miss"
               and r.get("industry") not in ("未知", "", None)]
    if not targets:
        return 0
    by_name = {}

    def one(start):
        chunk = targets[start:start + 8]
        lines = "\n".join("%d | %s | %s | %s" % (i, c["name"], c["industry"], c["city"])
                          for i, c in enumerate(chunk, 1))
        prompt = INFER_PROMPT.format(
            intended_jobs="、".join(profile.get("intended_jobs", [])),
            companies=lines)
        try:
            out = call_model(prompt, cfg, timeout=90)
            if isinstance(out, dict):
                out = out.get("results") or out.get("rows") or []
            got = {}
            for item in out:
                idx = int(item.get("i", 0)) - 1
                if 0 <= idx < len(chunk):
                    got[chunk[idx]["name"]] = item
            return got
        except Exception as e:
            if verbose:
                print("  推断批次失败：%s" % str(e)[:100], file=sys.stderr)
            return {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s) for s in range(0, len(targets), 8)]
        for f in as_completed(futs):
            by_name.update(f.result())

    n = 0
    for r in targets:
        item = by_name.get(r["name"])
        if not item or not item.get("matched_jobs"):
            continue
        r["state"] = "inferred"
        r["matched_jobs"] = item["matched_jobs"][:3]
        r["reason"] = item.get("reason") or ("网上未查到岗位，按行业「%s」推断" % r["industry"])
        r["verify_question"] = item.get("verify_question") or (
            "请问今年是不是招 %s 岗位？" % "、".join(r["matched_jobs"][:2]))
        r["gaps"] = list(dict.fromkeys(list(r.get("gaps") or []) + ["岗位为推断，需现场确认"]))
        r["priority"] = max(r.get("priority", 3), 3)
        n += 1
    if verbose:
        print("  补推断 %d / %d 家" % (n, len(targets)), flush=True)
    return n


def run(profile, companies, cfg, batch=8, verbose=True, workers=4):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def one_batch(start):
        chunk = companies[start:start + batch]
        lines = "\n".join(company_line(c, i) for i, c in enumerate(chunk, start=1))
        prompt = PROMPT.format(
            intended_jobs="、".join(profile.get("intended_jobs", [])),
            region=profile.get("region", ""), home_city=profile.get("home_city", ""),
            companies=lines)
        try:
            out = call_model(prompt, cfg, timeout=90)
            if isinstance(out, dict):
                out = out.get("results") or out.get("rows") or []
            got = []
            for item in out:
                idx = int(item.get("i", 0)) - 1
                if not (0 <= idx < len(chunk)):
                    continue
                c = chunk[idx]
                row = {
                    "name": c.get("name", ""),
                    "industry": c.get("industry", "未知"),
                    "city": c.get("city", "未知"),
                    "state": item.get("state", "miss"),
                    "matched_jobs": item.get("matched_jobs", []) or [],
                    "reason": _tag_reason(item.get("reason", "")),
                    "gaps": item.get("gaps", []) or [],
                    "verify_question": item.get("verify_question", ""),
                    "priority": int(item.get("priority", 3) or 3),
                    "has_jd": bool(c.get("jobs")),
                }
                # 代码级职能兜底：剔除跨职能误判 + 补回漏判的明确同类岗
                new_matched = guard_matches(
                    row["matched_jobs"], profile.get("intended_jobs", []),
                    c.get("jobs") or [])
                if new_matched != row["matched_jobs"]:
                    row["matched_jobs"] = new_matched
                    if new_matched:
                        row["state"] = "hit"
                        row["reason"] = _rescue_reason(new_matched, profile.get("intended_jobs", []))
                    elif row["state"] in ("hit", "inferred"):
                        row["state"] = "miss"
                got.append(row)
            return start, got, None
        except Exception as e:
            return start, [], {"batch": start // batch + 1, "error": str(e)[:160]}

    starts = list(range(0, len(companies), batch))
    collected = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one_batch, s): s for s in starts}
        for f in as_completed(futs):
            s, got, err = f.result()
            collected[s] = (got, err)
            if verbose:
                print("  完成 %d/%d 家" % (min(s + batch, len(companies)), len(companies)), flush=True)

    rows, errors = [], []
    for s in starts:
        got, err = collected.get(s, ([], None))
        rows.extend(got)
        if err:
            errors.append(err)
            if verbose:
                print("  批次 %d 失败：%s" % (s // batch + 1, err["error"]), file=sys.stderr)

    # 模型偶尔不遵守 1-5 的范围（实测出现 8），且会把 miss 打到高优先级；
    # 先钳位，再按「明确命中 > 推断 > 不对口」分组，组内按 priority 降序。
    for r in rows:
        try:
            r["priority"] = max(1, min(5, int(r["priority"])))
        except (TypeError, ValueError):
            r["priority"] = 3
    order = {"hit": 0, "inferred": 1, "miss": 2}
    rows.sort(key=lambda r: (order.get(r["state"], 3), -r["priority"], r["name"]))
    inferred_added = infer_missing(profile, rows, cfg, workers=workers, verbose=verbose)
    rows.sort(key=lambda r: (order.get(r["state"], 3), -r["priority"], r["name"]))
    stats = {"total": len(rows),
             "hit": sum(1 for r in rows if r["state"] == "hit"),
             "inferred": sum(1 for r in rows if r["state"] == "inferred"),
             "miss": sum(1 for r in rows if r["state"] == "miss"),
             "inferred_added": inferred_added,
             "errors": len(errors)}
    return {"ok": True, "mode": "llm", "model": cfg["model"],
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "profile": profile, "stats": stats, "errors": errors, "rows": rows}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.startswith("/api/fair/llm-match"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        cfg = load_config()
        companies = body.get("companies")
        if companies is None:
            with open(os.path.join(DATA, "companies.json"), encoding="utf-8") as f:
                companies = json.load(f)
        limit = body.get("limit")
        if limit:
            companies = companies[:int(limit)]
        out = run(body.get("profile") or DEFAULT_PROFILE, companies, cfg,
                  batch=int(body.get("batch", 8)), verbose=False,
                  workers=int(body.get("workers", 4)))
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
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(DATA, "match_llm.json"))
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8101)
    a = ap.parse_args()

    cfg = load_config()
    if not cfg["key"]:
        print("没有配置 API key（ent_tools/.env）", file=sys.stderr)
        return 1

    if a.serve:
        print("llm_match 服务 http://127.0.0.1:%d/api/fair/llm-match" % a.port)
        HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
        return 0

    with open(os.path.join(DATA, "companies.json"), encoding="utf-8") as f:
        companies = json.load(f)
    if not a.all:
        # 小样本里故意混入有 JD 和无 JD 的企业，验证推断分支
        withjd = [c for c in companies if c.get("jobs")][:6]
        nojd = [c for c in companies if not c.get("jobs")][:6]
        companies = (withjd + nojd)[:a.limit]

    print("模型 %s @ %s" % (cfg["model"], cfg["base"]))
    print("学生：%s / 意向 %s" % (DEFAULT_PROFILE["major"],
                                 "、".join(DEFAULT_PROFILE["intended_jobs"])))
    print("企业 %d 家，每批 %d 家\n" % (len(companies), a.batch))
    out = run(DEFAULT_PROFILE, companies, cfg, batch=a.batch, workers=a.workers)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\n统计：", json.dumps(out["stats"], ensure_ascii=False))
    print("结果：", a.out)
    print("\n前 8 家：")
    for r in out["rows"][:8]:
        print("  [%s/%s] %-24s 岗位 %s" % (
            r["state"], r["priority"], r["name"][:22],
            "、".join(r["matched_jobs"][:3]) or "—"))
        if r["reason"]:
            print("      %s" % r["reason"][:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
