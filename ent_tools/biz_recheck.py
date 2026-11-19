# -*- coding: utf-8 -*-
"""画像×主营复检（2026-11-17 用户定口径）：

命中不能只看「岗位名 ↔ 意向岗位」的字面，还要看「企业主营业务」是否容纳
「意向岗位 + 专业」的方向。反面案例：金融公司的「管培生」 × 物流专业学生
选的「管培生」—— 岗位名字面完全相同，但金融公司培养不出物流方向。

判定规则（确定性、无需模型；前端 JS 镜像同源，见 fair_report_js.BIZ_FIELDS）：

  学生方向词 = 专业所属领域词表 + 每个意向剥掉通用词后的核心词 + 该领域词表。
  - 明确命中（企业真实岗位名证据）：岗位名里出现方向词 → 保留（本家公告
    自带方向，比主营更硬）；否则主营文本出现方向词才保留；两边都没有 →
    降为现场待问（名字只是字面相同，主营与方向对不上）。
  - 推断命中（非公告证据）：岗位名是模型按画像猜的，不能自证方向 → 只有
    主营文本出现方向词才保留；主营为空 / 全是「未知」同样不保留（没有
    业务依据就不该推断）。
  - 学生方向词为空（专业未知且意向全是通用岗）→ 无法判断，全部放行。

主营词分两级，防止单个宽泛词误触发：
  - 强词：单次出现即可判「业务与方向相关」（物流 / 制造 / 生产…）；
  - 弱词：需出现 ≥2 个不同弱词（超市 / 商贸 / 跨境…）。
  网页导航垃圾短语（「安全生产」「主营产品」这类栏目标题）先剔除再匹配。

2026-11-17 追加「诊断」层（开发期口径）：一条对没通过时，不直接判企业不符，
先查原因 —— 岗位名自带方向 / 主营字段缺失 / 主营无词表方向词 / 只有宽泛词 /
主营是别的行业。前四类是我们自己的缺口（规则漏判、采集没采到、词表没覆盖、
证据太薄），开发期保留待补、写进 data/_recheck_diag.json；只有「主营是别的
行业」才降级为现场待问。数据与词表补齐后把 FAIR_RECHECK_POLICY 设成 strict，
待补的那批才按严格口径一起拦。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ind_code               # noqa: E402  专业族 × 行业码 准入表（第一判据）

# 通用词（从意向/岗位名里剥掉，剩下的才算方向核心）。长词在前，避免「管理培训生」
# 被「培训生」先吃掉一半。
GENERIC_TOKENS = (
    "管理培训生", "管培生", "培训生", "储备干部", "储干", "校招生",
    "专员/助理", "仓储管理员", "管理员", "工程师", "技术员", "代表", "办事员",
    "专员", "助理", "人员", "职员", "文员", "操作工", "实习生", "学徒", "岗",
    "主管", "经理", "总监", "员",
)

# 领域词表：(领域名, 专业标记, 意向核心标记, 强方向词, 弱方向词)
# 强词：主营文本出现一次即判「业务与方向相关」；弱词：需 ≥2 个不同弱词。
# 反面教训（2026-11-17）：「零售」放强词会把「零售金融」误判成商贸物流；
# 「安全生产」是节能集团网页导航栏的栏目标题，不是业务词，先剔再匹配。
FIELDS = [
    ("物流供应链",
     ("物流", "供应链", "仓储", "运输管理"),
     ("物流", "供应链", "仓储", "运输", "货代", "配送"),
     # 强词
     ("物流", "供应链", "仓储", "运输", "货代", "快递", "配送", "冷链", "报关",
      "货运", "电商", "外贸", "进出口", "制造", "生产", "工厂", "加工", "实业",
      "物流园"),
     # 弱词
     ("超市", "商超", "卖场", "跨境", "出口", "商贸", "分销", "采购", "贸易",
      "批发")),
    ("化学化工材料",
     ("化学", "化工", "材料", "高分子", "制药", "药学", "应用化学"),
     ("化学", "化工", "材料", "制药", "药学", "试剂", "涂料", "配方"),
     ("化学", "化工", "材料", "制药", "医药", "药", "试剂", "涂料", "配方",
      "石化", "精细化工", "实验", "电池材料", "树脂", "胶粘", "助剂", "冶金",
      "冶炼"),
     # 「分析/检测」太宽泛（广告分析软件、数据公司都有），降弱词：不参与主营判定
     ("分析", "检验", "化验", "检测", "质检", "测试")),
    ("机械制造",
     ("机械", "机电", "车辆", "过程装备", "焊接"),
     ("机械", "机电", "模具", "焊接", "液压", "装配"),
     ("机械", "机电", "装备", "制造", "汽车", "模具", "重工", "设备", "零部件",
      "加工", "焊接", "液压", "装配", "生产", "工厂", "实业"),
     ()),
    ("电气自动化",
     ("电气", "自动化", "控制"),
     ("电气", "自动化", "控制", "变频"),
     ("电气", "自动化", "控制", "电力", "电机", "变频", "PLC", "仪表",
      "智能装备", "制造", "生产", "设备"),
     ()),
    ("电子信息",
     ("电子", "通信", "微电子", "集成电路", "光电"),
     ("电子", "通信", "集成电路", "芯片", "半导体", "光电", "嵌入式"),
     ("电子", "通信", "芯片", "半导体", "集成电路", "光电", "电路", "嵌入式",
      "硬件", "元器件", "封装", "研发", "矿机"),
     ("测试", "科技")),
    ("计算机软件",
     ("计算机", "软件", "信息安全", "网络工程", "人工智能", "数据科学"),
     ("软件", "算法", "前端", "后端", "软件测试", "运维", "数据", "人工智能"),
     ("软件", "信息技术", "互联网", "云计算", "人工智能", "算法", "数据",
      "系统集成", "数字化", "科技", "网络"),
     ()),
    ("金融财会",
     ("金融", "会计", "财务管理", "审计", "经济学", "保险", "证券"),
     ("金融", "会计", "审计", "风控", "投资", "证券", "保险"),
     ("金融", "银行", "证券", "保险", "基金", "投资", "财务", "会计", "审计",
      "风控", "信托", "理财", "资本", "期货", "租赁"),
     ()),
    ("土木建筑",
     ("土木", "建筑", "工程管理", "造价", "城乡规划", "道路桥梁"),
     ("土木", "建筑", "造价", "施工", "监理", "结构", "规划"),
     ("建筑", "土木", "施工", "工程", "地产", "建设", "监理", "造价", "市政",
      "装饰", "园林", "基建"),
     ()),
    ("医学生物",
     ("临床", "医学", "生物", "护理", "药学"),
     ("医学", "临床", "生物", "护理", "医疗器械"),
     ("医院", "医疗", "医药", "生物", "诊断", "器械", "健康", "临床", "护理"),
     ()),
    ("食品",
     ("食品", "营养"),
     ("食品", "品控", "烘焙", "饮料"),
     ("食品", "饮料", "乳品", "烘焙", "餐饮", "农产品", "调味", "品控",
      "中医", "养生", "茶饮", "食养", "保健品"),
     ()),
    ("能源环境",
     ("能源", "环境", "环保", "安全工程"),
     ("能源", "环保", "环境", "安全", "节能"),
     ("能源", "环保", "环境", "节能", "电力", "新能源", "光伏", "风电", "储能",
      "脱硫", "水处理"),
     ()),
    ("教育",
     ("师范", "教育", "学前教育"),
     ("教师", "教研", "教务", "辅导"),
     ("教育", "学校", "培训", "教学", "课程", "教研"),
     ()),
    ("设计创意",
     ("设计", "美术", "动画", "视觉"),
     ("设计", "视觉", "插画", "UI"),
     ("设计", "视觉", "创意", "广告", "品牌", "传媒", "文化"),
     ()),
    # 诊断补出来的行业（2026-11-17）：家纺企业主营写「被子、件套、面料」，
    # 词表里没有纺织这一行 → 被误记成「词表未覆盖」。补上之后，化学学生看
    # 家纺企业会被正确判成「别的行业」，而不是「我们词表漏了」。
    ("纺织服装",
     ("纺织", "服装", "轻化工程"),
     ("纺织", "服装", "面料", "家纺"),
     ("纺织", "家纺", "面料", "服装", "针织", "无纺", "床上用品", "制衣"), ()),
    ("商贸管理",
     ("工商管理", "市场营销", "国际贸易", "电子商务", "管理科学", "人力资源"),
     ("市场", "营销", "销售", "运营", "电商", "外贸", "商务"),
     ("商贸", "贸易", "销售", "市场", "营销", "电商", "零售", "品牌", "运营",
      "进出口", "外贸", "跨境", "人力资源", "劳务", "招聘", "外包"),
     ()),
]

# 领域细分/工艺词（2026-11-17 诊断得出）：只用来判「这家企业与我的领域对不对
# 得上」，**不**用来判「它属于别的行业」。原因：这些词跨行业通用（塑料厂也
# 需要机械、玻璃厂也需要电气），拿去判「别的行业」会把对口的机械/电气学生
# 误判成不符。词源都是真实库里「主营有字、却没有本领域词」的企业（电解铝、
# 家纺印染、靶材、稀土永磁…），补上之后它们按对口处理，而不是被当成别行业。
FIELD_EXTRA = {
    "化学化工材料": (
        "电解", "氧化铝", "碳素", "阳极", "铝箔", "有色金属", "金属材料",
        "表面处理", "电镀", "涂装", "磷化", "钝化", "清洗剂", "溶剂", "颜料",
        "染料", "油墨", "油漆", "农药", "化肥", "日化", "香精", "香料",
        "发酵", "提取", "纯化", "色谱", "光谱", "质谱", "滴定", "合成",
        "聚合", "改性", "复合", "粉体", "陶瓷", "玻璃", "稀土", "永磁",
        "靶材", "硅料", "多晶硅", "单晶", "电子化学品",
        "光刻胶", "橡胶", "塑料", "锂电", "正极", "负极", "电解液",
        "隔膜", "催化剂", "添加剂", "防腐", "印染", "染整", "化纤",
        "造纸", "制浆"),
}

# 专业标记 → 族列表（**一对多**）。原来这里是 dict 推导，「药学」这种同时
# 属于「化学化工材料」和「医学生物」的标记会被后一个覆盖掉，药学学生的化学
# 方向整个消失（查表时代价更大：化学族直接不见了）。前端 BIZ_FIELDS 一直是
# 一对多，后端才是outlier —— 这里对齐前端。
_MAJOR_FIELD = {}
for _f in FIELDS:
    for _mk in _f[1]:
        _MAJOR_FIELD.setdefault(_mk, []).append(_f[0])
_INTENT_FIELD = {mk: f[0] for f in FIELDS for mk in f[2]}
_STRONG_BY_FIELD = {f[0]: tuple(f[3]) for f in FIELDS}
# 自家领域命中用的词表 = 强词 + 细分词；判「别的行业」仍只用强词（见下）
_HIT_BY_FIELD = {k: tuple(v) + tuple(FIELD_EXTRA.get(k) or ())
                 for k, v in _STRONG_BY_FIELD.items()}
_WEAK_BY_FIELD = {f[0]: tuple(f[4]) for f in FIELDS}

# 弱词同义族：同族词（商贸/贸易/批发/分销）在一篇主营里同时出现只算一个证据。
# 反面教训（2026-11-17）：「奕兴格商贸」merge 后 industry=贸易、主营含「商贸」，
# 两个同义词凑满 2 个弱词误过闸 —— 弱词必须按族计数。
_WEAK_GROUPS = [
    ("商贸", "贸易", "批发", "分销"),
    ("超市", "商超", "卖场"),
    ("跨境", "出口"),
    ("采购",),
]

# 网页导航垃圾短语：栏目标题混进主营文本（节能集团「主营业务 安全生产 节能期刊…」），
# 其中的实词（生产）会误触发强词。先剔再匹配。
_BIZ_JUNK_PHRASES = ("安全生产", "主营产品", "经营范围", "公司简介", "联系我们",
                     "时政要闻", "社会责任", "新闻中心")


def _strip_generic(text: str) -> str:
    """剥掉通用词，剩下的才是方向核心。反复剥直到不再变化。"""
    s = (text or "").strip()
    while True:
        before = s
        for tok in GENERIC_TOKENS:
            if tok in s:
                s = s.replace(tok, "")
        s = s.strip()
        if s == before:
            return s


def _clean_biz(text: str) -> str:
    """主营文本清干净：未知/空占位与导航垃圾短语剔除，多空白折叠。"""
    s = str(text or "")
    for junk in ("未知", "暂无", "None", "none") + _BIZ_JUNK_PHRASES:
        s = s.replace(junk, " ")
    return " ".join(s.split())


def student_domain(intended, major=""):
    """学生方向：返回 (方向词并集, 命中领域集合)。

    方向词用于岗位名匹配；领域集合用于主营文本的强/弱词两级判定。
    """
    kw: set = set()
    fields: set = set()
    m = str(major or "").strip()
    for mk, fams in _MAJOR_FIELD.items():
        if mk and mk in m:
            fields.update(fams)
    for it in (intended or []):
        core = _strip_generic(str(it or ""))
        if len(core) < 2:
            continue
        kw.add(core)
        for mk, field in _INTENT_FIELD.items():
            if mk in core:
                fields.add(field)
                break
    for field in fields:
        kw.update(_STRONG_BY_FIELD.get(field) or ())
        kw.update(_WEAK_BY_FIELD.get(field) or ())
    return tuple(sorted(kw)), fields


def biz_text_of(name, c) -> str:
    """企业主营上下文：名字 + 行业 + 细分 + 主营范围 + 岗位简介，清洗后返回。"""
    c = c or {}
    return _clean_biz(" ".join(str(x or "") for x in (
        name, c.get("industry"), c.get("segment"), c.get("biz_scope"),
        c.get("job_brief"))))


def pair_fields(intent, major=""):
    """单意向方向集：major + 这一个意向的领域。

    2026-11-17 用户问出的疑点：方向集原先按全意向并集算——化工学生选了
    「化工销售工程师」（含意向词「销售」）会把商贸管理领域拉进方向集，
    于是化学分析工程师的复检也跟着吃「贸易/零售/电商」强词。一个意向开门、
    全体意向受益，就是「金融公司管培生 × 物流专业」反面案例的变体。
    修法：主营复检按意向对各算各的（major + 该对里的意向），全局集只作
    空集兜底（专业和意向都识别不出时闸门不咬人，口径不变）。
    """
    return student_domain([intent], major or "")[1]


def biz_hit(kw_fields: set, biz_text: str) -> bool:
    """主营文本是否容纳这些领域：强词一票制。

    弱词（分析/检测/商超…）只参与岗位名/意向名的方向词匹配，不参与主营判定
    —— 推断命中宁可少标不错标（2026-11-17：广告分析软件公司靠「分析」误过闸）。
    """
    biz = _clean_biz(biz_text)
    if not biz or not kw_fields:
        return False
    return any(any(k in biz for k in (_HIT_BY_FIELD.get(field) or ()))
               for field in kw_fields)


# 跨行业通用职能/实验室岗：采购、仓储物流、质检化验、EHS、专利注册……这类岗
# 每家公司都设，不依赖主营——岗位名或意向含这些词时免查主营直放。
# 领域研发/工艺/合成岗（研发、工艺、合成、设计…）不在此列：必须主营沾边，
# 否则「半导体研发 & 工艺」会钻进化学学生的匹配（2026-11-17 用户反馈）。
_GENERIC_JOB = re.compile(
    r"采购|供应商|仓储|物流|供应链|客服|售前|售后|技术支持|EHS|安全工程师|"
    r"计划员|化验|质检|实验|检验|检测|分析|测试|质量|专利|注册|"
    r"生产管理|生产计划|生产跟单")


# ── 专业族 × 行业码 查表（2026-11-17 起成为第一判据）────────────────────
# 旧判据是「学生方向词 ∩ 主营文本」，两头都是自由文本，词表宽窄不对称：
# 化学的窄词表把靶材/永磁/涂布全卡掉，物流机械的宽词表靠「制造/生产」通吃，
# 土木靠「工程」二字全线放行。查表两头都是枚举值，谁松谁紧在表里一眼可见。
#
# 判不了（None）就退回旧的关键词通路，不当成「不放」：
#   - 专业识别不出族（数学/法学/外语…）；
#   - 企业还没打码（新进库、外部交付包没跑 build）。
def _code_name(code) -> str:
    """行业码 → 中文名（给报告里的解释用；认不出就原样回显）。"""
    c = ind_code.normalize(code)
    return ind_code.CODE_NAMES.get(c, str(code or "未知"))


def families_of_major(major) -> set:
    """专业名 → 专业族集合（族名与 ind_code.PRO_MAP 的 key 同一套）。"""
    m = str(major or "").strip()
    if not m:
        return set()
    out = set()
    for mk, fams in _MAJOR_FIELD.items():
        if mk and mk in m:
            out.update(fams)
    return out


def table_verdict(major, intent, code):
    """(专业, 单个意向, 企业行业码) → core / edge / out / None。

    族集合按「专业族 ∪ 这个意向的族」取，与 pair_fields 同源 ——
    一个意向开门、全体意向受益是老问题（化工学生选了销售意向，化学分析岗
    也跟着吃「贸易」的词），查表这边同样按对各算各的。
    """
    fams = families_of_major(major)
    core = _strip_generic(intent)
    if core:
        for mk, field in _INTENT_FIELD.items():
            if mk and mk in core:
                fams.add(field)
                break
    if not fams:
        return None
    return ind_code.fit(fams, code)


# ── 「为什么没有方向词」诊断（2026-11-17 用户定：开发期先查原因，不要直接毙）──
# 一条对没通过主营复检，根因分四类，处理方式完全不同：
#   name_dir     岗位名自己就写了方向词（「材料研发工程师」里的「材料」）→ 本该
#                放行：本家公告比主营更硬，属规则漏判，跟企业不符无关。
#   biz_missing  主营字段没采到（行业/细分/主营范围/简介都空，或只有「未知」）→
#                数据缺口。拿缺失当证据否决企业，等于把采集的锅算到企业头上。
#   biz_no_word  主营有实质文字，但一个本领域词都没出现（「溅射靶材、离子束
#                设备研发」）→ 多半是词表没覆盖（靶材/永磁/镀膜/晶体都不含
#                「材料」二字），该补词表，不该毙。
#   biz_weak     只出现「分析/检测/质检」这类宽泛词 → 证据太薄，人工看一眼。
#   biz_other    主营明确落在别的行业（金融/教育/软件/建筑…）→ 真不符，才降级。
DIAG_TEXT = {
    "name_dir": "岗位名自带方向词",
    "biz_missing": "主营字段缺失",
    "biz_no_word": "主营无词表方向词",
    "biz_weak": "主营只有宽泛词",
    "biz_other": "主营是别的行业",
    # 查表判据的三种结论（2026-11-17 起为第一判据，关键词退成兜底）
    "code_core": "行业码对口",
    "code_edge": "行业码有限对口（只放通用职能岗）",
    "code_out": "行业码不是这个方向",
}
# 只有这两类算「企业真的不符」，降级为现场待问。其余是我们自己的缺口
# （采集没采到 / 词表没覆盖 / 证据太薄），开发期一律保留待补——见 recheck_real。
DROP_CODES = frozenset(["biz_other", "code_out"])
# 一家企业有几条被问的对时，报告里展示最严重的那条。
DIAG_ORDER = ("code_out", "biz_other", "code_edge", "biz_no_word", "biz_missing",
              "biz_weak", "name_dir")

# 判「别的行业」时不算证据的通用词：制造/生产/科技/研发…几乎每家公司主营都有，
# 拿它判行业会张冠李戴（机械厂会先命中「物流供应链」的「制造」）。
_CROSS_GENERIC = frozenset((
    "制造", "生产", "工厂", "加工", "实业", "科技", "研发", "设备", "工程",
    "建设", "运营", "数据", "网络", "销售", "市场", "品牌", "服务", "技术",
    # 「设计」也是经营范围里的套话（「…的设计与销售」），拿它判「设计创意」
    # 行业会把靶材厂、设备厂全打成广告公司。
    "设计"))


def biz_parts(name, c=None) -> dict:
    """把主营上下文拆成字段：判断「没方向词」是数据缺口还是词表缺口要用到。

    与 biz_text_of 同源同序（名字 + 行业 + 细分 + 主营范围 + 岗位简介），
    多返回 has_desc：行业/细分/范围/简介是否至少有一个有实质文字。
    四项全空 = 主营没采到，不能据此判企业不符（那是采集的锅）。
    """
    c = c or {}
    out = {
        "name": str(name or ""),
        "industry": str(c.get("industry") or ""),
        "segment": str(c.get("segment") or ""),
        "scope": str(c.get("biz_scope") or ""),
        "brief": str(c.get("job_brief") or ""),
    }
    out["text"] = _clean_biz(" ".join(out[k] for k in
                                     ("name", "industry", "segment", "scope", "brief")))
    _desc = _clean_biz(" ".join(
        (out["industry"], out["segment"], out["scope"], out["brief"])))
    # 只写了「消费」「制造」这种两三个字的行业标签，等于没描述——判方向时
    # 按「主营没采到」处理，不能拿它当「主营有字但没方向词」。
    out["has_desc"] = len(_desc) >= 4
    return out


def other_fields_hit(biz_text: str, own_fields) -> list:
    """主营落在哪些「别的领域」上：返回 [(领域, 命中词), ...]。

    只在自家领域没命中时才用来判「真不符」：金融公司管培生 × 物流专业，
    自家（物流）不命中、别的领域（金融）命中 —— 这才叫企业不符。
    通用词（制造/生产/科技…）不算证据，否则机械厂会先命中物流域的「制造」。
    """
    biz = _clean_biz(biz_text)
    own = set(own_fields or ())
    out = []
    for field, words in _STRONG_BY_FIELD.items():
        if field in own:
            continue
        for w in words:
            if w and w not in _CROSS_GENERIC and w in biz:
                out.append((field, w))
                break
    return out


def diagnose(job, intent, biz_text, major="", kw_fields=None, parts=None) -> dict:
    """为什么这条对「没发现主营业务方向词」：返回 {code, label, detail, drop}。

    detail 是给人看的一句话：岗位名写了什么、主营字段缺了哪几项、主营原文
    写了什么、命中的是哪个别的行业 —— 这一层就是「为什么没有方向词」的答案，
    也是补词表 / 补采集的依据。drop=True 只给「主营确实是别的行业」。
    """
    job_s = str(job or "")
    pf = pair_fields(intent, major) or set(kw_fields or ())
    # ① 岗位名自带方向词：本家公告自证方向，比主营更硬
    hitw = [w for f in sorted(pf) for w in (_HIT_BY_FIELD.get(f) or ())
            if w and w in job_s]
    if hitw:
        return {"code": "name_dir", "label": DIAG_TEXT["name_dir"], "drop": False,
                "detail": ("岗位名「%s」里就写了「%s」，本家公告自证方向，不必靠主营"
                           % (job_s, "、".join(hitw[:3])))}
    biz = _clean_biz(biz_text)
    # ② 主营没采到：字段全空 → 数据缺口，不是企业不符
    if (parts is not None and not parts.get("has_desc")) or not biz:
        return {"code": "biz_missing", "label": DIAG_TEXT["biz_missing"], "drop": False,
                "detail": ("这家行业/细分/主营范围/岗位简介四项都没采到（或只有「未知」），"
                           "没有主营就没法判方向，属数据缺口")}
    # ③ 主营落在别的行业 → 真不符
    others = other_fields_hit(biz, pf)
    if others:
        # 取「先出现在主营里」的那行，同位置取更长的词：主营开头的往往是主业
        # （「节能环保集团…能源…大健康」应判能源，不是医学生物）。
        f0, w0 = min(others, key=lambda fw: (biz.find(fw[1]) if fw[1] in biz else 999,
                                             -len(fw[1])))
        return {"code": "biz_other", "label": DIAG_TEXT["biz_other"], "drop": True,
                "detail": ("主营是「%s」这一行（命中「%s」），主营原文「%s」，与你的方向不是一回事"
                           % (f0, w0, biz[:30]))}
    # ④ 只有宽泛词（分析/检测/质检）→ 证据太薄
    weak = [w for f in sorted(pf) for w in (_WEAK_BY_FIELD.get(f) or ())
            if w and w in biz]
    if weak:
        return {"code": "biz_weak", "label": DIAG_TEXT["biz_weak"], "drop": False,
                "detail": ("主营只出现「%s」这类宽泛词，凑不成方向证据（原文「%s」）"
                           % ("、".join(weak[:3]), biz[:30]))}
    # ⑤ 有实质文字、却没有本领域任何一个词 → 词表没覆盖
    need = [w for f in sorted(pf) for w in (_STRONG_BY_FIELD.get(f) or ())][:6]
    return {"code": "biz_no_word", "label": DIAG_TEXT["biz_no_word"], "drop": False,
            "detail": ("主营有文字（「%s」）但没有任何本领域词；期望的词是「%s」这类"
                       "—— 多半是词表没覆盖，补词后复检再看" % (biz[:40], "、".join(need)))}


def dump_diag(rows, path=None) -> str:
    """把本次复检的诊断清单落盘：开发期补词表 / 补采集的依据，可回溯。"""
    import io as _io
    import json as _json
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "_recheck_diag.json")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _io.open(p, "w", encoding="utf-8").write(
            _json.dumps({"generated_at": __import__("time").strftime(
                "%Y-%m-%d %H:%M:%S"), "rows": rows}, ensure_ascii=False, indent=1))
    except Exception as e:                      # 落盘失败不影响报告生成
        print("[主营复检] 诊断清单写盘失败：%s" % e)
    return p


def name_dir_words(job, fields) -> list:
    """岗位名里出现了哪些本领域强词（本家公告自证方向，比主营更硬）。"""
    js = str(job or "")
    if not js or not fields:
        return []
    return [w for f in sorted(fields) for w in (_HIT_BY_FIELD.get(f) or ())
            if w and w in js]


def recheck_real(real_pairs, kw, kw_fields, biz_text, major="", parts=None,
                 policy=None, diag=None, code=None):
    """明确命中复检（三态）：通过 / 待补 / 不通过，并记下「为什么」。

    2026-11-17 用户定口径：开发期不要直接毙，先查「为什么没有方向词」。
    「主营缺失」「词表没覆盖」是我们自己的缺口，拿它当证据否决企业，等于把
    采集/词表的锅算成企业不符 —— 所以开发期保留、标注待补、进诊断清单
    （data/_recheck_diag.json）；只有「主营明确落在别的行业」才降级为现场待问。
    数据与词表补齐后把 policy 设成 strict（或环境变量 FAIR_RECHECK_POLICY=strict），
    这批待补的对就按严格口径一起拦。

    2026-11-17 起先查表（专业族 × 行业码，见 table_verdict）：
      code 有值 → 查表即结论，不再读主营文本；
        core 直放 / out 降级现场待问 / edge 只放通用职能岗与岗位名自带方向的对；
      code 为 None（企业没打码）→ 退回下面的关键词通路，口径与之前完全一致。
    这样「判据是枚举还是文本」可回退、可对比，不会一次性推倒重来。

    返回 {"kept", "blocked", "watch", "weak", "diag"}；kept 含 watch（开发期口径），
    strict 下 watch 并入 blocked。weak 是「有限对口」：命中保留显示，但名次沉到
    队尾。2026-11-18 用户拍板：**都得复检**——半导体公司的「研发工程师」对生物
    学生，公告里有、意向也有，主营对不上就是匹配不上；公告自证（name_dir）与
    通用职能白名单都不再是免检金牌。kw/kw_fields 都空 → 闸门不咬人，全部放行。
    """
    pairs = list(real_pairs)
    rows = diag if diag is not None else []
    if not kw and not kw_fields:
        return {"kept": pairs, "blocked": [], "watch": [], "weak": [], "diag": rows}
    dev = (policy or os.environ.get("FAIR_RECHECK_POLICY") or "dev").lower() != "strict"
    kept, watch, blocked, weak = [], [], [], []
    for pair in pairs:
        t = str((pair[0] if isinstance(pair, (list, tuple)) else pair) or "")
        it = str((pair[1] if isinstance(pair, (list, tuple)) else "") or "")
        # 有行业码就先查表定论（2026-11-18 收紧）：core 直放 / edge 算有限对口
        # （weak：命中保留、沉队尾）/ out 不算命中——岗位名自带方向、通用职能
        # 白名单都救不回来。
        v = table_verdict(major, it, code) if code else None
        if v == ind_code.FIT_CORE:
            kept.append(pair)
            continue
        if v == ind_code.FIT_EDGE:
            weak.append(pair)
            continue
        if v == ind_code.FIT_OUT:
            rows.append({"job": t, "intent": it, "code": "code_out",
                         "label": DIAG_TEXT["code_out"], "verdict": "blocked",
                         "detail": ("这家按行业码是「%s」，与你的方向不是一条线（不是没查到主营，"
                                    "是查到了也对不上）；公告里就算真有这个岗，也匹配不上"
                                    % _code_name(code)),
                         "biz": _clean_biz(biz_text)[:60],
                         "name": (parts or {}).get("name") or ""})
            blocked.append(pair)
            continue
        # 没打码（新进库还没跑 backfill）→ 退回关键词通路：主营强词放行；
        # 通用职能岗、岗位名自带方向保留但算有限对口——没验过主营，不装通过。
        pf = pair_fields(it, major) or set(kw_fields or ())
        if _GENERIC_JOB.search(it) or _GENERIC_JOB.search(t):
            weak.append(pair)
            rows.append({"job": t, "intent": it, "code": "code_edge",
                         "label": DIAG_TEXT["code_edge"], "verdict": "keep_weak",
                         "detail": ("「%s」属通用职能岗（哪家公司都设），但这家没打行业码、"
                                    "主营没法验证：命中保留，名次排到队尾" % t),
                         "biz": _clean_biz(biz_text)[:60],
                         "name": (parts or {}).get("name") or ""})
            continue
        if biz_hit(pf, biz_text):
            kept.append(pair)
            continue
        d = diagnose(t, it, biz_text, major, kw_fields, parts)
        if d["code"] == "name_dir":
            # 岗位名自带方向（「材料研发工程师」）但主营没验到、又没有行业码：
            # 保留命中但算有限对口，不冒充「主营复核通过」。
            weak.append(pair)
            rows.append({"job": t, "intent": it, "code": "code_edge",
                         "label": DIAG_TEXT["code_edge"], "verdict": "keep_weak",
                         "detail": ("岗位名自带方向（%s），但主营没验到、这家也没打行业码："
                                    "命中保留，名次排到队尾" % t),
                         "biz": _clean_biz(biz_text)[:60],
                         "name": (parts or {}).get("name") or ""})
            continue
        is_block = bool(d["drop"]) or not dev
        rows.append({"job": t, "intent": it, "code": d["code"], "label": d["label"],
                     "verdict": "blocked" if is_block else "watch",
                     "detail": d["detail"], "biz": _clean_biz(biz_text)[:60],
                     "name": (parts or {}).get("name") or ""})
        if is_block:
            blocked.append(pair)
        else:
            watch.append(pair)
            kept.append(pair)
    return {"kept": kept, "blocked": blocked, "watch": watch, "weak": weak,
            "diag": rows}


def filter_real(real_pairs, kw, kw_fields, biz_text, major="", parts=None, policy=None):
    """兼容旧调用的两元组版本：返回 (kept（含待补与有限对口）, blocked)。

    新代码请直接用 recheck_real —— 它多返回 weak（有限对口：保留显示、沉队尾）、
    watch 与诊断，报告要靠它们说清「为什么」。
    """
    r = recheck_real(real_pairs, kw, kw_fields, biz_text, major=major,
                     parts=parts, policy=policy)
    return r["kept"] + r["weak"], r["blocked"]


def _code_families(code):
    """行业码 → 以该码为核心就业方向的专业族集合（PRO_MAP 反查）。

    经历岗复检用：实习公司判出行业码 ec 后，把「ec 的核心专业族」当学生在这段
    经历里的「专业族」，再与企业行业码查同一张 fit 表——互联网实习推出的研发
    岗配化工企业的研发岗，fit(计算机族, chem) = out，拦；化工实习配化工企业
    则 core，放。
    """
    code = ind_code.normalize(code)
    return {fam for fam, m in ind_code.PRO_MAP.items()
            if (m or {}).get(code) == ind_code.FIT_CORE}


def exp_recheck(exp_pairs, meta, company_code):
    """经历岗复检（2026-11-18 用户二次拍板）：轴不在专业，在实习。

    实习/竞赛带来的岗与专业无关是正常的（文科生做供应链实习），所以不按
    「专业 × 意向」判；改按「实习企业行业 × 本家企业行业」判——化工企业的
    研发岗，配互联网公司实习出来的研发，同是研发岗也匹配不上，拦；配化工
    公司实习出来的研发，放。

    规则（meta = fair_bridge.exp_recheck_meta 的产物）：
      ① 通用职能岗（数据分析这类哪家都设）→ 不拦；
      ② 实习文本看不出企业/细分行业（没写主营，或竞赛没有企业）→ 不刷，
         放行——拿空判据否决经历，等于把采集的锅算到学生头上（采集端已
         提示补「企业主营业务或细分行业」）；
      ③ 两边行业 fit = core → 放行；edge → weak（保留显示、沉队尾）；
      ④ fit = out → 拦，不算命中（诊断记 exp_out，报告写清两边行业）。

    meta 里没有该岗（映射表没认出来）→ 按②放行。
    返回 {"kept", "weak", "blocked", "diag"}。
    """
    kept, weak, blocked, rows = [], [], [], []
    cc = str(company_code or "").strip() or None
    for pair in exp_pairs or []:
        t = str((pair[0] if isinstance(pair, (list, tuple)) else pair) or "")
        it = str((pair[1] if isinstance(pair, (list, tuple)) else "") or "")
        me = (meta or {}).get(t) or (meta or {}).get(it) or {}
        if _GENERIC_JOB.search(it) or _GENERIC_JOB.search(t) or me.get("generic"):
            kept.append((t, it))
            continue
        ec = me.get("code")
        if not ec or not cc:
            # 实习行业判不出，或这家企业没打码：复检不刷，放行
            kept.append((t, it))
            continue
        fams = _code_families(ec)
        v = ind_code.fit(fams, cc) if fams else None
        if v in (ind_code.FIT_CORE, None):
            kept.append((t, it))
        elif v == ind_code.FIT_EDGE:
            weak.append((t, it))
            rows.append({"job": t, "intent": it, "code": "code_edge",
                         "label": DIAG_TEXT["code_edge"], "verdict": "keep_weak",
                         "detail": ("你实习的公司属「%s」，这家是「%s」，只算相邻行业："
                                    "这条经历岗保留显示，名次排到队尾"
                                    % (_code_name(ec), _code_name(cc))),
                         "biz": "", "name": ""})
        else:
            blocked.append((t, it))
            rows.append({"job": t, "intent": it, "code": "exp_out",
                         "label": "实习行业与这家对不上", "verdict": "blocked",
                         "detail": ("同是「%s」，但你实习的公司属「%s」，这家是「%s」——"
                                    "行业对不上，这条不算命中；现场确认是否接受跨行背景"
                                    % (t, _code_name(ec), _code_name(cc))),
                         "biz": "", "name": ""})
    return {"kept": kept, "weak": weak, "blocked": blocked, "diag": rows}


def filter_infer(infer_pairs, kw_fields, biz_text, major="", diag=None, name="",
                 code=None, soft=None):
    """推断命中复检：岗位名是猜的不能自证，主营文本（或行业码）必须容纳方向。

    推断命中本来就是「猜的」，缺依据就不猜 —— 这里保持严格（主营为空/纯
    「未知」一律不保留），与 recheck_real 的「开发期不直接毙企业」不矛盾：
    那是不删企业，这只是不标推断对。诊断照样记下来，好知道有多少推断是
    因为主营没采到 / 词表没覆盖才没标（那些是真该补的数据）。
    复检按意向对各算各的方向集（2026-11-17）：销售意向放行商贸类主营，
    不再连带给其他意向放行化学分析岗。

    2026-11-17 起有行业码就先查表：core 直接标，edge/out 不标（推断岗本就
    是猜的，行业码只沾边就不再往上加一对猜测）。code 为 None 走旧关键词口径。

    2026-11-18 修正 edge 口径：edge（有限对口）不再丢，传了 `soft` 就收进去，
    由调用方以「推断命中·有限对口」展示并沉到队尾——不标岗位名等于让学生
    无从判断这家为什么在榜上。out（方向不交叉）仍不标。
    """
    pairs = list(infer_pairs)
    rows = diag if diag is not None else []
    if not kw_fields:
        return pairs
    biz = _clean_biz(biz_text)
    if not biz:
        # 主营没采到 → 不标推断对（猜的岗没有业务依据），但记下来：这就是
        # 「为什么这条推断没标」的答案，也是该补采集的地方。
        # 例外：行业码判 core 的企业——码是建库时打好的，不依赖这段主营文本，
        # 已经在库里的结论不必因为文案没采到就作废。
        keep_core = []
        for p in pairs:
            it = str((p[1] if isinstance(p, (list, tuple)) else "") or "")
            if code and table_verdict(major, it, code) == ind_code.FIT_CORE:
                keep_core.append(p)
                continue
            rows.append({"job": str((p[0] if isinstance(p, (list, tuple)) else "") or ""),
                         "intent": it,
                         "code": "biz_missing", "label": DIAG_TEXT["biz_missing"],
                         "verdict": "drop_infer",
                         "detail": "这家主营没采到，推断岗没有业务依据、不标推断对", "biz": "",
                         "name": name})
        return keep_core
    out = []
    for p in pairs:
        it = str((p[1] if isinstance(p, (list, tuple)) else "") or "")
        v = table_verdict(major, it, code) if code else None
        if v == ind_code.FIT_CORE:
            out.append(p)
            continue
        if v in (ind_code.FIT_EDGE, ind_code.FIT_OUT):
            # 有限对口（edge）：不再静默丢弃。推断对是学生唯一能看见的
            # 「凭什么排这家」的线索，藏起来就只剩「推断待核实」四个字，
            # 没法定位判断依据（2026-11-18 用户明确要求）。名次该沉照沉，
            # 依据照写。out（方向不交叉）仍然不标：那不是「有限对口」。
            if v == ind_code.FIT_EDGE and soft is not None:
                soft.append(p)
            continue
        if biz_hit(pair_fields(it, major) or kw_fields, biz):
            out.append(p)
            continue
        if diag is not None:
            d = diagnose("", it, biz, major, kw_fields)
            rows.append({"job": str((p[0] if isinstance(p, (list, tuple)) else "") or ""),
                         "intent": it, "code": d["code"], "label": d["label"],
                         "verdict": "drop_infer", "detail": d["detail"],
                         "biz": biz[:60], "name": name})
    return out


def blocked_why(job: str, intent: str, biz_text: str, major: str, intended,
                code=None) -> str:
    """降级理由：主营是做什么的 vs 学生方向是什么，一句话讲清为什么对不上。

    有行业码时用码说话（「这家是机械装备，跟你只是相邻方向」），比再念一遍
    主营文本更好懂 —— 学生不需要读企业的经营范围。
    """
    biz = _clean_biz(biz_text)
    head = (biz[:24] + "…") if len(biz) > 24 else (biz or "主营未知")
    dom = str(major or "").strip()
    its = [str(x or "").strip() for x in (intended or []) if str(x or "").strip()]
    if dom:
        dom += "专业"
    if its:
        dom += ("选的" + "、".join(its[:2])) if dom else ("、".join(its[:2]))
    if code:
        v = table_verdict(major, intent, code)
        if v == ind_code.FIT_EDGE:
            return ("「%s」与你的「%s」方向相邻但不对口：这家是「%s」，通用职能岗会"
                    "放行，这个领域岗不在其列。想投就现场问一句培养方向。"
                    % (job, intent, _code_name(code)))
        if v == ind_code.FIT_OUT:
            return ("「%s」与你的「%s」只是字面对得上：这家是「%s」，与你%s的方向"
                    "不搭。是否按你的方向培养，现场问一句。"
                    % (job, intent, _code_name(code), dom or "的"))
    return ("「%s」与你的「%s」只是字面对得上：这家主营是「%s」，与你%s的方向对不上。"
            "是否按你的方向培养，现场问一句。" % (job, intent, head, dom or "的"))
