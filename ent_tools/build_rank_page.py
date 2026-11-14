# -*- coding: utf-8 -*-
"""把李炳成画像的排名结果渲染成一张独立网页（可离线打开）。

数据来源：ent_tools/data/_rank_libingcheng.json
          （由 _rank_run.js 用 demo 页面自己的 calcOne / matchIntent 算出，
            所以本页分数与 demo 列表页显示的一致。）
输出：demo/ranking_libingcheng.html
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "_rank_libingcheng.json")
OUT = os.path.join(HERE, "..", "demo", "ranking_libingcheng.html")

DIM_SHORT = {
    "city": "城市", "home": "离家", "company_type": "企业类型", "industry": "行业",
    "scale": "规模", "industry_status": "行业地位", "growth": "成长性",
}


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    rows = d["rows"]
    prof = d["profile"]
    W = d["weights"]

    n_hit = sum(1 for r in rows if r["state"] == "hit")
    n_inf = len(rows) - n_hit
    n_sem = sum(1 for r in rows if r["intent_matches"])
    n_zero = sum(1 for r in rows if not r["job_titles"])
    n_hit_nomatch = sum(1 for r in rows if r["state"] == "hit" and not r["intent_matches"])
    scores = [r["score"] for r in rows]

    facts = {
        "fair_id": d["fair_id"],
        "major": prof.get("major") or "—",
        "intended": prof.get("intended_jobs") or [],
        "home": prof.get("home_city") or "—",
        "n": len(rows),
        "n_hit": n_hit, "n_inf": n_inf, "n_sem": n_sem, "n_zero": n_zero,
        "n_hit_nomatch": n_hit_nomatch,
        "smin": min(scores), "smax": max(scores),
        "weights": W,
        "gen": d["generated_at"][:19].replace("T", " "),
        "exp": prof.get("experiences") or [],
    }

    tpl = open(os.path.join(HERE, "_rank_page_tpl.html"), encoding="utf-8").read()
    html = (tpl.replace("/*__FACTS__*/", "const FACTS=" + json.dumps(facts, ensure_ascii=False) + ";")
               .replace("/*__ROWS__*/", "const ROWS=" + json.dumps(rows, ensure_ascii=False) + ";")
               .replace("/*__DIM__*/", "const DIM=" + json.dumps(DIM_SHORT, ensure_ascii=False) + ";")
               .replace("__BUILT_AT__", time.strftime("%Y-%m-%d %H:%M:%S")))
    open(OUT, "w", encoding="utf-8").write(html)
    print("已生成 %s（%.1f KB，%d 行）" % (OUT, len(html) / 1024, len(rows)))


if __name__ == "__main__":
    main()
