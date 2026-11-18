# -*- coding: utf-8 -*-
"""虚拟用户端到端测试（常态化，可进 CI）。

2026-11-18 用户要求「虚拟用户测试脚本也写一个放代码做常态化」。
此前只在 scratch 里临时跑 heredoc，无法复现；本脚本把虚拟用户固化成代码。

每个虚拟用户跑三段：
  1) 推荐全量翻页：把 L1(切合)/L2(相邻)/L3(通用职能) 分层候选**全部**翻出来，
     不只看前 6 个（用户实测过「材料专业后面批次混进化学岗」就是只看前几漏掉的）。
  2) 常识复核：must_include 的对口岗必须在 L1；must_exclude 的别专业核心岗
     不得混入 L1/L2。
  3) 145 家匹配（可选 --with-match）：注入虚拟画像跑 fair_bridge.build()，
     OUT 指到 scratch 不写生效 payload；核对行数、hit/inferred 状态、
     fit=no 抑制（主榜不含 fit=no）。

用法：
  python -m ent_tools.virtual_user_e2e                    # 推荐分层 + 常识复核（离线、快）
  python -m ent_tools.virtual_user_e2e --with-match       # 再加 145 家匹配（需 DB）
  python -m ent_tools.virtual_user_e2e --with-match --tag 公共卫生   # 只跑单个画像
退出码：失败数（全绿 0）。报告写 scratch/virtual_user_e2e_report.json。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import job_recommend as J  # noqa: E402

SCRATCH = os.path.join(HERE, "scratch")

# ── 虚拟用户画像表 ──────────────────────────────────────────────
# must_include：本专业的对口岗，至少一个要出现在 L1（否则等于没对口岗）。
# must_exclude：不许出现在 L1 的别专业岗（泛岗降到 L2 是允许的，符合「降级不丢失」）。
# must_exclude_all：任何层都不许出现（邻专业核心岗混专业，如材料混化学核心岗）。
# 覆盖本次修的重灾区 + 之前实测过的代表专业（防止回归）。
PROFILES = [
    {"tag": "公共卫生", "major": "公共卫生",
     "must_include": ["公卫医师", "疾病预防控制专员", "流行病学调查员"],
     "must_exclude": ["机械设计工程师", "法务专员", "语文教师", "软件开发工程师"]},
    {"tag": "基础医学", "major": "基础医学",
     "must_include": ["基础医学研究员", "病理技师"],
     "must_exclude": ["法务专员", "机械设计工程师", "语文教师"]},
    {"tag": "法学", "major": "法学",
     "must_include": ["法务专员", "律师助理", "合规专员"],
     "must_exclude": ["公卫医师", "机械设计工程师", "GIS工程师"]},
    {"tag": "中国语言文学", "major": "中国语言文学",
     "must_include": ["语文教师", "中文编辑"],
     "must_exclude": ["公卫医师", "法务专员", "机械设计工程师"]},
    {"tag": "心理学", "major": "心理学",
     "must_include": ["心理咨询助理", "心理教师"],
     "must_exclude": ["公卫医师", "机械设计工程师", "法务专员"]},
    {"tag": "地理科学", "major": "地理科学",
     "must_include": ["GIS工程师", "测绘技术员"],
     "must_exclude": ["法务专员", "公卫医师", "语文教师"]},
    {"tag": "建筑学", "major": "建筑学",
     "must_include": ["建筑设计助理", "建筑师助理"],
     "must_exclude": ["法务专员", "公卫医师", "焊接工程师"]},
    {"tag": "核工程", "major": "核工程与核技术",
     "must_include": ["核工程师", "辐射防护工程师"],
     "must_exclude": ["法务专员", "公卫医师", "语文教师"]},
    {"tag": "动物医学", "major": "动物医学",
     "must_include": ["宠物医生", "兽医", "动物疫病防治员"],
     "must_exclude": ["法务专员", "公卫医师", "机械设计工程师"]},
    {"tag": "音乐与舞蹈学", "major": "音乐与舞蹈学",
     "must_include": ["音乐教师", "舞蹈教师"],
     "must_exclude": ["法务专员", "公卫医师", "机械设计工程师"]},
    # ── 对照组：此前已修好的代表专业，防回归 ──
    {"tag": "工业工程", "major": "工业工程",
     "must_include": ["工业工程师", "质量工程师", "精益改善岗"],
     "must_exclude": ["法务专员", "公卫医师", "语文教师"]},
    {"tag": "机械", "major": "机械设计制造及其自动化",
     "must_include": ["机械设计工程师", "结构工程师"],
     "must_exclude": ["法务专员", "公卫医师", "语文教师"]},
    {"tag": "计算机", "major": "计算机科学与技术",
     "must_include": ["后端开发工程师", "算法工程师"],
     "must_exclude": ["法务专员", "公卫医师", "语文教师"]},
    # 2026-11-18：原来断言「管培生 / 商业分析岗」在 L1 —— 那其实是 bug 的形状
    # （会计学没登记基线岗，被经管大类表顶成泛经管）。会计的出口是会计/审计/税务。
    {"tag": "会计学", "major": "会计学",
     "must_include": ["财务会计岗", "审计助理", "税务专员"],
     "must_exclude": ["公卫医师", "机械设计工程师", "法务专员", "语文教师"]},
    {"tag": "材料", "major": "材料科学与工程",
     "must_include": ["材料研发工程师", "失效分析工程师"],
     "must_exclude_all": ["化工工艺工程师", "化工设备工程师", "中试/放大工程师"]},
    {"tag": "中医", "major": "中医学",
     "must_include": ["中医师", "中医临床医师"],
     "must_exclude": ["研发工程师", "医药代表", "机械设计工程师"]},
]


def check_recommend(prof):
    """第 1+2 段：推荐全量分层 + 常识复核。返回检查记录。"""
    major = prof["major"]
    l1 = J.core_jobs(major)
    l2 = J.near_jobs(major)
    all_shown = l1 + l2  # 全量（相当于把「换一批」翻到底）
    problems = []
    for t in prof["must_include"]:
        if t not in l1:
            problems.append("L1 缺对口岗: %s" % t)
    # must_exclude：别专业岗不得进 L1（泛岗降到 L2 允许）
    for t in prof.get("must_exclude", []):
        if t in l1:
            problems.append("L1 混入别专业岗: %s" % t)
    # must_exclude_all：邻专业核心岗任何层都不得混（如材料混化学核心岗）
    for t in prof.get("must_exclude_all", []):
        if t in all_shown:
            where = "L1" if t in l1 else "L2"
            problems.append("%s 混入别专业核心岗: %s" % (where, t))
    # 全量翻页后 L1 不得与 L2 重复（同一批里重复展示）
    dup = [t for t in l1 if t in l2]
    if dup:
        problems.append("L1/L2 重复: %s" % "、".join(dup[:3]))
    return {
        "tag": prof["tag"], "major": major,
        "l1": l1, "l2": l2, "l1_n": len(l1), "l2_n": len(l2),
        "problems": problems, "ok": not problems,
    }


def check_match(prof):
    """第 3 段：145 家匹配（注入画像，不写生效 payload）。

    返回检查记录；依赖 DB / 联网复核，异常时返回 ok=False 但带 error，
    由调用方决定是否计入退出码（CI 核心测试不跑这段）。
    """
    import fair_bridge
    real_out = fair_bridge.OUT
    real_read = fair_bridge.read_db
    try:
        base = json.load(io.open(real_out, encoding="utf-8"))
        weights = base.get("weights") or {}
        raw = {
            "fair_id": "FAIR-VIRT-E2E-" + prof["tag"],
            "identity": {"major": prof["major"]},
            "answers": {"intended_jobs": J.core_jobs(prof["major"])[:6],
                        "region": "苏州"},
            "fair": {"home_city": "苏州"},
        }
        fair_bridge.read_db = lambda _raw=raw: (*real_read()[:2], _raw)
        fair_bridge.OUT = os.path.join(SCRATCH, "pl_virt_e2e.json")
        out = fair_bridge.build()
    except Exception as e:
        return {"tag": prof["tag"], "ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        fair_bridge.OUT = real_out
        fair_bridge.read_db = real_read
    rows = out.get("rows") or []
    problems = []
    if not rows:
        problems.append("145 匹配 rows 为空")
    # fit=no 抑制：主榜任何行都不得直接带 fit=no（应进 biz_fit_no 留档段）
    fit_no_in_rows = [r["name"] for r in rows if (r.get("fit") or "") == "no"]
    if fit_no_in_rows:
        problems.append("主榜含 fit=no: %s" % "、".join(fit_no_in_rows[:5]))
    states = {}
    for r in rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
    return {
        "tag": prof["tag"], "major": prof["major"], "rows": len(rows),
        "states": states, "fit_no_in_rows": len(fit_no_in_rows),
        "biz_fit_no": len(out.get("biz_fit_no") or []),
        "problems": problems, "ok": not problems,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-match", action="store_true", help="再加 145 家匹配（需 DB）")
    ap.add_argument("--tag", help="只跑指定 tag 的画像")
    ap.add_argument("--json", help="报告输出路径（默认 scratch/virtual_user_e2e_report.json）")
    args = ap.parse_args(argv)

    profiles = [p for p in PROFILES if not args.tag or p["tag"] == args.tag]
    if not profiles:
        print("没有匹配的画像：%s" % args.tag)
        return 1

    report = {"profiles": [], "with_match": args.with_match}
    failures = 0
    for prof in profiles:
        rec = check_recommend(prof)
        if args.with_match:
            rec["match"] = check_match(prof)
            if not rec["match"]["ok"]:
                failures += 1
        if not rec["ok"]:
            failures += 1
        report["profiles"].append(rec)
        flag = "OK" if rec["ok"] else "FAIL"
        print("[%s] %-10s %s  L1(%d)/L2(%d)" % (
            flag, prof["tag"], rec["major"], rec["l1_n"], rec["l2_n"]))
        for p in rec["problems"]:
            print("    ! %s" % p)
        if args.with_match and not rec["match"]["ok"]:
            print("    ! 匹配: %s" % ("；".join(rec["match"].get("problems") or [])
                                    or rec["match"].get("error")))

    out = args.json or os.path.join(SCRATCH, "virtual_user_e2e_report.json")
    if os.path.isdir(os.path.dirname(out)):
        with io.open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n%d 虚拟用户，失败 %d 项，报告 %s" % (len(profiles), failures, out))
    return failures


if __name__ == "__main__":
    sys.exit(main())
