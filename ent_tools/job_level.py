# -*- coding: utf-8 -*-
"""岗位层级（校招可投性）判定 —— 纯规则，前端 fair_report_js.py 的 miLevel 与这里同源。

为什么需要这一层：
    语义匹配原本只问「是不是同一类活」，于是把「供应链主管」判成「供应链管培生」
    的另一种说法、把「资深有机合成研究员」判成「有机合成研究员」的同一入口。
    同一职能里，主管 / 资深 / 经理是带团队或有年限要求的岗，2027 届应届投不进去；
    硬算成「对口」，等于给学生一个进不去的假希望，还挤掉了真正能投的岗位。

五档（只有档位，不含打分；本模块的结论只影响「算不算对口」，不进 calcOne）：

    1  实习 / 见习   实习生、见习生、暑期实习
    2  校招初入       管培生、培训生、校招生、储备干部 / 储干、专员、助理、
                      初级、专工 / 专责、见习期岗
    3  常规经验岗     名字里没有层级标记（工程师、分析师、运营、会计、研究员…）或中级
    4  资深 / 主管    资深、高级、主管、组长、班长、科长、主任、主办、督导、领班
    5  管理 / 专家    总经理、总监、经理、负责人、首席、专家、合伙人、董事、总裁

应届口径（本系统默认身份 = 参加双选会的在校生 / 应届生）：
    · 1~3 档算「可投」；4 / 5 档不算对口。企业本身不删、不写成「不招」，
      只在报告里注明「同职能但有主管 / 资深岗，校招一般投不进，可现场问是否按应届招」。
    · 意向与岗位层级差 ≥ 2 一律不算（意向 = 实习生 时，正式的工程师岗也不硬算同类）。
    · 意向本身就是 4 / 5 档（有工作经验的学生）时，才允许 4 / 5 档岗位。
    · 一条公告并列多个层级（「工艺工程师/主管/经理」「营销员/营销主管」）按**最低档**算：
      公告既然列了工程师、营销员，应届就能投这一档。宁可放行可解释的组合，
      也不因为出现「经理」二字把整条公告误杀。
"""
from __future__ import annotations

LEVEL_NAMES = {
    0: "未识别",
    1: "实习/见习",
    2: "校招初入",
    3: "常规经验岗",
    4: "资深/主管",
    5: "管理/专家",
}

# 应届生能投的最高档：常规经验岗。4 档以上是社招序列。
CAMPUS_MAX = 3

# ---- 管理 / 专家档 ----
LEAD_WORDS = ("总经理", "副总经理", "总监", "首席", "合伙人", "董事", "总裁",
              "经理", "负责人", "专家", "VP", "CTO", "CEO", "CFO", "COO")
# 名里带「经理」但校招照招的前台业务岗 / 产品岗——按常规经验岗处理。
# （客户经理、大堂经理、业务经理在券商 / 银行 / 制造业都是校招入口，
#  不能因为「经理」二字把整条线判成社招。）
LEAD_EXEMPT = ("客户经理", "大堂经理", "值班经理", "产品经理", "业务经理",
               "客户成功经理", "理财经理")

# ---- 资深 / 主管档 ----
SENIOR_WORDS = ("资深", "高级", "主管", "组长", "班长", "科长", "主任", "主办",
                "督导", "领班", "Sr.", "Senior")

# ---- 校招初入档 ----
ENTRY_WORDS = ("管培", "培训生", "校招", "应届", "储备", "储干", "专员", "助理",
               "初级", "专工", "专责", "干部", "实习生")

# ---- 实习 / 见习档 ----
INTERN_WORDS = ("实习", "见习")

# 名里带管理 / 资深词、但本身是助理岗的写法（校招常见入口）：
# 「总经理助理」「区域总监助理」「零碳事业部主任助理」都是助理位置，
# 不是管理位置，按常规经验岗算。
ASSIST_SUFFIX = ("助理", "秘书")

# 「高级」不表示资历的固定搭配：高级中学 / 高级技校 / 高级职称。
# 「主任」不表示管理岗的固定搭配：班主任（教师岗）。
SENIOR_FALSE = ("高级中学", "高级技工学校", "高级技校", "班主任", "主任委员")


def _norm(title):
    t = str(title or "").strip()
    return t.replace("（", "(").replace("）", ")").replace("　", " ")


