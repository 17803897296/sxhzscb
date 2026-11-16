# -*- coding: utf-8 -*-
"""校招来源核实 · 建新库（幂等，不联网，只读 careersafari.db）

背景：145 家产品库的 jobSource 标签混用了「采集手段」与「来源类型」，
标签写 websearch/未知 的企业里，82 家其实带着真实校招来源 URL 的岗位证据。
本脚本按 source_url 真实域名重新核实，产出可证明的校招岗位库。

口径（用户 2026-11-16 拍板）：
  1. 校招来源渠道：高校就业网/校园平台、公众号(含企业官方号·招聘号·高校就业号)、
     企业官网/自建门户 —— 三类都算，但要过第 3 条的齐套阈值。
  2. 同一企业多来源的岗位按「企业 + 岗位名」合并去重。
  3. 齐套阈值：校招岗位数 >= MIN_CAMPUS_JOBS(默认 3) 才认作校招来源；
     1-2 个岗位视为「明显不齐 / 零散」，不认校招、降级 websearch。
  4. 岗位只取 job_evidence 里带真实 URL 的证据岗 —— 原 company_jobs.jobs 里
     note 为「根据公司名称…推断/判断」、且无 URL 证据的补全岗一律不进新库，
     等价于「之前大模型推断补全的岗位删掉」。
  5. 真实校招企业不再进 unknown_infer / 按行业推断分支（由判定逻辑侧保证）。

输出：
  data/campus_recheck.db   新库（campus_jobs / campus_company / meta / dropped_infer）
  data/campus_recheck_report.md   每家企业去留 + 待删补全岗对照
"""

from __future__ import annotations

import collections
import json
import os
import sqlite3
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(HERE, "data", "careersafari.db")
OUT_DB = os.path.join(HERE, "data", "campus_recheck.db")
OUT_MD = os.path.join(HERE, "data", "campus_recheck_report.md")
MIN_CAMPUS_JOBS = 3

# 校招渠道域名判定（真实域名，不看 jobSource 标签）
CAMPUS_EDU = (
    "edu.cn", "91wllm.cn", "bjbys.net.cn", "91job.org.cn", "ahbys.com",
    "ncmc.edu.cn", "bys.", "jysd.com", "ncss.cn", "jobszu.", "chsi.com.cn",
    "24365", "job.", "jobs.", "career.", "jy.", "xsjy.", "jyzd.", "jyzx.",
    "91job", "iguopin.com", "ycyg.", "nbhr.org.cn",
)
COMMERCIAL = ("51job.com", "zhaopin.com", "liepin.com", "zhipin.com",
              "lagou.com", "mokahr.com")
BIZPROFILE = ("aiqicha.baidu.com", "qcc.com", "tianyancha.com",
              "qixin.com", "11315.com", "gsxt.gov.cn")
INFER_NOTE = ("推断", "判断", "抽取")


def dom(u):
    try:
        return urlparse(str(u)).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def channel(u):
    d = dom(u)
    if not d:
        return None
    if any(k in d for k in BIZPROFILE):
        return None                      # 工商页不是招聘公告
    if "mp.weixin.qq.com" in d:
        return "公众号"
    if any(k in d for k in COMMERCIAL) and "/campus" not in str(u).lower():
        return None                      # 纯社招平台不算；/campus 专页算
    if any(k in d for k in CAMPUS_EDU):
        return "高校就业网/校园平台"
    return "企业官网/自建门户"            # 企业自己官网/招聘门户，算候选，受阈值约束


