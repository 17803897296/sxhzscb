"""行业码：企业的「这一家是干什么的」用一张固定枚举说清楚。

为什么要这个（2026-11-17 用户定）：
之前判「这家企业和大学生的专业对不对口」，靠的是 15 套专业族关键词。实测出来
两边都失真——化学的词表窄（电解铝、靶材、印染这些词不含「材料」二字，全被漏掉，
于是真对口的被判「没方向词」），机械/电气的词表宽（「制造/生产/设备/加工」这些
词家家都有，于是对什么都通过）。按这个词表继续补，只能是打地鼠。

改法是换判据，不是补词：
    企业侧：建库时给每家打一个**行业码**（本文件的 CODES），写进库；
    学生侧：专业族 × 行业码 = 一张准入表（PRO_MAP，core / edge / 不放）；
    匹配时：查表，不跑关键词、不调模型、不联网。
关键词只在这里用一次——把已有企业的主营文本归到码上，结果可人工审、可测。

和「一锤子买卖」的区别：
    - 新企业进库时，采集链路（enrich_by_llm 的 prompt）让模型直接给码；
    - 老企业/外部交付包进库时，backfill() 用本文件的规则补；
    - refresh_all 里固定跑一步 step_ind_code()，缺码就补、覆盖率低了就报警；
    - 码表导出 data/industry_codes.json，外部「企业搜索」工程引用同一份枚举，
      两边不会各说各话。

命令行：
    python -X utf8 ind_code.py --report     # 只看分布与覆盖率，不写库
    python -X utf8 ind_code.py --backfill   # 给缺码的企业补码（活库 + 增补档案）
    python -X utf8 ind_code.py --export     # 导出码表 JSON 给采集侧
"""

import argparse
import io
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LIVE_DB = os.path.join(DATA, "careersafari.db")
ENRICH_DB = os.path.join(DATA, "fair2026_enrich.db")

UNKNOWN = "unknown"
# 人工确认过的码。优先级最高：模型和规则都会重跑，改词表只能影响下一次猜测，
# 人看过原文定下来的结论必须钉住，不许被下一次重跑冲掉。
OVERRIDE = os.path.join(DATA, "_ind_code_override.json")