def _level_one(seg):
    """单段岗位名 -> 档位。"""
    t = seg
    if not t:
        return 0

    # 0) 去掉不表示资历的固定搭配，避免「高级中学教师」「班主任」被误判
    scan = t
    for w in SENIOR_FALSE:
        scan = scan.replace(w, "")

    # 1) 高层助理：X 助理 / X 秘书 —— 助理位置本身是常规岗
    if any(t.endswith(w) for w in ASSIST_SUFFIX) or \
       any(t.endswith(w + "岗") for w in ASSIST_SUFFIX):
        return 3

    # 2) 管理 / 专家：先摘掉校招照招的例外写法
    probe = scan
    for w in LEAD_EXEMPT:
        probe = probe.replace(w, "")
    if any(w in probe for w in LEAD_WORDS):
        return 5

    # 3) 资深 / 主管
    if any(w in scan for w in SENIOR_WORDS):
        return 4

    # 4) 实习 / 见习
    if any(w in scan for w in INTERN_WORDS):
        return 1

    # 5) 校招初入
    if any(w in scan for w in ENTRY_WORDS):
        return 2

    return 3


def parse_level(title):
    """岗位名 -> (档位, 命中的写法)。并列多档时取最低档（见模块注释）。"""
    t = _norm(title)
    if not t:
        return 0, ""
    got = [(lv, s) for s in _segments(t) for lv in [_level_one(s)] if lv]
    if not got:
        return 0, ""
    lv, seg = min(got, key=lambda x: x[0])
    return lv, seg


# 能独立成岗的收尾字：只有每段都像独立岗位名时才按「/」拆开分档。
# 反例：「新市场/新项目开发经理」——「新市场」不是岗位，斜杠分的是业务对象，
# 拆开就会把一条「开发经理」公告误判成常规档。
_JOB_TAIL = ("员", "师", "生", "岗", "助", "理", "管", "监", "任", "工", "长",
             "代表", "顾问", "专员", "主管", "经理", "干部", "储干", "业")


def _segments(t):
    raw = [s.strip() for s in t.replace("、", "/").split("/") if s.strip()]
    if len(raw) <= 1:
        return raw
    if all(s.endswith(_JOB_TAIL) and len(s) >= 2 for s in raw):
        return raw
    return [t]


def level_of(title):
    return parse_level(title)[0]


def level_name(title):
    return LEVEL_NAMES.get(level_of(title), "未识别")


# ---- 岗位名有效性：不是岗位的写法，别当岗位展示、也别当岗位算对口 ----
# 双选会岗位表里混进来的三类噪声（真实数据里都有）：
#   招聘批次名「2027 届校园招聘」、专业类目「机械类 / 材化类」、
#   事业单位招考的名目「公务员 / 科学研究人员」。
_NON_JOB_EXACT = ("公务员", "参公人员", "事业编制", "专业技术人员", "科学研究人员")


def is_job_title(title):
    """True = 像一条真实岗位名。False = 批次名 / 专业类目 / 招考名目。"""
    t = _norm(title)
    if not t:
        return False
    if t in _NON_JOB_EXACT:
        return False
    if "校园招聘" in t or "届" in t and any(ch.isdigit() for ch in t):
        return False
    if t.endswith("类") and len(t) <= 8:
        return False
    if t.endswith("专业") or "及其自动化" in t:
        return False
    return True


def campus_max_for(intent):
    """该意向本身对应的最高可投档：
    意向是资深 / 管理岗（有经验的学生）时放开到 5 档，否则按应届口径 3 档。"""
    il = level_of(intent)
    return 5 if il >= 4 else CAMPUS_MAX


def check(intent, title):
    """判「这个岗位是不是该意向的可投同类」。

    返回 (ok, reason)：
      ok=False 时 reason 是给学生看的一句说明，说明为什么不算（层级不符），
      而不是笼统的「不相关」。
    """
    tl, seg = parse_level(title)
    if not tl:
        return True, ""
    cap = campus_max_for(intent)
    il = level_of(intent)
    if tl > cap:
        return False, ("「%s」属「%s」档，校招应届一般投不进（应届可投到「%s」）"
                       % (title, LEVEL_NAMES[tl], LEVEL_NAMES[cap]))
    if il and il <= 2 and tl - il >= 2:
        return False, ("「%s」属「%s」档，与你选的「%s」档差 %d 级"
                       % (title, LEVEL_NAMES[tl], LEVEL_NAMES[il], tl - il))
    if il >= 4 and tl <= 2:
        return False, ("你选的是「%s」，「%s」只到「%s」" % (LEVEL_NAMES[il], title, LEVEL_NAMES[tl]))
    return True, ""


def ok(intent, title):
    return check(intent, title)[0]