def main():
    con = sqlite3.connect(SRC_DB)
    con.row_factory = sqlite3.Row

    # 145 家基础信息
    base = {}
    for r in con.execute("SELECT item_json FROM company_jobs"):
        d = json.loads(r[0])
        base[d["name"]] = {
            "tag": d.get("jobSource") or "未知",
            "jobs": [j.get("title", "") for j in (d.get("jobs") or [])],
            "note": str(d.get("note") or ""),
        }
    # 662 条带 URL 证据：{企业: [岗位, ...]}
    ev = collections.defaultdict(list)
    for r in con.execute("SELECT company,title,source_url FROM job_evidence"):
        ch = channel(r["source_url"])
        if ch:
            ev[r["company"]].append({
                "title": r["title"], "url": r["source_url"], "channel": ch})
    con.close()

    # 逐企业判定
    verdict = []
    campus_jobs = []          # 新库岗位（合并去重，只真实证据）
    dropped_infer = []        # 被删的推断补全岗（无 URL 证据的 jobs）
    for name, b in base.items():
        # 同企业多来源、同岗位名合并：保留首次出现的 URL，记录所有渠道
        by_title = collections.OrderedDict()
        for e in ev.get(name, []):
            t = e["title"]
            if t not in by_title:
                by_title[t] = {"title": t, "url": e["url"],
                               "channels": [e["channel"]]}
            elif e["channel"] not in by_title[t]["channels"]:
                by_title[t]["channels"].append(e["channel"])
        n_campus = len(by_title)
        is_campus = n_campus >= MIN_CAMPUS_JOBS
        channels = sorted({c for v in by_title.values() for c in v["channels"]})
        verdict.append({
            "name": name, "old_tag": b["tag"], "n_campus_jobs": n_campus,
            "is_campus": int(is_campus), "channels": "、".join(channels),
            "decision": "校招来源" if is_campus else "降级websearch",
        })
        if is_campus:
            for v in by_title.values():
                campus_jobs.append({
                    "company": name, "title": v["title"],
                    "source_url": v["url"],
                    "channel": "、".join(v["channels"]),
                })
            # 删掉的推断补全岗：原 jobs 里有、但无真实证据的
            for t in b["jobs"]:
                if t and t not in by_title:
                    dropped_infer.append({"company": name, "title": t})

    # 写新库
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    out = sqlite3.connect(OUT_DB)
    out.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE campus_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT, title TEXT, source_url TEXT, channel TEXT,
        UNIQUE(company,title));
      CREATE TABLE campus_company(
        name TEXT PRIMARY KEY, old_tag TEXT, n_campus_jobs INTEGER,
        is_campus INTEGER, channels TEXT, decision TEXT);
      CREATE TABLE dropped_infer(company TEXT, title TEXT);
      CREATE TABLE meta(key TEXT, value TEXT);
    """)
    out.executemany(
        "INSERT OR IGNORE INTO campus_jobs(company,title,source_url,channel) VALUES(?,?,?,?)",
        [(j["company"], j["title"], j["source_url"], j["channel"]) for j in campus_jobs])
    out.executemany(
        "INSERT INTO campus_company VALUES(?,?,?,?,?,?)",
        [(v["name"], v["old_tag"], v["n_campus_jobs"], v["is_campus"],
          v["channels"], v["decision"]) for v in verdict])
    out.executemany("INSERT INTO dropped_infer VALUES(?,?)",
                    [(d["company"], d["title"]) for d in dropped_infer])
    meta = [
        ("version", "campus_recheck_v1_20261116"),
        ("source_db", "careersafari.db (只读) / 145 家"),
        ("min_campus_jobs", str(MIN_CAMPUS_JOBS)),
        ("rule", "按 source_url 真实域名判校招渠道，同企业多来源岗位合并去重；"
                 "校招岗>=3 才认校招，1-2 岗降级 websearch；"
                 "岗位只取带 URL 的真实证据，无证据的推断补全岗不收录"),
        ("n_company_total", str(len(verdict))),
        ("n_campus_company", str(sum(1 for v in verdict if v["is_campus"]))),
        ("n_campus_jobs", str(len(campus_jobs))),
        ("n_dropped_infer", str(len(dropped_infer))),
    ]
    out.executemany("INSERT INTO meta VALUES(?,?)", meta)
    out.commit()
    out.close()

    # 报告
    lines = [
        "# 校招来源核实报告", "",
        "## 口径", "",
        f"- 校招渠道：高校就业网/校园平台、公众号、企业官网/自建门户（按真实域名判，不看 jobSource 标签）",
        f"- 同一企业多来源岗位按「企业+岗位名」合并去重",
        f"- 齐套阈值：校招岗 ≥{MIN_CAMPUS_JOBS} 才认校招来源；1-2 岗判明显不齐，降级 websearch",
        f"- 岗位只取带 URL 的真实证据；原 jobs 里无证据的推断补全岗删除", "",
        "## 汇总", "",
        f"- 145 家：认校招 **{sum(1 for v in verdict if v['is_campus'])}** 家 / "
        f"降级 websearch **{sum(1 for v in verdict if not v['is_campus'])}** 家",
        f"- 校招岗位 **{len(campus_jobs)}** 条（合并去重后）",
        f"- 删除推断补全岗 **{len(dropped_infer)}** 条", "",
        "## 原标签 web/未知/社招 → 现认校招（82 家的核心修正）", "",
        "| 企业 | 原标签 | 校招岗数 | 渠道 |",
        "|---|---|---:|---|",
    ]
    fixed = [v for v in verdict if v["is_campus"] and v["old_tag"] != "校招公告"]
    fixed.sort(key=lambda v: -v["n_campus_jobs"])
    for v in fixed:
        lines.append(f"| {v['name']} | {v['old_tag']} | {v['n_campus_jobs']} | {v['channels']} |")
    lines += ["", "## 有校招证据但明显不齐（1-2 岗，降级 websearch）", "",
              "| 企业 | 原标签 | 校招岗数 |", "|---|---|---:|"]
    down = [v for v in verdict if not v["is_campus"] and v["n_campus_jobs"] > 0]
    down.sort(key=lambda v: -v["n_campus_jobs"])
    for v in down:
        lines.append(f"| {v['name']} | {v['old_tag']} | {v['n_campus_jobs']} |")
    lines += ["", "## 完全无校招证据（维持原状）", "",
              f"{sum(1 for v in verdict if v['n_campus_jobs'] == 0)} 家，无 URL 证据可证明，不认校招。",
              "", "## 删除的推断补全岗（无 URL 证据）", "",
              f"共 {len(dropped_infer)} 条，样例："]
    for d in dropped_infer[:30]:
        lines.append(f"- {d['company']}：{d['title']}")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("新库:", OUT_DB)
    print("报告:", OUT_MD)
    print("145 家 → 校招", sum(1 for v in verdict if v["is_campus"]),
          "/ 降级", sum(1 for v in verdict if not v["is_campus"]))
    print("校招岗位", len(campus_jobs), "| 删除推断补全岗", len(dropped_infer))


if __name__ == "__main__":
    main()