# ── 码表 ──────────────────────────────────────────────────────────────
# 每项：(码, 中文名, 别名, 强词, 弱词)
#   强词：基本只在这一行出现的词（芯片、家纺、电解铝），命中 1 个就够
#   弱词：跨几行但仍有区分度（电池、汽车、光伏），要命中 2 个才算
# 故意**不收**的词：制造、生产、加工、设备、研发、科技、销售、服务、实业、集团
#   —— 这些词在 145 家主营里几乎家家都有，收进来就等于没有判据（这是上一版
#   关键词方案翻车的直接原因）。
CODES = [
    ("semi", "半导体与集成电路",
     ("芯片", "半导体", "集成电路", "晶圆", "封测", "光刻", "存储芯片"),
     ("芯片", "半导体", "集成电路", "晶圆", "封测", "光刻", "刻蚀", "DRAM", "NAND",
      "存储颗粒", "封装测试", "IC设计", "EDA", "碳化硅", "氮化镓"),
     ("电子", "器件", "制程")),
    ("elec", "电子通信与光电",
     ("电子", "通信", "光电", "元器件", "显示"),
     ("电子", "通信", "光电", "元器件", "传感器", "PCB", "线路板", "显示屏", "面板",
      "光纤", "射频", "微波", "天线", "激光", "摄像头", "整机",
      "交换机", "以太网"),
     ("设备", "模组", "仪器", "智能硬件")),
    ("it", "软件与信息服务",
     ("软件", "互联网", "信息技术"),
     ("软件", "互联网", "云计算", "大数据", "人工智能", "算法", "SaaS", "信息化",
      "信息系统", "网络安全", "数据中心", "数据库", "系统集成", "数字",
      "平台开发", "小程序", "APP"),
     ("系统", "网络", "算力")),
    ("mech", "机械装备与汽车零部件",
     ("机械", "装备", "汽车", "零部件"),
     ("机械", "装备", "汽车", "整车", "零部件", "模具", "机床", "工程机械", "重工",
      "液压", "轴承", "齿轮", "焊接", "铸造", "锻压", "机器人", "减速器", "压缩机",
      "发动机", "变速箱", "自动化产线", "家具", "电动工具", "切割"),
     ("结构", "精密", "金属")),
    ("ele", "电气与自动化",
     ("电气", "自动化", "电机"),
     ("电气", "自动化", "电机", "变频", "PLC", "伺服", "配电", "开关柜", "变压器",
      "电控", "仪表", "工控", "传动", "控制器", "控制系统", "驱控"),
     ("电力", "控制", "智能")),
    ("chem", "化工与新材料",
     ("化工", "化学", "新材料"),
     ("化工", "化学", "材料", "试剂", "涂料", "树脂", "胶粘", "助剂", "催化剂",
      "添加剂", "颜料", "染料", "油墨", "农药", "化肥", "日化", "香精", "香料",
      "发酵", "聚合", "改性", "粉体", "陶瓷", "玻璃", "稀土", "永磁", "靶材",
      "硅料", "多晶硅", "电子化学品", "光刻胶", "橡胶", "塑料", "锂电", "正极",
      "负极", "电解液", "隔膜", "防腐", "表面处理", "电镀", "炼油",
      "纳米薄膜", "杀虫剂", "纺丝"),
     ("精细", "合成", "提取")),
    ("metal", "冶金与金属加工",
     ("冶金", "冶炼", "有色金属"),
     ("冶金", "冶炼", "电解铝", "氧化铝", "预焙阳极", "铝箔", "铜材", "钢材",
      "特钢", "不锈钢", "压延", "有色金属", "金属材料", "合金", "铸造", "铝型材",
      "高纯铝", "铝土矿", "铜产品", "铜合金", "电极箔", "铝电解"),
     ("金属", "材料", "加工")),
    ("pharma", "医药与医疗器械",
     ("医药", "制药", "医疗器械"),
     ("医药", "制药", "药品", "原料药", "制剂", "生物药", "疫苗", "临床", "诊断",
      "医疗器械", "器械", "医院", "药房", "CRO", "CDMO", "中药", "保健品"),
     ("医疗", "健康", "生物")),
    ("food", "食品饮料与农产品",
     ("食品", "饮料", "农产品"),
     ("食品", "饮料", "乳品", "烘焙", "餐饮", "调味", "速冻", "零食", "茶饮",
      "酒类", "粮油", "肉制品", "农产品", "养殖", "种植", "饲料", "品控",
      "食品安全"),
     ("消费", "品牌", "加工")),
    ("textile", "纺织服装与家纺",
     ("纺织", "服装", "家纺"),
     ("纺织", "服装", "家纺", "面料", "针织", "无纺", "床上用品", "制衣", "印染",
      "染整", "化纤", "鞋服", "箱包"),
     ("服饰", "布", "纱")),
    ("build", "建筑与土木工程",
     ("建筑", "土木", "施工"),
     ("建筑", "土木", "施工", "地产", "监理", "造价", "市政", "装饰", "园林",
      "基建", "基础设施", "房建", "建材", "水泥", "混凝土", "钢结构", "勘察", "设计院",
      "工程总承包",
      "物业", "住宅", "写字楼", "城市开发", "城市建设", "产业园区", "园区运营"),
     ("工程", "建设", "装修")),
    ("energy", "能源电力与环保",
     ("能源", "电力", "环保"),
     ("能源", "电力", "环保", "节能", "新能源", "光伏", "风电", "储能", "脱硫",
      "水处理", "固废", "垃圾发电", "氢能", "核电", "火电", "水电", "供热",
      "清洁供能", "生态修复"),
     ("发电", "环境", "清洁")),
    ("finance", "金融与保险",
     ("金融", "银行", "保险"),
     ("金融", "银行", "证券", "保险", "基金", "投资", "信托", "期货", "租赁",
      "资管", "财富管理", "信贷", "风控", "支付", "会计师事务所", "审计"),
     ("财务", "资本", "资产")),
    ("trade", "商贸零售与电商",
     ("商贸", "零售", "电商"),
     ("商贸", "贸易", "零售", "电商", "连锁", "超市", "百货", "批发", "进出口",
      "外贸", "跨境", "经销", "代理商", "门店", "快消", "品牌运营", "人力资源",
      "劳务", "采购平台", "MRO", "商业综合体", "招商", "营运"),
     ("销售", "渠道", "营销")),
    ("logistics", "物流与供应链",
     ("物流", "供应链", "仓储"),
     ("物流", "供应链", "仓储", "运输", "货代", "快递", "配送", "冷链", "报关",
      "货运", "港口", "航运", "第三方物流", "分拨"),
     ("仓配", "车队", "库存")),
    ("edu", "教育与培训",
     ("教育", "培训", "学校"),
     ("教育", "培训", "学校", "教学", "课程", "教研", "幼儿园", "中学", "大学",
      "职业培训", "留学", "考试", "教辅", "辅导", "奥数", "升学", "培优", "竞赛班"),
     ("老师", "教师", "学员")),
    ("design", "设计与文化传媒",
     ("设计", "传媒", "文创"),
     ("设计", "视觉", "创意", "广告", "传媒", "文化", "影视", "动漫", "游戏",
      "出版", "会展", "公关", "新媒体", "MCN", "摄影"),
     ("品牌", "内容", "策划")),
    ("agri", "农林牧渔",
     ("农业", "林业", "牧业"),
     ("农业", "林业", "牧业", "渔业", "种植", "养殖", "种业", "农机", "化肥",
      "饲料", "农产品加工", "合作社"),
     ("农田", "作物", "畜牧")),
    ("public", "政府事业与科研",
     ("政府", "事业", "科研"),
     ("政府", "事业单位", "党校", "医院", "高校", "研究院", "研究所", "疾控",
      "检验检疫", "机关", "协会", "基金会", "组织", "政务", "行政审批",
      "社会保险", "就业促进", "人才服务", "考务"),
     ("公共", "公益", "编制")),
    ("aero", "航空航天与船舶",
     ("航空", "航天", "船舶"),
     ("航空", "航天", "船舶", "卫星", "火箭", "导弹", "无人机", "飞控", "总装",
      "遥测", "遥感", "导航", "舰船", "海洋工程"),
     ("飞行", "军工", "国防")),
]

