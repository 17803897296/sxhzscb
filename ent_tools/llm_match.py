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

学生画像：
  专业：{major}
  实习/经历：{experiences}
  技能：{skills}
  想投的岗位：{intended_jobs}
  期望城市：{region}；家乡：{home_city}
  偏好权重：{weights}

企业列表（每行一家，字段用 | 分隔）：
{companies}
格式：序号 | 企业名 | 行业 | 性质 | 城市 | 已查到的岗位标题（逗号分隔；「无岗位记录」表示网上没查到）

请为每家企业输出一个对象，字段：
  "i": 序号（必须和输入一致）
  "state": "hit"（有 JD 且岗位对口）| "inferred"（无 JD，按行业推断可能有对口岗位）| "miss"（明确不对口）
  "matched_jobs": 具体岗位名数组（有 JD 时只能从给出的岗位标题里选；推断时写推断出的岗位名）
  "reason": 一句话说明为什么对口 / 为什么不对口，给学生看的依据
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
               "temperature": 0.2}
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

学生想投的岗位：{intended_jobs}；专业：{major}

企业列表（序号 | 企业名 | 行业 | 城市）：
{companies}

要求：
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
            major=profile.get("major", ""), companies=lines)
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
            major=profile.get("major", ""),
            experiences="；".join(profile.get("experiences", [])),
            skills="、".join(profile.get("skills", [])),
            intended_jobs="、".join(profile.get("intended_jobs", [])),
            region=profile.get("region", ""), home_city=profile.get("home_city", ""),
            weights=json.dumps(profile.get("weights", {}), ensure_ascii=False),
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
                got.append({
                    "name": c.get("name", ""),
                    "industry": c.get("industry", "未知"),
                    "city": c.get("city", "未知"),
                    "state": item.get("state", "miss"),
                    "matched_jobs": item.get("matched_jobs", []) or [],
                    "reason": item.get("reason", ""),
                    "gaps": item.get("gaps", []) or [],
                    "verify_question": item.get("verify_question", ""),
                    "priority": int(item.get("priority", 3) or 3),
                    "has_jd": bool(c.get("jobs")),
                })
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
