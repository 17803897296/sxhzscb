# -*- coding: utf-8 -*-
"""把「企业岗位画像」里的通用岗位补进岗位库，逐岗标注为大模型推断。

边界（用户拍板，详见 docs/采集流水线与并行调度.md 第 8 节）：
  · 没有方向性岗位的企业都补：非校招来源全补；校招公告但 0 岗 / 只有管培生的也补。
    校招公告且列了方向性真实岗的不碰——公告清单可信。
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
import job_level    # noqa: E402

DB = os.environ.get("FAIR_DB") or os.path.join(HERE, "data", "careersafari.db")
ENRICH = os.path.join(HERE, "data", "fair2026_enrich.db")
BEFORE = os.path.join(HERE, "data", "infer_fill_before.db")
AFTER = os.path.join(HERE, "data", "infer_fill_after.db")
NO_JOB_SOURCES = ("未知", "未知1", "未知2", "", "websearch", "社招信息")
KIND = "大模型推断"


def enabled():
    return (os.environ.get("FAIR_INFER_FILL") or "1").strip() != "0"


def usable_real(o):
    """这家企业「清洗之后还剩几个能用的真实岗位名」。

    岗位字段里有东西 ≠ 有岗位。批次名（「27届秋招」）、专业类目名、招考名目
    都会被 payload 侧的 `_clean_jobs` 滤掉；补库判定必须用同一把尺子，
    否则这类企业被当成「有岗企业」，永远轮不到补推断岗 —— 报告里就成了
    「企业有 145 家、点开一个岗位名都没有，只能按行业瞎猜」。
    """
    out = []
    for j in (o.get("jobs") or []):
        if not isinstance(j, dict):
            continue
        if str(j.get("job_source_kind") or "") == KIND:
            continue
        t = str(j.get("title") or "").strip()
        if t and job_level.is_job_title(t):
            out.append(t)
    return out


# 只有泛岗（管培生 / 储备干部 / 见习）的清单，等于没有方向性岗位：
# 公告原文只写「27届管培生」，学生看不出这家招什么职能，匹配端也没法判对口。
# 词表与 fair_bridge._GENERIC_ROLE 同一份（那边决定「只看得见管培生」的分层）。
_GENERIC = ("管培", "培训生", "储备干部", "储干", "储备生", "轮岗",
            "校招生", "见习")


def directed_real(o):
    """真实岗里带方向的那部分（已排除管培 / 储备这类泛岗）。"""
    return [t for t in usable_real(o) if not any(g in t for g in _GENERIC)]


def _non_enterprise():
    """增补档案里标成「非企业主体」的单位（学校 / 政府 / 研究所 / 人才中心）。

    给它们补推断岗没有意义：这些单位不按企业招聘逻辑设岗，一条「工艺工程师」
    推出来纯属噪声。读不到就返回空集，不影响补库（只是少一层过滤）。
    """
    if not os.path.exists(ENRICH) or os.path.getsize(ENRICH) == 0:
        return set()
    con = sqlite3.connect(ENRICH)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM companies WHERE non_enterprise=1")}
    except Exception:
        return set()
    finally:
        con.close()


def targets(con):
    """需要补推断岗的 [(name, item)]：**没有方向性岗位的一律补**。

    2026-11-18 用户第三次定口径（前两次：只补 0 岗 → 非校招来源全补）：
      · 非校招来源（websearch / 社招信息 / 未知）全部补 —— 清单注定不全；
      · 校招公告来源，但**只有管培生 / 储备干部这类泛岗**的也补 —— 那不是
        「有岗位」，只是「有批次」；学生看不出这家招什么职能，匹配端也无从
        判断对口，等于岗位名一个都没有；
      · 校招公告来源且**有方向性真实岗**的不补 —— 公告写得清清楚楚，再塞
        一份模型猜的编制进去，会盖住「这家实际招这些岗」这个事实；
      · 推断岗只追加、同名跳过，真实岗一个字段都不动；已补过的跳过（可重复执行）；
      · 排除非企业主体（学校 / 政府 / 研究所）。
    """
    out = []
    ne = _non_enterprise()
    for name, raw in con.execute("SELECT name,item_json FROM company_jobs"):
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if name in ne:
            continue                      # 学校 / 政府 / 研究所：不按企业招聘设岗
        jobs = [j for j in (o.get("jobs") or []) if isinstance(j, dict)]
        if any(str(j.get("job_source_kind") or "") == KIND for j in jobs):
            continue                      # 已经补过，不重复补（保证可重复执行）
        if (str(o.get("jobSource") or "").strip() not in NO_JOB_SOURCES
                and directed_real(o)):
            continue                      # 校招公告 + 有方向性真实岗：清单可信，不补
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
    todo = targets(con)
    print("补库目标 %d 家（非校招来源全补；已排除非企业主体与补过的）" % len(todo))
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