CODE_IDS = [c[0] for c in CODES]
CODE_NAMES = {c[0]: c[1] for c in CODES}
ALIASES = {}
for _c in CODES:
    ALIASES[_c[0]] = _c[0]
    ALIASES[_c[1]] = _c[0]
    for _a in _c[2]:
        ALIASES.setdefault(_a, _c[0])

# 粗行业（企业库 industry 列，10 类）→ 码：只在主营没有足够证据时兜底，且只用
# 语义确定的几类。「制造」不映射（26 家全塞进 mech 就是制造假阳性）。
INDUSTRY_FALLBACK = {
    "教育": "edu", "金融": "finance", "建筑": "build", "医药": "pharma",
    "能源": "energy", "IT": "it", "物流": "logistics",
    "政府事业": "public", "消费": "trade",
}

# ── 专业族 × 行业码 准入表 ────────────────────────────────────────────
# core：这家企业的主营方向与该专业对口，领域岗（研发/工艺/设计）都能投
# edge：有限对口——只放通用职能岗（采购/质检/生产计划/EHS/销售…），
#       不放纯研发；判据与 biz_recheck._GENERIC_JOB 同源
# 表里没写的组合 = 不放。**这张表是唯一判据**，改它等于改匹配口径，要过测试。
PRO_MAP = {
    "化学化工材料": {"chem": "core", "metal": "core", "pharma": "core", "food": "core",
                 "energy": "core", "textile": "edge", "semi": "edge",
                 "elec": "edge", "build": "edge", "trade": "edge"},
    "机械制造": {"mech": "core", "metal": "core", "aero": "core", "semi": "edge",
             "elec": "edge", "energy": "edge", "chem": "edge", "build": "edge",
             "logistics": "edge"},
    "电气自动化": {"ele": "core", "mech": "edge", "energy": "core", "semi": "edge",
              "aero": "edge", "build": "edge"},
    "电子信息": {"elec": "core", "semi": "core", "it": "edge", "aero": "edge",
             "mech": "edge", "ele": "edge"},
    "计算机软件": {"it": "core", "semi": "edge", "finance": "edge", "trade": "edge",
              "edu": "edge", "logistics": "edge", "design": "edge"},
    "物流供应链": {"logistics": "core", "trade": "core", "mech": "edge", "food": "edge",
              "chem": "edge", "elec": "edge", "pharma": "edge"},
    "金融财会": {"finance": "core", "trade": "edge", "public": "edge"},
    "土木建筑": {"build": "core", "energy": "edge", "public": "edge"},
    "医学生物": {"pharma": "core", "public": "edge", "food": "edge", "chem": "edge"},
    "食品": {"food": "core", "agri": "edge", "pharma": "edge", "trade": "edge"},
    "能源环境": {"energy": "core", "chem": "edge", "build": "edge", "metal": "edge"},
    "教育": {"edu": "core", "public": "edge", "design": "edge"},
    "设计创意": {"design": "core", "trade": "edge", "it": "edge"},
    "纺织服装": {"textile": "core", "chem": "edge", "trade": "edge"},
    "商贸管理": {"trade": "core", "logistics": "core", "finance": "edge",
             "edu": "edge", "design": "edge", "public": "edge"},
}

