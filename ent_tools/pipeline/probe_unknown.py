"""诊断脚本：跑 N 家企业，逐家判定「为什么被判成未知」。

用法：python -X utf8 probe_unknown.py --limit 20
输出：data/_probe_unknown.md + 控制台表格
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 脚本已移入 pipeline/：把 ent_tools 挂上 sys.path，兄弟模块照常导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrich_by_llm as e

CAUSES = {
    "page_fail": "没抓到页面",
    "not_recruit_page": "抓到页面但不是招聘页",
    "model_no_jobs": "模型没抽出岗位",
    "evidence_mismatch": "证据对不上被降级",
    "ok": "正常出岗位",
}


def diagnose(name):
    t0 = time.time()
    text, source = e.get_context(name)
    if not text:
        return {"name": name, "cause": "page_fail", "jobs": 0, "stage": "-",
                "source": "未知", "secs": time.time() - t0, "note": ""}

    kw = sum(1 for k in e.JOB_ACCEPT if k in text)
    try:
        raw = e.call_model(name, text, e.MODEL_CFG["key"], e.MODEL_CFG["base"], e.MODEL_CFG["model"])
    except Exception as exc:
        return {"name": name, "cause": "model_no_jobs", "jobs": 0, "stage": "-",
                "source": source, "secs": time.time() - t0, "note": "模型调用失败:" + str(exc)[:40]}

    raw_jobs = raw.get("jobs") or []
    src = str(raw.get("jobSource") or "未知")
    ev = str(raw.get("evidence") or "").strip()

    if src != "未知" and (not ev or ev not in text):
        cause = "evidence_mismatch"
    elif not raw_jobs:
        cause = "model_no_jobs"
    else:
        entry = e.build_entry(name, raw, text, source)
        if entry["jobs"]:
            cause = "ok"
        else:
            cause = "evidence_mismatch" if src != "未知" else "model_no_jobs"
        raw_jobs = entry["jobs"]

    stage = "-"
    if raw_jobs:
        m = re.search(r"招聘阶段：([^；;]+)", raw_jobs[0].get("requirements", ""))
        stage = m.group(1).strip() if m else "无字段"

    return {"name": name, "cause": cause, "jobs": len(raw_jobs), "stage": stage,
            "source": source[:60], "secs": time.time() - t0,
            "note": "页面关键词 %d 个" % kw}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    e.load_env()
    key = __import__("os").environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("没有 key")
        return 2
    e.MODEL_CFG = {
        "key": key,
        "base": __import__("os").environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": __import__("os").environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    }

    names = json.loads(Path("data/company_names.json").read_text(encoding="utf-8-sig"))[:args.limit]
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(diagnose, n): n for n in names}
        for fut in as_completed(futs):
            r = fut.result()
            rows.append(r)
            print("%-22s %-18s jobs=%d stage=%s %.0fs"
                  % (r["name"][:20], CAUSES[r["cause"]], r["jobs"], r["stage"], r["secs"]))

    order = ["page_fail", "not_recruit_page", "model_no_jobs", "evidence_mismatch", "ok"]
    stat = {k: sum(1 for r in rows if r["cause"] == k) for k in order}

    print("\n=== 归因汇总（%d 家）===" % len(rows))
    for k in order:
        print("  %-22s %2d 家" % (CAUSES[k], stat[k]))

    ok_rows = [r for r in rows if r["cause"] == "ok"]
    if ok_rows:
        stages = {}
        for r in ok_rows:
            stages[r["stage"]] = stages.get(r["stage"], 0) + 1
        print("\n=== 出了岗位的，阶段分布 ===")
        for k, v in sorted(stages.items(), key=lambda x: -x[1]):
            print("  %-10s %d 家" % (k, v))

    out = Path("data/_probe_unknown.md")
    lines = ["# 未知归因诊断（%d 家）" % len(rows), "", "## 汇总", ""]
    for k in order:
        lines.append("- %s：%d 家" % (CAUSES[k], stat[k]))
    lines += ["", "## 明细", "", "| 企业 | 原因 | 岗位数 | 阶段 | 耗时 | 页面关键词 | 来源 |",
              "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["cause"]):
        lines.append("| %s | %s | %d | %s | %.0fs | %s | %s |"
                     % (r["name"], CAUSES[r["cause"]], r["jobs"], r["stage"],
                        r["secs"], r["note"], r["source"]))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n明细：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
