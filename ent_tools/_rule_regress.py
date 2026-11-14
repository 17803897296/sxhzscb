# -*- coding: utf-8 -*-
"""规则改动回归：对比改前(0030 备份) vs 改后，看 10 个历史意向的种子召回有无漂移，
以及历史 kept 列表是否被新规则误删。纯确定性，不调模型。"""
import importlib.util
import json
import os

import job_level

HERE = os.path.dirname(os.path.abspath(__file__))

def _ensure_old(d):
    """把改动前备份展开成可 import 的临时模块（.bak 后缀不能被 import）。"""
    import shutil
    src = os.path.join(d, "intent_semantics.py.bak_20261115_0030")
    dst = os.path.join(d, "_is_old_tmp.py")
    if os.path.isfile(src) and not os.path.isfile(dst):
        shutil.copy(src, dst)
    return dst




def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


old = load("IS_old", _ensure_old(HERE))
new = load("IS_new", os.path.join(HERE, "intent_semantics.py"))

# 只共享「工程师」通用后缀的岗位，被 BOUNDARY_TAIL 收紧后不再算同类。
# 这批不是 bug：旧规则「都是工程师就算同职能」正是用户指出的问题
# （机械设计 ≠ 研发 / 技术 / 装备 / 光机；电气 ≠ 硬件 / 系统 / 新能源）。
# 明确同职能的写法（结构设计工程师、结构工程师）仍由字面 / 设计规则命中，
# 这里逐条登记，避免每次回归都当成退化报警。
INTENTIONAL = {
    "机械设计工程师": ["研发工程师", "研发助理工程师", "模具工程师", "助理工程师",
                 "仿真工程师", "产品技术工程师", "装备研发工程师", "光机工程师",
                 "装备技术工程师", "技术工程师", "力学分析工程师", "园林工具结构工程师",
                 "结构工程师"],
    "电气工程师": ["初级硬件工程师", "研发工程师", "电机开发工程师（拧紧工具）",
               "电子工程师", "电力工程师", "电控软件工程师", "控制工程师",
               "硬件研发工程师", "自动化工程师", "自控工程师", "电控硬件工程师",
               "硬件研发工程师电子", "系统工程师", "新能源开发工程师",
               "综合能源工程师", "硬件工程师", "电机控制算法工程师", "热动集控工程师"],
}

payload = json.load(open(os.path.join(HERE, "data", "fair_payload.json"), encoding="utf-8"))
titles = sorted({str(t).strip() for r in payload.get("rows") or []
                 for t in (r.get("job_titles") or []) if str(t).strip()})
tset = set(titles)
verify = json.load(open(os.path.join(HERE, "data", "_v5_verify.json"), encoding="utf-8"))

print("清单 %d 条 / 回归意向 %d 个\n" % (len(titles), len(verify)))
bad = 0
for intent, v in verify.items():
    so = set(old._seeds(intent, tset))
    sn = set(new._seeds(intent, tset))
    add = sorted(sn - so)
    drop = sorted(so - sn)
    kept = v.get("kept") or []
    gone = [t for t in kept if not new._role_ok(intent, t) or not new._share_core(intent, t)]
    # 层级规则上线后，「人事经理 / 市场经理 / 法务合规负责人」这类岗位本就不该算
    # 应届专员的同类入口——它们从 kept 里消失是**修复**，不是退化。按档位分开算：
    # 只有仍在应届可投档（1~3）的岗位丢失才计入退化。
    cap = job_level.campus_max_for(intent)
    lost = [t for t in gone if job_level.level_of(t) <= cap]
    known = INTENTIONAL.get(intent, [])
    bydesign = [t for t in lost if t in known]
    lost = [t for t in lost if t not in known]
    bylv = [t for t in gone if job_level.level_of(t) > cap]
    flag = "  "
    if lost or drop:
        flag = "!!"
        bad += 1
    print("%s %-16s 种子 %d→%d 新增%s 消失%s 真实丢失%s 层级剔除%s 通用后缀收紧%d"
          % (flag, intent, len(so), len(sn),
             ("、".join(add[:8]) + ("…" if len(add) > 8 else "")) or "—",
             ("、".join(drop[:8])) or "—",
             ("、".join(lost)) or "—",
             ("、".join(bylv[:6])) or "—",
             len(bydesign)))

# 新意向：看新规则下 3 个新意向的种子是什么
print("\n--- 新意向种子（新规则）---")
for it in ("质量工程师", "新媒体运营专员", "外贸业务员"):
    print("%-12s %s" % (it, "、".join(sorted(new._seeds(it, tset))[:20]) or "—"))
print("\n退化项 %d 个" % bad)