# 判定的三种结论。None（返回 None 而不是 'out'）专门留给「判不了」：
# 专业识别不出族、或企业还没打码 —— 这两种情况必须退到旧的关键词复检，
# 不能当成「不放」把人毙掉（数据缺口不是企业不符）。
FIT_CORE, FIT_EDGE, FIT_OUT = "core", "edge", "out"


def fit(families, code):
    """专业族 × 行业码 → core / edge / out / None（判不了）。

    这是匹配端唯一的准入判据：学生在库里是「专业族」，企业在库里是「行业码」，
    两个离散值一查表就有结论，不跑关键词、不调模型、不联网。

    为什么不再拿关键词判：同一份词表对两头是不对称的 —— 化学的窄词表把靶材、
    永磁、涂布企业全卡在门外；物流/机械/电气的宽词表靠「制造/生产/设备」通吃，
    连出版社都能过；土木因为「工程」二字全线放行。补词表只能补一头，查表
    两头都是枚举值，谁松谁紧一眼看得见。

    families 传多个族时取最宽松的那个（任一 core 即 core，否则任一 edge 即 edge），
    与 biz_recheck 的「全局方向集兜底」口径一致。
    """
    fams = [f for f in (families or ()) if f]
    c = normalize(code)
    if not fams or c == UNKNOWN:
        return None
    edge = False
    for fam in fams:
        v = (PRO_MAP.get(fam) or {}).get(c)
        if v == FIT_CORE:
            return FIT_CORE
        if v == FIT_EDGE:
            edge = True
    return FIT_EDGE if edge else FIT_OUT


def load_override(path=None):
    """读人工确认表 -> {企业名: {"code", "why", "date"}}。

    分歧清单（data/_ind_code_review.json）里模型和规则打架的那些，拿一眼主营
    就能核掉；核完写进这里，以后重跑模型/规则都不会覆盖。这是整条链路最后一
    道闸，也是唯一「不需要联网、不需要词表就能定论」的闸。
    """
    p = path or OVERRIDE
    if not os.path.exists(p):
        return {}
    try:
        with io.open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def normalize(raw):
    """模型/人手写的码、中文名、别名 → 合法码；认不出 → unknown（不抛错）。"""
    s = str(raw or "").strip()
    if not s:
        return UNKNOWN
    if s.lower() in CODE_NAMES:
        return s.lower()
    if s in ALIASES:
        return ALIASES[s]
    # 模型常写「半导体与集成电路（semi）」这类混排，抓括号外的中文名再试一次
    for name, code in ((c[1], c[0]) for c in CODES):
        if name and name in s:
            return code
    return UNKNOWN


def _hits(text, words):
    return [w for w in words if w and w in text]


