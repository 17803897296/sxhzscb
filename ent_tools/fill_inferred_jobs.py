# -*- coding: utf-8 -*-
"""把「企业岗位画像」里的通用岗位补进岗位库，逐岗标注为大模型推断。

边界（用户拍板，详见 docs/采集流水线与并行调度.md 第 8 节）：
  · 只补 0 岗 + 非校招来源企业；被校招核实库认作校招的一律不碰。
  · 画像不带学生意向，只按主营列"这类单位通常的编制"，换专业不用重算。
  · 推断岗带 job_source_kind="大模型推断"，source_url 为空，不冒充真实 JD。
  · 真实岗位一个字段都不改，只追加；同名已有岗跳过。
  · FAIR_INFER_FILL=0 可整段关闭；补库前后各存一份快照。

用法：
    python -X utf8 fill_inferred_jobs.py --dry     # 只打印将补什么，不写库
    python -X utf8 fill_inferred_jobs.py            # 正式补库
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import job_profile  # noqa: E402

DB = os.environ.get("FAIR_DB") or os.path.join(HERE, "data", "careersafari.db")
CAMPUS = os.path.join(HERE, "data", "campus_recheck.db")
BEFORE = os.path.join(HERE, "data", "infer_fill_before.db")
AFTER = os.path.join(HERE, "data", "infer_fill_after.db")
NO_JOB_SOURCES = ("未知", "未知1", "未知2", "", "websearch", "社招信息")
KIND = "大模型推断"


def enabled():
    return (os.environ.get("FAIR_INFER_FILL") or "1").strip() != "0"


def _campus_names():
    """校招核实库认作校招的企业：这些用真实 URL 岗，绝不补推断岗。"""
    if not os.path.exists(CAMPUS):
        return set()
    rc = sqlite3.connect(CAMPUS)
    try:
        return {r[0] for r in rc.execute("SELECT name FROM campus_company WHERE is_campus=1")}
    except Exception as exc:
        # 库在却读不出来必须报错，不能静默当成空名单——那样会把推断岗灌进校招企业。
        raise RuntimeError("校招核实库读取失败：%s" % exc)
    finally:
        rc.close()


def targets(con, campus):
    """返回需要补推断岗的 [(name, item)]：0 岗或非校招来源，且不在校招名单。"""
    out = []
    for name, raw in con.execute("SELECT name,item_json FROM company_jobs"):
        if name in campus:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        jobs = [j for j in (o.get("jobs") or []) if isinstance(j, dict)]
        src = str(o.get("jobSource") or "").strip()
        if jobs and src not in NO_JOB_SOURCES:
            continue                      # 有真实岗且来源是校招类，不补
        out.append((name, o))
    return out


def _one_row(name, o):
    return {
        "name": name,
        "industry": o.get("industry", ""),
        "nature": o.get("nature", ""),
        "segment": o.get("segment", ""),
        "scope": o.get("biz_scope", "") or o.get("biz_scope_raw", ""),
        "job_src": str(o.get("jobSource") or ""),
        "titles": [j.get("title", "") for j in (o.get("jobs") or []) if isinstance(j, dict)],
    }


def to_jobs(name, prof):
    """画像岗位 → 带标注的岗位对象。只收通过画像自身 suspect 校验的岗位。"""
    chain = str(prof.get("chain") or "").strip()
    out = []
    for j in (prof.get("jobs") or []):
        title = str(j.get("title") or "").strip()
        if not title or j.get("suspect"):
            continue
        out.append({
            "title": title,
            "requirements": "按企业主营业务推断的常规岗位，非招聘公告原文",
            "headcount": "未知",
            "urgency": "未知",
            "group": str(j.get("stage") or "未知"),
            "salary": "未知",
            "source_url": "",
            "job_source_kind": KIND,
            "job_profile_basis": chain[:120],
            "strength": str(j.get("strength") or "medium"),
        })
    return out


def _merge(old_jobs, add_jobs):
    """真实岗保留、同名跳过；只对推断岗做去重，返回 (合并结果, 新增数)。"""
    merged = [j for j in old_jobs if isinstance(j, dict)]
    have = {str(j.get("title") or "").strip() for j in merged}
    added = 0
    for j in add_jobs:
        if j["title"] in have:
            continue
        merged.append(j)
        have.add(j["title"])
        added += 1
    return merged, added


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写库")
    args = ap.parse_args(argv)

    if not enabled():
        print("FAIR_INFER_FILL=0，跳过画像推断岗补库")
        return 0
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    campus = _campus_names()
    todo = targets(con, campus)
    print("补库目标 %d 家（排除校招名单 %d 家）" % (len(todo), len(campus)))
    if not todo:
        con.close()
        return 0

    profs = job_profile.build_many([_one_row(n, o) for n, o in todo])
    plan, total = [], 0
    for name, o in todo:
        prof = profs.get(name) or {}
        if prof.get("via") != "llm" or not prof.get("jobs"):
            continue                      # 没建成画像就不补，绝不编
        add = to_jobs(name, prof)
        merged, added = _merge(o.get("jobs") or [], add)
        if added:
            plan.append((name, o, merged, added, len(add)))
            total += added
    print("将补推断岗 %d 条，覆盖 %d 家" % (total, len(plan)))
    for name, _o, _m, added, cand in plan[:8]:
        print("  %-24s +%d/%d" % (name[:22], added, cand))
    if args.dry:
        print("（--dry：未写库）")
        con.close()
        return 0

    shutil.copyfile(DB, BEFORE)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for name, o, merged, added, _cand in plan:
        o["jobs"] = merged
        o["job_infer_filled_at"] = now
        con.execute("UPDATE company_jobs SET item_json=?,updated_at=? WHERE name=?",
                    (json.dumps(o, ensure_ascii=False), now, name))
    con.commit()
    con.close()
    shutil.copyfile(DB, AFTER)
    job_profile.dump({n: profs.get(n) for n, _o, _m, _a, _c in plan if profs.get(n)})
    print("已补 %d 条推断岗，快照：%s / %s" % (total, os.path.basename(BEFORE), os.path.basename(AFTER)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