def classify(name="", industry="", segment="", scope="", brief=""):
    """按主营文本判行业码。返回 (码, 置信度, 依据)。

    置信度：
      high   强词命中 >= 1，且领先第二名至少 2 分（不会两头都像）
      medium 只有弱词够分，或强词命中但第二名紧随
      low    靠粗行业兜底
      none   判不出 → unknown（下游按「现场待问」处理，不判死）
    """
    # 主营描述优先用 segment（最精炼、就是业务本身），其次 biz_scope；
    # 企业名只在前两者都空时才用——名字里的「农业科技」「教育科技」经常是
    # 集团名，拿它当主营会整片带偏。
    main = " ".join(str(x or "") for x in (segment, scope, brief)).strip()
    text = (main + " " + str(name or "")) if main else str(name or "")
    if not text.strip():
        return UNKNOWN, "none", "没有任何主营描述可判"

    def pos_score(words, base):
        """返回 (命中词, 分数, 最先出现的位置)。

        位置要单独返回：同分时按「谁先出现在主营里」定胜负。主营开头写的
        是主业，后面列的是业务范围——「全球领先的出版公司，期刊覆盖生物、
        化学、医药」这句里，「出版」在 8 字、「化学」在 30 字，该判出版。
        """
        hit, score, first = [], 0, 10 ** 6
        for w in words:
            if not w:
                continue
            i = text.find(w)
            if i < 0:
                continue
            hit.append(w)
            first = min(first, i)
            score += base if i < 80 else max(1, base // 2)
        return hit, score, first

    strong_scores, weak_scores, why = {}, {}, {}
    for code, _name, _alias, strong, weak in CODES:
        sh, ss, sp = pos_score(strong, 4)
        wh, ws, wp = pos_score(weak, 2)
        if sh:
            strong_scores[code] = (ss, sp)
            why[code] = sh[:3] + wh[:2]
        elif wh:
            weak_scores[code] = (ws, wp)
            why[code] = wh[:4]

    # 强词命中优先：一个行业专属词（芯片/家纺/电解铝）胜过一堆通用弱词
    if strong_scores:
        ranked = sorted(strong_scores.items(), key=lambda kv: (-kv[1][0], kv[1][1], kv[0]))
        best, (top, _pos) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0
        conf = "high" if top - second >= 2 else "medium"
        return best, conf, "命中 " + "、".join(why[best])
    if weak_scores:
        ranked = sorted(weak_scores.items(), key=lambda kv: (-kv[1][0], kv[1][1], kv[0]))
        best, (top, _pos) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0
        if top >= 4 and top - second >= 2:
            return best, "medium", "弱词命中 " + "、".join(why[best])
        return UNKNOWN, "none", "只有泛词命中（%s），不足以判行业" % "、".join(why[best])
    fb = INDUSTRY_FALLBACK.get(str(industry or "").strip())
    if fb:
        return fb, "low", "主营无行业词，按粗行业「%s」兜底" % industry
    return UNKNOWN, "none", "主营没有可判的行业词（缺采集或词表未覆盖）"


def _item_fields(item):
    return dict(name=item.get("name") or "",
                industry=item.get("industry") or "",
                segment=item.get("segment") or "",
                scope=item.get("biz_scope") or "",
                brief=item.get("job_brief") or "")


def load_live():
    """读活库企业：name -> 判码所需字段。"""
    out = {}
    if not os.path.exists(LIVE_DB):
        return out
    con = sqlite3.connect(LIVE_DB)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("SELECT name, item_json FROM company_jobs"):
            try:
                it = json.loads(row["item_json"] or "{}")
            except Exception:
                it = {}
            it.setdefault("name", row["name"])
            out[row["name"]] = it
    finally:
        con.close()
    return out


def load_enrich():
    """读 145 家增补档案：name -> 行（含 segment，是判码最有用的字段）。"""
    out = {}
    if not os.path.exists(ENRICH_DB) or os.path.getsize(ENRICH_DB) == 0:
        return out
    con = sqlite3.connect(ENRICH_DB)
    con.row_factory = sqlite3.Row
    try:
        try:
            rows = list(con.execute("SELECT * FROM companies"))
        except sqlite3.OperationalError:
            return out
        for row in rows:
            out[row["name"]] = dict(row)
    finally:
        con.close()
    return out


def report(verbose=True):
    """覆盖率报表：不写库。给 refresh_all 与人工核查共用。"""
    live = load_live()
    enrich = load_enrich()
    names = sorted(set(live) | set(enrich))
    overrides = load_override()
    stat = {"total": len(names), "has_code": 0, "code": {}, "conf": {}, "unknown": [],
            "no_input": [], "mismatch": [], "manual": len(overrides)}
    for n in names:
        item = dict(live.get(n) or {})
        er = enrich.get(n) or {}
        f = _item_fields({**item, "segment": er.get("segment") or item.get("segment") or ""})
        code, conf, why = classify(**f)
        ov_code = normalize((overrides.get(n) or {}).get("code"))
        cur = normalize(item.get("ind_code"))
        if ov_code != UNKNOWN:
            code, conf = ov_code, "manual"
        elif cur != UNKNOWN:
            code, conf = cur, item.get("ind_conf") or conf
        if code != UNKNOWN:
            stat["has_code"] += 1
        stat["code"][code] = stat["code"].get(code, 0) + 1
        stat["conf"][conf] = stat["conf"].get(conf, 0) + 1
        if code == UNKNOWN:
            stat["unknown"].append((n, why))
        if not f["segment"] and not f["scope"]:
            stat["no_input"].append(n)
    if verbose:
        print("行业码覆盖：%d/%d 有码（%.0f%%）"
              % (stat["has_code"], stat["total"],
                 100.0 * stat["has_code"] / max(1, stat["total"])))
        print("  分布：")
        for code, cnt in sorted(stat["code"].items(), key=lambda kv: -kv[1]):
            print("    %-10s %-16s %d" % (code, CODE_NAMES.get(code, "未识别"), cnt))
        print("  置信度：" + "、".join("%s=%d" % kv for kv in sorted(stat["conf"].items())))
        if stat["manual"]:
            print("  人工确认 %d 家（priority 最高，重跑不覆盖）" % stat["manual"])
        if stat["unknown"]:
            print("  判不出的 %d 家（前 10）：" % len(stat["unknown"]))
            for n, why in stat["unknown"][:10]:
                print("    %-24s %s" % (n[:24], why))
    return stat


def backfill(dry=False, force=False):
    """给缺码的企业补码，写回两处：活库 item_json、增补档案新列。

    force=False 时只补缺的（有码就不动）——采集链路已经给过码的，这里不覆盖，
    避免「模型判的」被「规则猜的」冲掉。force=True 才整体重算。
    """
    live = load_live()
    enrich = load_enrich()
    overrides = load_override()
    names = sorted(set(live) | set(enrich))
    written = {"live": 0, "enrich": 0, "skip": 0}
    results = {}

    con_e = None
    if enrich and not dry:
        con_e = sqlite3.connect(ENRICH_DB)
        cols = [r[1] for r in con_e.execute("PRAGMA table_info(companies)")]
        for col, decl in (("ind_code", "TEXT"), ("ind_conf", "TEXT"), ("ind_why", "TEXT")):
            if col not in cols:
                con_e.execute('ALTER TABLE companies ADD COLUMN %s %s' % (col, decl))
        con_e.commit()

    con_l = None
    if not dry:
        con_l = sqlite3.connect(LIVE_DB)
    try:
        for n in names:
            item = dict(live.get(n) or {})
            er = enrich.get(n) or {}
            f = _item_fields({**item, "segment": er.get("segment") or item.get("segment") or ""})
            ov = overrides.get(n) or {}
            ov_code = normalize(ov.get("code"))
            if ov_code != UNKNOWN:
                # 人工确认过：哪怕 force 也不动它。
                code, conf = ov_code, "manual"
                why = "人工确认：" + str(ov.get("why") or "")
                results[n] = (code, conf, why)
                cur = normalize(item.get("ind_code"))
                if cur == code and (item.get("ind_conf") == "manual"):
                    written["skip"] += 1     # 已经是这个结论，不重写（别动库文件）
                    continue
                if dry:
                    continue
                if n in live:
                    item["ind_code"], item["ind_conf"], item["ind_why"] = code, conf, why
                    con_l.execute("UPDATE company_jobs SET item_json=? WHERE name=?",
                                  (json.dumps(item, ensure_ascii=False), n))
                    written["live"] += 1
                if con_e is not None and n in enrich:
                    con_e.execute("UPDATE companies SET ind_code=?, ind_conf=?, ind_why=? WHERE name=?",
                                  (code, conf, why, n))
                    written["enrich"] += 1
                continue
            cur = normalize(item.get("ind_code"))
            if cur != UNKNOWN and not force:
                results[n] = (cur, item.get("ind_conf") or "", item.get("ind_why") or "")
                written["skip"] += 1
                continue
            code, conf, why = classify(**f)
            results[n] = (code, conf, why)
            if dry:
                continue
            if n in live:
                item["ind_code"] = code
                item["ind_conf"] = conf
                item["ind_why"] = why
                con_l.execute("UPDATE company_jobs SET item_json=? WHERE name=?",
                              (json.dumps(item, ensure_ascii=False), n))
                written["live"] += 1
            if con_e is not None and n in enrich:
                con_e.execute("UPDATE companies SET ind_code=?, ind_conf=?, ind_why=? WHERE name=?",
                              (code, conf, why, n))
                written["enrich"] += 1
        if con_l is not None:
            con_l.commit()
        if con_e is not None:
            con_e.commit()
    finally:
        if con_l is not None:
            con_l.close()
        if con_e is not None:
            con_e.close()
    return results, written


def apply_new_codes(items, dry=False):
    """把「采集时模型顺手打的码」落地。items 是 [{name, code, why, src}]。

    与 backfill 的分工：backfill 是**规则兜底**（补缺口），这里是**模型结果落地**。
    两边遵守同一条优先级，谁也不许覆盖谁：

        人工确认（_ind_code_override.json） > 库里已有的 model 码 > 本次模型码 > 规则

    「库里已有的 model 码」也保留，是因为重跑采集时模型对同一家可能给出不同码，
    与其来回跳，不如认第一次的结论 —— 真要改就写进人工表，那里有 why 可查。
    """
    overrides = load_override()
    live = load_live()
    enrich = load_enrich()

    con_e = None
    if os.path.exists(ENRICH_DB) and os.path.getsize(ENRICH_DB):
        con_e = sqlite3.connect(ENRICH_DB)
        cols = [r[1] for r in con_e.execute("PRAGMA table_info(companies)")]
        for col in ("ind_code", "ind_conf", "ind_why"):
            if col not in cols:
                con_e.execute("ALTER TABLE companies ADD COLUMN %s TEXT" % col)
        con_e.commit()
    con_l = None if dry else sqlite3.connect(LIVE_DB)
    n = 0
    try:
        for x in items or []:
            name = str((x or {}).get("name") or "").strip()
            code = normalize((x or {}).get("code"))
            if not name or code == UNKNOWN:
                continue
            if normalize((overrides.get(name) or {}).get("code")) != UNKNOWN:
                continue                      # 人工已定论
            cur = normalize((live.get(name) or {}).get("ind_code"))
            if cur != UNKNOWN and (live.get(name) or {}).get("ind_conf") == "model":
                continue                      # 已有一份模型结论，不来回跳
            why = str((x or {}).get("why") or "")[:40]
            n += 1
            if dry:
                continue
            if con_l is not None and name in live:
                item = dict(live[name])
                item["ind_code"], item["ind_conf"], item["ind_why"] = code, "model", why
                con_l.execute("UPDATE company_jobs SET item_json=? WHERE name=?",
                              (json.dumps(item, ensure_ascii=False), name))
            if con_e is not None and name in enrich:
                con_e.execute("UPDATE companies SET ind_code=?, ind_conf='model', ind_why=? "
                              "WHERE name=?", (code, why, name))
        if con_l is not None:
            con_l.commit()
        if con_e is not None:
            con_e.commit()
    finally:
        if con_l is not None:
            con_l.close()
        if con_e is not None:
            con_e.close()
    return n


def export_codes(path=None):
    """导出码表给外部采集工程用：枚举 + 别名 + 说明，不放关键词（避免被抄成判据）。"""
    payload = {
        "_note": "行业码枚举（唯一真源 ent_tools/ind_code.py）。采集侧选码后用 ind_code.normalize 校验。",
        "version": 1,
        "unknown": UNKNOWN,
        "codes": [{"code": c[0], "name": c[1], "aliases": list(c[2])} for c in CODES],
        "pro_map": PRO_MAP,
    }
    p = path or os.path.join(DATA, "industry_codes.json")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="行业码：分类、回填、导出")
    ap.add_argument("--report", action="store_true", help="只看覆盖率与分布")
    ap.add_argument("--backfill", action="store_true", help="给缺码企业补码并写库")
    ap.add_argument("--force", action="store_true", help="忽略已有码，整体重算")
    ap.add_argument("--dry", action="store_true", help="配合 --backfill：只算不写")
    ap.add_argument("--export", action="store_true", help="导出 data/industry_codes.json")
    a = ap.parse_args(argv)
    if not any((a.report, a.backfill, a.export)):
        a.report = True
    if a.export:
        print("已导出 " + export_codes())
    if a.backfill:
        res, w = backfill(dry=a.dry, force=a.force)
        print("补码：写活库 %d 家、写增补档案 %d 家、已有码跳过 %d 家%s"
              % (w["live"], w["enrich"], w["skip"], "（dry run，未写）" if a.dry else ""))
    if a.report or a.backfill:
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
