# -*- coding: utf-8 -*-
"""匹配端 · 岗位语义符合度（大模型推断）。

学生在采集端勾选的 intended_jobs 与企业校招公告里的真实岗位名往往字面不同，
旧的 matchIntent 只做 22 个关键词的字面交集，导致报告里显示的「命中岗位」
和学生勾选的岗位看起来没关系。

这里在 fair_bridge.build() 时用 GLM-4-flash 判断「企业岗位名」与「意向岗位」
是否属于同一类校招工作，把结果写进 payload 顶层 `intent_semantics`，前端据此
显示「语义命中：企业岗位 X ↔ 你选的 Y」。

- 每个意向岗位单独一次短调用（只让它从清单里挑岗位名），输出小、不易超时。
- 结果按 (意向岗位组合 + 岗位名集合) 缓存，避免每次 build 都调模型。
- 模型未配置 / 超时 / 解析失败 -> 沿用上次成功判定；仍没有则返回 {}，
  前端自动退回原有字面/关键词匹配，绝不编造。
- 只做「岗位语义」判断，不改企业事实字段，也不碰 calcOne 打分。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request

import cache_guard
import job_level

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "intent_semantics_cache.json")
# 按需补判用的小缓存：与主缓存分开，避免前台临时勾的意向把构建期的整份映射顶掉。
LIVE_CACHE = os.path.join(HERE, "data", "intent_semantics_live.json")

PROMPT = """你是校招岗位语义匹配助手。
学生意向岗位：{intent}
下面是本次双选会企业校招公告里出现的岗位名（去重清单）：{titles}

请从清单里挑出与「{intent}」属于同一类校招工作、应届生可以同样投递的岗位名。

要求：
1. 只能写清单里出现过的原词，不要造新岗位名；最多 25 个，按相符程度从高到低排序；同一个岗位不要重复输出。
2. **先扫描整份清单**：凡是属于同一职能的岗位都要挑出来，不要只挑最先看到的几个；漏掉一个明确同类比多挑一个模糊岗位更糟。
3. 判定标准：岗位名是「{intent}」的另一种叫法（同义、简称），或日常工作职责与「{intent}」基本相同。
   不看学生学过哪些课程、具备哪些能力。仅在名字里共享一个通用词（专员 / 管培生 / 工程师 / 经理 / 助理 / 主管）不算同类。
   **方向词不是通用词**：不带方向的意向（如「测试工程师」「供应链专员」）遇到带方向限定的同类岗
   （电气测试 / 光学测试 / 机械测试 / 硬件测试），可以算同类，但它们是该职能的一个细分、
   不是同一个岗位的别名——reason 必须按第 6 条如实写出方向差异。
4. **必须算同类**：
   a) 同义简称：「人事 / 人资 / 人力资源」同义；「会计 / 财务 / 审计 / 税务 / 资金」属同一职能；「采购 / 寻源 / 供应链 / 计划 / 物流 / 仓储 / 关务」属同一职能链；「法务 / 法律 / 合规」同义；「市场 / 营销 / 品牌 / 推广」属同一职能。
   b) **同一职能内，档位要到得着才算**：这是应届校招，学生投的是「进得去的那一档」。
      - 可投：管培生 / 培训生 / 校招生 / 储备干部（储干）/ 专员 / 助理 / 初级 / 专工 / 专责 / 实习生 / 见习生，以及没有档位标记的常规岗（工程师、分析师、运营、会计、研究员、设计师……）。
      - **不可投（务必排除）**：主管 / 资深 / 高级 / 组长 / 科长 / 主任 / 主办 / 督导，以及经理 / 总监 / 负责人 / 首席 / 专家 / 合伙人 / 董事 / 总裁。
        例：「供应链主管」不是「供应链管培生」的同类入口，「资深有机合成研究员」「高级分析研究员」不是「有机合成研究员」的同类入口，「数据加工经理」「驻场运营经理」不是「数据分析岗」的同类入口。
      - 一条公告并列多个档位（「工艺工程师/主管/经理」「营销员/营销主管」）时，只要其中有可投的那一档就算同类。
      - 注意必须先是同一职能——「IT 数字化管培生」「售前管培生」「教学管培生」「营销管培生」与「供应链管培生」只是都叫管培生，方向完全不同，不算同类。
   c) **细分技术方向**：当意向本身是软件开发 / 研发类岗位时，其细分方向算同类，包括「前端 / 后端 / 嵌入式 / 全栈 / 服务端 / 客户端 / Java / C++ / Python / PHP / Unity3d / 大模型应用 / 低代码」。
   d) 中小学教师：岗位名列出多个学科且**包含**意向学科，就算同类（如「初中学习机教师(语文、数学、英语)」对「中学语文教师」算同类）。
   e) **大类岗位名不能反推细分意向**：清单里的「管培生 / 培训生 / 储备干部 / 专员 / 助理 /
      研究员 / 分析师」如果不带业务方向，就不是「{intent}」的同类——「{intent}」是「管培生」
      不代表「管培生」就是「{intent}」。制造业企业的管培生不一定按供应链培养。
      只有当岗位名本身写清了这个细分方向（如「供应链管培生」「量化研究员」「数据分析岗」）
      才算同类。只有大类名的岗位一律不要选，交由现场提问。
5. **明确不算同类**，务必排除：
   - 档位比「{intent}」高、应届投不进的：主管 / 资深 / 高级 / 组长 / 科长 / 主任，以及经理 / 总监 / 负责人 / 首席 / 专家（见 4b）；
   - 测试 / 运维 / DBA / 数据库内核 / 集成实施 / 售后服务岗；工艺 / 设备 / 生产 / 质检 / 车间 / 维修岗；
   - 销售类不算技术、专业、职能类意向；财务 / 会计 / 审计不算供应链、数据、人力、市场类意向；
   - 「战略管培生」不算「供应链管培生」；「-工艺方向 / -设备方向」不算「-运营方向」；
   - 教师须学科一致：意向语文时，只教数学 / 英语 / 化学 / 历史 / 体育 / 信息技术 / 理科竞赛的不算；
   - 专业名、学科名不是岗位（「电气工程及其自动化」「机械类」「电气类」）；招聘批次名不是岗位（「2027 届校园招聘」）；
   - 产品 / 产品经理不算临床医学、法务、人力资源这类专业岗；
   - 运营 / 企宣 / 公关 / 会展不算「市场营销专员」；质量 / 体系 / 解决方案 / 技术支持不算设计、研发岗；
   - 不同专业的设计岗不算同类：「机械设计工程师」不算「模拟 IC 设计 / 光学设计 / 逻辑设计 / 电磁设计」。
6. 对每个挑中的岗位写一句 reason。**先判断关系，再选句式，不许一律套「另一种说法」**：
   - 真同义 / 简称 / 只差档位用词（「测试工程师」对「初级测试工程师」、「人事专员」对「人力资源专员」）：
     写「「XX」即「{intent}」的另一种说法」。
   - **同一职能但方向不同**（「电气测试工程师」「光学测试工程师」「机械测试工程师」「硬件测试工程师」
     对「测试工程师」；「新能源开发工程师」对「开发工程师」）：**严禁**写「另一种说法」，
     必须以「大模型判断二者类似工作内容：」开头，并点明方向差异。例：
     「大模型判断二者类似工作内容：同属测试职能，但该岗方向是电气 / 整机测试，与不带方向的测试岗不是同一细分」
   - 字面差距大但职责相同：同样以「大模型判断二者类似工作内容：」开头。
7. 不要输出任何解释文字。严格输出 JSON：{{"titles":[{{"title":"岗位名","reason":"..."}}]}}
"""


def _load_env():
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _candidate_titles(titles, intended, limit=240):
    """语义无关的初筛：只把「可能和意向岗位沾边」的岗位名送进模型，压短 prompt。

    纯字符粗筛，不做语义判断；宁可多留，也不把真正相关的岗位名筛掉。
    """
    core = ["管培", "培训生", "专员", "分析师", "工程师", "顾问", "助理", "储备",
            "数据", "分析", "供应链", "计划", "采购", "物流", "运营", "项目",
            "商业", "经营", "风控", "咨询", "市场", "财务", "产品", "管理",
            "支持", "研究", "策略", "质量", "交付", "实施"]
    keys = set(core)
    for it in intended:
        for i in range(max(0, len(it) - 1)):
            keys.add(it[i:i + 2])
    scored = []
    for t in titles:
        n = sum(1 for k in keys if k in t)
        if n:
            scored.append((n, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return sorted(t for _, t in scored[:limit])


# 明显的「工程 / 研发」职责词：企业岗位名含这些词、而意向岗位不含时，
# 即使共享「数据」「供应链」等字也不能算同类（防模型按关键词凑数）。
ROLE_CONFLICT = (
    "内核", "测试", "DBA", "运维", "研发", "嵌入式", "前端", "后端",
    "驱动", "固件", "编译", "工艺", "设备", "生产", "质检", "研究员",
    "开发工程师", "集成工程师", "服务工程师",
)


# 不带任何专业 / 职能方向的泛岗位名。它们不能算作「供应链管培生」
# 「采购寻源专员」这类带方向意向的同类 —— 除非意向本身就是泛词。
GENERIC_JOB = {
    "管培生", "培训生", "管理培训生", "职能管培生", "运营培训生",
    "储备干部", "校招生", "专员", "助理", "实习生", "干部", "管培岗",
}

# 「大类岗位名」：名字里不带任何业务方向，只说了层级 / 身份。
# 「供应链管培生」是「管培生」，但「管培生」不是「供应链管培生」——
# 小类可以进大类，大类不能反推小类。制造业企业的管培生不一定按供应链培养，
# 所以这类只能进「现场问一句」，不能算对口证据。
# （例外：公司业务本身就是那个细分时，大类岗才可按对口算，见 biz_ok）
BARE_EXACT = {
    "管培生", "管培岗", "管理培训生", "培训生", "校招生", "储备干部", "储干",
    "干部", "职能管培生", "专员", "助理", "实习生", "见习生", "研究员",
    "分析师", "工程师", "经理", "主管", "主任", "组长", "业务员", "职员",
    "员工", "顾问", "人员", "人才",
}
_FILLER_EXTRA = {"岗位", "岗", "人员", "类", "方向", "相关", "储备", "管理"}
_FILLER = []   # 懒构建：GEN_GRAMS 在本模块靠后定义，导入期不能直接引用


def _generic_filler():
    global _FILLER
    if not _FILLER:
        # 不抹 GEN_GRAMS：「培训专员 / 培训管培生」是培训条线的岗位名，
        # 抹掉「培训 / 专工 / 管理」后会被当成「只写了大类」的岗位。
        _FILLER = sorted(BARE_EXACT | _FILLER_EXTRA,
                         key=len, reverse=True)
    return _FILLER


def is_bare_generic(title):
    """岗位名是否「不带业务方向」——只有层级 / 身份词。

    判断方式：把层级词与通用词全部抹掉，看还剩不剩方向字。
    「管培生 / 储备干部 / 研究员 / 专员」→ 剩空 → 大类；
    「供应链管培生 / 量化研究员 / 商务专员 / 运营培训生」→ 剩方向字 → 不是大类。
    """
    # 这里不用 _norm/_syn：它们在本模块靠后定义，而 is_bare_generic 要在
    # 导入期就能被 _role_ok 调用。等价地自己做一次轻量归一。
    t = re.sub(r"[\s（）()\[\]【】、，,·—\-/_]+", "", str(title or "")).lower()
    if not t:
        return False
    if t in BARE_EXACT:
        return True
    rest = t
    for w in _generic_filler():
        rest = rest.replace(w, "")
    return len(rest) <= 1


def _dir_text(s):
    """抹掉层级 / 身份词后剩下的方向串。

    「算法工程师」→「算法」，「AI算法研究工程师」→「ai算法研究」，
    「供应链管培生」→「供应链」，「管培生」→ 空。
    级别词（初级 / 中级 / 高级 / 资深）是档位不是方向：「初级测试工程师」的
    方向就是「测试」，不能因为多了「初级」二字被当成比「测试工程师」更细分。
    """
    t = re.sub(r"[\s（）()\[\]【】、，,·—\-/_]+", "", str(s or "")).lower()
    if not t:
        return t
    for w in ("初级", "中级", "高级", "资深"):
        t = t.replace(w, "")
    for w in _generic_filler():
        t = t.replace(w, "")
    return t


def is_wider_job(title, intent):
    """岗位名是不是比意向「更宽一层」的类。

    「算法工程师」的方向只剩「算法」，「AI算法研究工程师」的方向是「AI算法研究」
    —— 前者是后者的上位类，不能当对口证据（小类能进大类，大类不能反推小类）。
    只在「岗位方向 ⊂ 意向方向」时成立；并列方向（机械设计 × 结构设计）不算更宽，
    保持原有口径。
    """
    a, b = _dir_text(title), _dir_text(intent)
    if not a or not b or a == b:
        return False
    return a in b and len(a) < len(b)


# 并列方向词：只放「同属一个大类、却是两条不同职业路径」的限定词。
# 换技术栈不算换方向（Java / Python / C++ / SQL 不在表内）；
# 学科方向（机械 / 结构 / 电气）也不在表内 —— 「机械设计工程师」和「结构设计工程师」
# 是同一岗位的两种写法，放进来会误伤。
DIR_WORDS = ("前端", "后端", "服务端", "客户端", "嵌入式", "上位机", "下位机", "固件",
             "移动端", "桌面", "驱动", "全栈", "低代码", "量化", "算法", "数据",
             "图像", "视觉", "图形", "游戏", "unity", "unreal", "大模型", "ai",
             "人工智能", "测试", "运维", "安全", "网络", "通信",
             "供应链", "计划", "采购", "物流", "仓储", "商业分析")
# 不带方向的通用词：抹干净后仍为空，才说明「这个意向 / 岗位没限定方向」。
DIR_NEUTRAL = ("开发", "研发", "软件", "程序", "工程师", "技术", "应用", "系统", "岗", "员")

# 方向层级：父类 → 它自己的下属细分。岗位方向全是意向父类的下属细分时算同一条线
# （供应链管培生 × 采购/物流/仓储 → 照旧算对口）。
# **计划不在里面**：计划岗在生产企业、核电、航天到处都是，不是供应链专属，
# 「计划管培生 × 供应链管培生」是并列方向，不算命中。
DIR_PARENTS = {"供应链": ("采购", "物流", "仓储")}


def _same_line(at, bt):
    """意向与岗位的方向词是否同属一条线（供应链家族）：

    一方是父类本身、另一方全是它的下属细分（供应链 × 采购）→ 同一条线；
    双方都是同一个父类下的兄弟细分（采购 × 物流/仓储）→ 也是同一条线。
    计划不在家族里：供应链 × 计划 仍是并列。
    """
    if not at or not bt:
        return False
    for kids in DIR_PARENTS.values():
        ks = set(kids) | set(DIR_PARENTS.keys())
        if set(at) <= ks and set(bt) <= ks:
            return True
    return False


def _dir_core(s):
    """抹到「只剩方向」的串，外加方向词集合。

    「开发工程师」→ 空（没限定方向）；「量化开发工程师」→「量化」；
    「后端开发」→「后端」；「电气测试工程师」→「电气测试」+ {电气? 测试}。
    """
    t = _dir_text(s)
    for w in DIR_NEUTRAL:
        t = t.replace(w, "")
    return t, frozenset(w for w in DIR_WORDS if w in t)


def pair_verdict(title, intent):
    """(岗位, 意向) 这一对能不能算命中的对口证据。

    "ok"  算：意向没限定方向（开发 / 管培生），任何细分方向都能进；
               岗位没限定方向（开发工程师 / 软件工程师），比意向更泛，也能投；
               岗位方向 ⊂ 意向方向（算法工程师 × AI算法研究工程师），小类进大类。
    "ask" 不算但值得问一句：同向但岗位更细（测试 → 电气测试 / 器件测试）。
    "no"  不成立：并列方向（后端 × 量化 / 前端 / 嵌入式）—— 同一个大类下的两条
          不同的路，不能说成「另一种说法」，也不能算对口。
    """
    a, at = _dir_core(intent)
    b, bt = _dir_core(title)
    if not a:                       # 意向没方向 → 收所有细分（管培生 → 供应链管培生）
        return "ok"
    if not b:                       # 岗位没方向 → 更泛的同类岗，可投
        return "ok"
    if b == a:
        return "ok"
    if b in a:                      # 岗位更泛：小类能进大类
        return "ok"
    if a in b:                      # 岗位更具体：同向但多了限定
        return "ask"
    if at and bt and not (at & bt):  # 两条并列的路径：后端 × 量化 / 前端 / 嵌入式
        if _same_line(at, bt):       # 采购/物流/仓储 ⊂ 供应链：同一条线的细分，照旧算
            return "ok"
        return "no"
    return "ok"


# 公司业务侧的职能线索：只用来判「这家公司是不是就做这个细分」。
# 与 FUNC_FAMILY 同一套 key，另补行业常用词。
BIZ_EXTRA = {
    "supply": ("物流", "仓储", "快递", "货运", "港口", "航运", "配送", "供应链管理"),
    "fin": ("证券", "基金", "银行", "保险", "期货", "资管", "私募", "信托", "金融"),
    "data": ("大数据", "人工智能", "智能科技", "算法", "数据服务", "信息技术"),
    "hr": ("人力资源服务", "人力资源"),
    "legal": ("律师事务所", "法律服务"),
    "mkt": ("广告", "公关", "传媒"),
    "trade": ("进出口", "国际贸易", "货代", "报关"),
    "quality": ("检测", "认证"),
    "media": ("文化传媒", "影视", "广告传媒"),
}


def biz_family(text):
    """从公司名 / 行业 / 主营范围里看出它到底做哪一行（职能族）。"""
    t = text or ""
    fam = _func_family(t)
    for f, kws in BIZ_EXTRA.items():
        if any(k in t for k in kws):
            fam.add(f)
    return fam


def biz_ok(intent, biz_text):
    """公司业务本身就是意向所在的细分时，才允许把大类岗位算作对口。

    例：东方嘉盛（供应链公司）的「管培生」可以算「供应链管培生」的对口证据；
    一家新材料公司的「管培生」不算，只能现场问一句。
    """
    fam = _func_family(intent)
    return bool(fam & biz_family(biz_text))


def _func_terms(title):
    """岗位名命中的职能标记集合（确定性，用于跨职能兜底，不依赖模型）。"""
    t = title or ""
    tags = set()
    if any(k in t for k in ("会计", "财务", "审计", "税务", "核算", "出纳")):
        tags.add("fin")
    if "战略" in t:
        tags.add("strat")
    if "数据库" in t:
        tags.add("db")
    if any(k in t for k in ("供应链", "物流", "采购", "寻源", "仓储", "关务", "计划")):
        tags.add("supply")
    if any(k in t for k in ("运营", "经营管理", "电商运营")):
        tags.add("ops")
    if any(k in t for k in ("数据分析", "商业分析", "量化", "分析", "数据")):
        tags.add("data")
    if any(k in t for k in ("工程师", "研发", "开发", "嵌入式", "前端", "后端", "测试", "算法")):
        if "data" not in tags:
            tags.add("eng")
    return tags


def _role_ok(intent, title, ignore_level=False):
    if title in GENERIC_JOB and intent not in GENERIC_JOB:
        return False
    # 小类可以进大类，大类不能反推小类：岗位名只写「管培生 / 研究员 / 专员」
    # 这种不带方向的大类名时，不能算成「供应链管培生 / 量化研究员 / 数据分析岗」
    # 的对口证据（制造业企业的管培生不一定是供应链方向）。
    # 例外（公司业务就是这个细分）在 align_jobs_detail 里用 biz 单独放行。
    if is_bare_generic(title) and not is_bare_generic(intent):
        return False
    # 专业名称 / 招聘批次名 / 专业类目名不是岗位（「电气工程及其自动化」「电气类」
    # 「2027 届校园招聘」「公务员」）
    if not job_level.is_job_title(title):
        return False
    # 学科：岗位列了多个学科且含意向学科 → 算同类；完全不含 → 拦。
    # （修「初中学习机教师(语文、数学、英语)」对「中学语文教师」的误杀）
    ts = {s for s in SUBJECTS if s in title}
    is_ = {s for s in SUBJECTS if s in intent}
    if is_ and ts and not (is_ & ts):
        return False
    # 销售岗不能算作非销售/非市场类意向（「临床&终端销售经理」≠ 临床医学专员）。
    # 但意向本身就是业务/销售型（外贸业务员、客户经理、商务拓展）时要放行——
    # 这类岗位名里没有「销售」二字，旧守卫会把 KA 销售管培生 全部误杀。
    if "销售" in title and not any(k in intent for k in SALES_INTENT_WORDS):
        return False
    # 产品岗不算专业 / 职能岗（「产品专员」≠「临床医学专员」）
    if any(w in title for w in PROD_WORDS) and not any(
            k in intent for k in ("产品", "市场", "营销", "运营")):
        return False
    # 数据类意向只收真的碰数据的岗位（化学分析工程师 / 质量分析员 不算）
    if "数据" in _syn(intent) and not any(w in _syn(title) for w in DATA_TITLE_WORDS):
        return False
    # 运营 / 企宣 / 公关不算市场类意向；但同职能族内的「-运营方向」要放行
    # （「供应链事业群管培生-供应链运营方向」不能被这条误杀）
    if any(w in title for w in OPS_WORDS) and "运营" not in intent \
       and not (_func_family(intent) & _func_family(title)):
        return False
    # 「运营」和「管培生」一样是个万能词：新媒体运营 / 电商运营 / 产品运营 是
    # 内容、店铺、产品三条不同的线，不是「运营管理培训生」的同类入口。
    # 只有意向本身就指向内容/电商方向时才放行（新媒体运营专员 × 新媒体运营）。
    if any(k in intent for k in ("运营",)) and not any(w in intent for w in OPS_CONTENT_WORDS) \
       and any(w in title for w in OPS_CONTENT_WORDS):
        return False
    # 跨专业设计岗：「机械设计工程师」不算「模拟IC / 光学 / 电磁 / 逻辑设计」。
    # 判据是「岗位是设计岗 + 名字里带意向没有的设计方向词」，不需要再查 title 有没有
    # 方向词（旧写法 `not (_DESIGN_FIELDS & _grams(title))` 恒为假，这条守卫从来没生效过）。
    if "设计" in title and any(f in title and f not in intent for f in DESIGN_FIELD_WORDS):
        return False
    # 跨职能硬错兜底：财务 / 战略 / 数据库 类岗位，意向不是该类时不算同类
    t_tags = _func_terms(title)
    i_tags = _func_terms(intent)
    if ("fin" in t_tags and "fin" not in i_tags) or \
       ("strat" in t_tags and "strat" not in i_tags) or \
       ("db" in t_tags and "db" not in i_tags):
        return False
    # 「运营培训生（综合柜员方向）」虽然字面共享「运营/培训」，但是银行柜面岗
    if "柜员" in title and "柜员" not in intent:
        return False
    # 校招没有总监岗；「销售总监（海外业务）」不能算「外贸业务员」的同类入口
    if "总监" in title and "总监" not in intent:
        return False
    # 仓库 / 仓管是物流现场岗，不能因为名字里带「外贸」就算外贸业务
    # （「外贸仓库科长」）。注意只能用「仓库/仓管」——「物流仓储储备人员」
    # 对采购寻源、供应链意向是正当同类，不能被这条误杀。
    if any(w in title for w in ("仓库", "仓管")) and not any(
            k in intent for k in ("仓", "物流", "供应链", "采购")):
        return False
    # 职责冲突词：细分方向词只在「软件/开发类意向」放行；
    # 「研发 / 开发工程师」这类泛研发岗名，只放给真正做工程设计的意向。
    soft = _is_soft_intent(intent)
    # 质量类意向的例外：岗位名自己写着「质量」时，测试 / 研究员 是质量岗的常规叫法
    # （「产品质量与测试工程师」「原料药质量研究员」都是 QA/QC，不该按职责词拦掉）。
    qual = ("质量" in intent) and ("质量" in title)
    eng_design = any(w in intent for w in ENG_DESIGN_WORDS)
    # 制造业方向词（生产 / 设备 / 工艺）在供应链语境下是正当同类：
    # 「生产计划」「供应链事业群管培生-设备方向」都属于供应链条线，
    # 但孤立的「设备工程师」「工艺工程师」仍按跨职能拦掉（下面 family 不相交）。
    supply_ctx = any(w in title for w in ("计划", "供应链", "物流", "采购", "PMC"))
    for k in ROLE_CONFLICT:
        if k in title and k not in intent:
            if soft and k in DEV_SUBS:
                continue
            if qual and k in ("测试", "研究员"):
                continue
            if k in ("生产", "设备", "工艺") and supply_ctx and (
                    _func_family(intent) & _func_family(title)):
                continue
            if k in ("研发", "开发工程师") and _is_eng_intent(intent):
                # 软件研发岗（数据开发 / AI / 低代码 / 量化…）不算机械、电气、质量类意向的同类
                if not soft and any(w in title for w in SOFT_TITLE_WORDS):
                    return False
                # 岗位名带别的设计方向（光学 / 电磁 / IC…）时也不放
                if any(f in title and f not in intent for f in DESIGN_FIELD_WORDS):
                    return False
                # 同一职能族，或意向本身是工程设计岗（机械设计 / 电气 / 结构…）→ 泛研发岗可投
                if (_func_family(intent) & _func_family(title)) or eng_design:
                    continue
            return False
    # 层级：同一职能内「主管 / 资深 / 经理」与「管培生 / 专员」是两档人。
    # 校招应届只认实习 / 校招初入 / 常规经验岗，主管以上不算对口（企业不删、
    # 不写「不招」，只标注层级不符，见 job_level.py 模块注释）。
    if not ignore_level:
        ok_lv, _ = job_level.check(intent, title)
        if not ok_lv:
            return False
    return True


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def _call(cfg, prompt, timeout):
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.2}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return _parse_json(raw["choices"][0]["message"]["content"])


# 「同一职能、换种叫法」的常见族。只用于救「字面零重合但确实是同类」的漏判，
# 例如「采购寻源专员」↔「供应链事业群管培生-供应链运营方向」。
# 不把 eng / mkt / hr 放进族表——那几类字面差异不大的误判风险高。
FUNC_FAMILY = {
    # 「计划」不能裸用：「启明星计划竞赛教练」会因此被当成供应链岗
    "supply": ("供应链", "物流", "采购", "寻源", "仓储", "关务", "库存", "配送",
               "物资", "PMC", "备件", "生产计划", "物料计划", "计划专员", "计划管培",
               "计划助理", "综合计划", "计划主管", "计划岗"),
    "data": ("数据分析", "商业分析", "经营分析", "业务分析", "策略分析", "分析岗",
             "量化", "BI", "数据科学", "统计"),
    "hr": ("人力资源", "人力", "人资", "人事", "招聘", "薪酬", "员工关系",
           "培训专员", "培训岗", "培训管培", "培训主管", "培训经理", "培训助理"),
    "fin": ("财务", "会计", "审计", "税务", "核算", "出纳", "资金"),
    "legal": ("法务", "法律", "合规", "知识产权", "诉讼"),
    "mkt": ("市场", "营销", "品牌", "推广"),
    # 新媒体不是「市场」，是内容/流量运营；漏了这一族，直播、短视频、公众号
    # 这些明明同类的岗位会被字面判成不相关。
    "media": ("新媒体", "短视频", "直播", "内容运营", "内容策划", "公众号", "小红书",
              "抖音", "B站", "视频号", "社媒", "社交媒体", "文案"),
    # 质量岗自成一族：SQE / 体系 / 客诉 / 质保 / 质检 都是同一职能，
    # 不靠「工程」这个通用 2 字词去凑。
    "quality": ("质量", "品质", "品控", "质检", "质保", "体系", "客诉"),
    # 外贸不是「市场」也不是「供应链」，是一条独立业务链：业务员 / 单证 / 报关 / 货代。
    # 缺了这一族，「国际市场营销」这种明显同类的入口会被字面判成不相关。
    "trade": ("外贸", "国际贸易", "进出口", "海外业务", "海外销售", "国际业务",
              "海外市场", "国际市场营销", "单证", "报关", "货代", "船代", "外销"),
}

# 写理由时允许作为「核心方向」出现的词：族关键词 + 几个动词性职能词。
# 只用 2 字滑窗去比对会产出「营管」这种切词噪声，必须过一层白名单。
MEANING_WORDS = {w for kws in FUNC_FAMILY.values() for w in kws} | {
    "运营", "计划", "生产", "项目", "行政", "品质", "采购", "物流", "数据"}

# 职能族的中文名：写「同属一条线」的理由时用得上（比「同属相关类岗位」具体）。
FAMILY_LABEL = {
    "supply": "供应链", "data": "数据", "hr": "人力资源", "fin": "财务",
    "legal": "法务", "mkt": "市场", "media": "内容运营", "quality": "质量",
    "trade": "国际贸易",
}

# 意向本身就属于「跑业务 / 谈客户」的销售型岗位：判断「销售 xxx」能否算同类时，
# 不能只看意向名里有没有「销售」二字（外贸业务员、客户经理、商务拓展 都没有）。
SALES_INTENT_WORDS = ("销售", "营销", "市场", "外贸", "业务", "客户", "渠道",
                      "商务", "贸易", "BD", "拓展")

# 技术岗词：族相交放行时，这些词必须「意向本身也有」才放，
# 否则「物流工程师」「PMC 工程师」会被错算成「供应链管培生」。
TECH_WORDS = ("工程师", "研发", "开发", "算法", "研究员", "测试", "工艺", "设备", "质检")

# 「数据」类意向必须真的做数据：化学分析 / 力学分析 / 金相分析 / 质量分析 是实验室
# 检测岗，只共享「分析」两个字，与数据条线毫无关系。
DATA_TITLE_WORDS = ("数据", "商业分析", "量化", "统计", "BI", "策略", "经营分析",
                    "算法", "机器学习", "数仓", "报表", "模型")

# 学科词：双选会里「教语文」和「教化学」是两个完全不同的岗位，
# 模型（尤其 DeepSeek）会把同类学科都挑进来，必须按学科互斥硬拦。
SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史",
            "地理", "体育", "音乐", "美术", "信息技术", "理科", "文科", "科学",
            "心理健康", "道德与法治")

# 产品岗：不算临床医学、法务、人力资源这类专业岗（修「产品专员」↔「临床医学专员」）
PROD_WORDS = ("产品经理", "产品专员", "产品工程师", "产品岗", "产品规划", "产品运营")
# 运营 / 企宣 / 公关：不算「市场营销专员」等职能岗
OPS_WORDS = ("运营", "企宣", "公关", "会展", "会议策划")
# 内容 / 电商 / 产品三条运营线，与「运营管理」不是同类入口（详见 _role_ok 注释）
OPS_CONTENT_WORDS = ("新媒体", "电商", "直播", "短视频", "内容运营", "内容策划",
                     "社群", "用户运营", "店铺", "产品运营", "公众号", "小红书", "抖音")
# 只有软件 / 开发类意向才允许「前端 / 后端 / 嵌入式 …」这些细分方向词放行
SOFT_INTENT_WORDS = ("开发", "研发", "软件", "程序", "算法")
DEV_SUBS = ("前端", "后端", "嵌入式", "驱动", "固件", "编译", "全栈", "服务端", "客户端",
            "C++", "Java", "Python", "PHP", "研发", "开发工程师", "Unity3d", "C#")
DEV_TITLE_HINTS = ("前端", "后端", "嵌入式", "全栈", "服务端", "客户端", "Java", "C++", "C#",
                   "Python", "PHP", "Unity3d", "大模型应用", "低代码", "鸿蒙", "Android",
                   "IOS", "上位机", "软件工程师")
GEN_GRAMS = {"专员", "管培", "培生", "培训", "训生", "管理", "助理", "储干",
             "干部", "工程", "经理", "主管", "储备",
             # 「分析」不携带方向：策略分析岗 与 分析仪器工程师 / 质量分析员 /
             # 化学分析 共享的只是「分析」二字，一个是商业分析，一个是设备与化验。
             # 真属于分析类的（经营分析 / 业务分析 / 策略分析）靠 data 族兜住。
             "分析",
             # 「研究」同样不携带方向：行业研究岗 ≠ AI算法研究工程师 /
             # 有机合成研究员 / 药物分析研究员。同名岗位靠字面同名那一条命中，
             # 不受影响；真正的同类（行业研究员）靠「行业」二字命中。
             "研究",
             # 「专工」不携带方向（安全质保助理专工）。
             # 「程师」不能进：前端/后端/嵌入式工程师 与「软件开发工程师」的
             # 唯一字面纽带就是「程师」；「业务」也不能进：外贸业务员 与
             # 业务经理 / 业务管培生 的纽带就是「业务」。
             "专工"}

# 岗位名的「尾部后缀字」：跨词边界的 2-gram 常以这些字收尾（理岗 / 源专 / 究员）。
# 共享片段只有这类字收尾时，不算同职能证据。
BOUNDARY_TAIL = set("岗员师专士长工部科组队站店厂所司局院校")


# 明显是「专业名称」而非岗位名称的写法（如「电气工程及其自动化」被当岗位挑中）。
def _is_major_name(title):
    t = (title or "").strip()
    return t.endswith("及其自动化") or t.endswith("专业") or "及其自动化" in t


# 「2027 届校园招聘」「2027届秋季校园招聘」是招聘批次名，不是岗位名。
def _is_campaign_name(title):
    t = (title or "").strip()
    return "校园招聘" in t or bool(re.search(r"\d{4}\s*届", t))


def _is_soft_intent(intent):
    return any(k in (intent or "") for k in SOFT_INTENT_WORDS)


# 抹掉后为空 = 意向只说了「软件 / 开发」，没限定方向。
_SOFT_WIDE = ("软件开发工程师", "软件研发工程师", "软件工程师", "软件开发", "软件研发",
              "研发工程师", "开发工程师", "程序开发工程师", "程序开发", "程序员",
              "工程师", "软件", "开发", "研发", "程序")


def _soft_broad(intent):
    """意向是不是「只说了软件、没限定方向」的笼统名。

    「软件开发工程师」可以投前端 / 后端 / 嵌入式 / Java —— 细分方向进大类。
    「算法工程师」不是笼统名：算法与前端 / 后端是并列方向，不是包含关系，
    不能因为都带「工程师」就算同类（小类能进大类，大类不能反推小类，反过来也不行）。
    """
    if not _is_soft_intent(intent):
        return False
    s = str(intent or "")
    for w in _SOFT_WIDE:
        s = s.replace(w, "")
    return not s.strip()


def _is_eng_intent(intent):
    """意向本身是不是工程 / 设计岗（决定「研发 / 开发工程师」能否算同类）。"""
    return any(k in (intent or "") for k in ("设计", "工程师", "研发", "开发", "机械", "电气"))


# 跨专业设计方向词：机械设计不吃 IC / 光学 / 电磁 这类设计岗
DESIGN_FIELD_WORDS = ("IC", "芯片", "光学", "光机", "电磁", "逻辑", "物理", "数字",
                      "模拟", "射频", "FPGA", "版图", "PCB")
# 之前这里写的是空集合，导致「机械设计工程师」对「模拟 IC 设计工程师」的拦截从未生效；
# 用同一份方向词做 2 字组合命中判断（FPGA / PCB 这类 3 字以上词由下面的整词判断兜底）。
_DESIGN_FIELDS = set(DESIGN_FIELD_WORDS)
# 真正做工程设计的意向：泛「研发工程师 / 开发工程师」岗名可以算同类（机械设计、电气、结构）。
# 「质量工程师 / 人力资源专员」这类职能岗不算——它们判断同类要看岗位名里的具体方向。
ENG_DESIGN_WORDS = ("设计", "机械", "电气", "电子", "结构", "自动化", "控制", "工艺",
                    "软件", "算法", "研发", "开发")
# 软件研发岗专用词：出现它们说明是写代码的岗，机械 / 电气 / 质量类意向不该收。
SOFT_TITLE_WORDS = ("数据开发", "AI", "大模型", "智能体", "低代码", "量化", "前端", "后端",
                    "全栈", "Java", "Python", "C++", "算法", "软件")


def _grams(s):
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


# 同义写法归一：只放「同义、且不会把别的职能拉进来」的替换。
# 「营运」是「运营」的行业写法（营运管培生 / 营运中台管培生），
# 不归一的话这类岗位对「运营管理培训生」一个字都对不上。
SYNONYMS = (("营运", "运营"),)


def _syn(s):
    s = str(s or "")
    for a, b in SYNONYMS:
        s = s.replace(a, b)
    return s


def _func_family(text):
    t = text or ""
    return {f for f, kws in FUNC_FAMILY.items() if any(k in t for k in kws)}


def _share_core(intent, title):
    """两道：① 共享 2 字核心词（字面同义）；② 或同属一个职能族且不是跨到技术岗。

    ② 是确定性兜底，专门修 ① 的字面盲区：采购寻源专员 与 供应链管培生 一个字都不重合，
    但确属同一职能。族相交只在「业务岗」上生效，技术岗词仍要求意向自身含该词，
    因此不会把「数据开发工程师」「药物分析研究员」（各自带 开发/研究员）放进来。
    """
    intent, title = _syn(intent), _syn(title)
    grams = _grams(intent)
    # 只共享「管培 / 专员 / 工程 / 经理」这类通用词不算同类，
    # 否则「IT 数字化管培生」「营销管培生」都会算进「供应链管培生」。
    #
    # 另外：2 字窗口会跨词边界造出假共享——「风险管理岗」的「理岗」正好落在
    # 「施工管理岗」尾部，「采购寻源专员」的「源专」正好落在「人力资源专员」中间，
    # 于是「风险管理岗 = 施工管理岗」「采购寻源专员 = 人力资源专员」。
    # 只共享到「以岗位后缀字结尾」的片段（岗/员/师/专/长/工…）时不算共享。
    shared = [g for g in (grams - GEN_GRAMS) if g in title]
    if any(g[-1] not in BOUNDARY_TAIL for g in shared):
        return True
    if intent in GENERIC_JOB and any(g in title for g in grams):
        return True
    # 意向本身是业务/销售型（外贸业务员、客户经理、商务拓展）时，
    # 「销售 xxx」就是同一类工作——这类意向的名字里并没有「销售」二字，
    # 光靠字面永远对不上，必须按「都是跑客户谈单」认。
    # 市场/营销 类仍不算：市场是职能，销售是渠道，两个岗位序列。
    if any(k in intent for k in SALES_INTENT_WORDS) and "销售" in title:
        return True
    # 软件类意向：前端 / 后端 / 嵌入式 / 全栈 这些细分方向就是同一职能的入口
    # （规则 4c）。它们只共享「工程师」这个通用后缀，光靠字面永远对不上。
    # 但只对「只说了软件、没限定方向」的笼统意向生效：算法与前端是并列方向，
    # 「算法工程师」不能因为都带「工程师」就吃下前端 / 后端 / Unity3d。
    if _soft_broad(intent) and any(h in title for h in DEV_TITLE_HINTS):
        return True
    if not (_func_family(intent) & _func_family(title)):
        return False
    return not any(w in title and w not in intent for w in TECH_WORDS)


def _seeds(intent, title_set):
    """确定性召回：不指望模型「想起」字面同名的岗位。

    只认三种强证据：① 与意向同名/包含关系；② 同职能族；③ 软件类意向的细分方向。
    它们仍要过 _role_ok / _share_core，所以只补漏，不放宽语义。
    """
    fam = _func_family(intent)
    soft = _is_soft_intent(intent)
    out = []
    for t in title_set:
        strong = (intent in t) or (t in intent and len(t) >= 4)
        if strong or (fam & _func_family(t)) \
           or (soft and any(h in t for h in DEV_TITLE_HINTS)):
            out.append(t)
    return out


def _strength(intent, t, picked):
    """排序权重：字面同名 > 共享核心词 > 同职能族 > 模型挑中。"""
    igc = _grams(intent) - GEN_GRAMS
    s = 0
    if intent in t or (t in intent and len(t) >= 4):
        s += 10
    s += 3 * len(igc & _grams(t))
    if _func_family(intent) & _func_family(t):
        s += 2
    if _is_soft_intent(intent) and any(h in t for h in DEV_TITLE_HINTS):
        s += 4
    if picked:
        s += 1
    return s


def _sanitize(intent, tl, title_set, use_seed=True, cap=25):
    """去重 + 去幻觉 + 去职责冲突 + 补确定性种子 + 按强度排序截断。"""
    picked = {str(t).strip() for t in (tl or []) if str(t).strip()}
    pool = list(picked)
    if use_seed:
        pool += [t for t in _seeds(intent, title_set) if t not in picked]
    keep = []
    for t in pool:
        t = str(t).strip()
        if not t or t in keep or t not in title_set:
            continue
        if not _role_ok(intent, t) or not _share_core(intent, t):
            continue
        keep.append(t)
    keep.sort(key=lambda t: -_strength(intent, t, t in picked))
    return keep[:cap]


def _tag_reason(r):
    """确保「名字差距大、但干的活差不多」这类判定，reason 以
    「大模型判断二者类似工作内容：」开头——这是告诉学生「这是模型判定、不是字面同名」的关键。
    同义（即另一种说法）类不动；已带前缀的也不重复加。"""
    if not r:
        return r
    if r.startswith(("大模型判断", "大模型判定")):
        return r
    if any(k in r for k in ("干的活", "职责", "相近", "类似工作")):
        return "大模型判断二者类似工作内容：" + r
    return r


def _strip_tag(r):
    """去掉理由前缀，方便多条理由拼成一句话（前缀只保留一次）。"""
    r = (r or "").strip()
    for p in ("大模型判断二者类似工作内容：", "大模型判定二者类似工作内容：",
              "大模型判断", "大模型判定"):
        if r.startswith(p):
            return r[len(p):].lstrip("：: ")
    return r


def align_jobs(intended, jobs, sem=None, biz=""):
    """把企业端「模型挑出的岗位」收紧到与意向真正同职能的范围。

    前端行里的 matched_jobs 是早期按**别的画像**跑的匹配结果，常出现
    「管培生」「业务推动」「区域总监助理」这类只共享通用词、或干脆跨职能的岗位名；
    它们在页面上还会被拼成「干的活相近」的假理由。这里用与语义层同一套
    确定性规则（_role_ok + _share_core）重新对齐，返回：
      kept   [(岗位名, 对应意向)]  按强度降序
      dropped [岗位名]             与任何意向都不同职能
    """
    kept, dropped, _, _ = align_jobs_detail(intended, jobs, sem, biz)
    return kept, dropped


def _ask_why(t, intent):
    """大类岗位名的现场问法：不假装对口，也不说人家不招，只让学生去问一句。"""
    return ("岗位名只写「%s」，没写业务方向；不同企业的「%s」按不同条线培养。"
            "现场问一句：是招「%s」这个方向，还是统一轮岗后再定岗。" % (t, t, intent))


def _ask_why_wider(t, intent):
    """岗位名比意向宽一层时的问法：不假装对口，也不说人家不招。"""
    return ("岗位名「%s」只写到「%s」这一层，比「%s」宽，看不出是不是这个细分方向。"
            "现场问一句：这个岗具体做「%s」吗。" % (t, _dir_text(t) or t, intent, intent))


def _ask_why_spec(t, intent):
    """同向但岗位更细（测试 → 电气测试 / 器件测试）：这不是同一个岗。"""
    return ("岗位名「%s」在「%s」上加了方向（%s），不是不带方向的「%s」，"
            "不能当成同一个岗位。现场问一句：这个岗是不是只做「%s」。"
            % (t, intent, _dir_text(t) or t, intent, _dir_text(t) or t))


def _ask_why_parallel(t, intent):
    """并列方向（后端 × 量化 / 前端）：同属一个大类，却是两条不同的路。"""
    return ("岗位名「%s」和你的「%s」同属一个大类，但方向不同（%s × %s），"
            "不是同一个岗位的两种叫法。现场问一句：这个岗能不能按「%s」的方向培养。"
            % (t, intent, _dir_text(t) or t, _dir_text(intent) or intent, intent))


def _reason_by_direction(t, intent):
    """方向不一样时，用确定性的句子替换模型那句「另一种说法」。

    「量化开发工程师」与「开发工程师」是同一大类下的两个方向，说成「另一种说法」
    就是错的；写成「是开发下面的量化方向」才对，学生才知道要不要去问。
    """
    a, at = _dir_core(intent)
    b, bt = _dir_core(t)
    if not b or a == b:
        return ""
    if b in a:
        return ""
    if at and bt and not (at & bt):
        if _same_line(at, bt):
            # 采购/物流/仓储 ⊂ 供应链：同一条线的细分，算对口，但要说清是哪个细分。
            return "「%s」是「%s」条线下的%s细分（同一条线）" % (t, intent, b)
        # 两个都带方向词且不相交：并列的两条路（量化 × 后端 / 计划 × 供应链）
        return "「%s」与「%s」同属一个大类但方向不同（%s × %s）" % (t, intent, b, a)
    if not at:
        # 意向没限定方向，岗位带方向（开发工程师 → 量化开发）
        return "「%s」是「%s」下的%s方向（同一大类，不是同一细分）" % (t, intent, b)
    if a in b:
        # 同向但岗位更细（测试 → 器件测试 / 电气测试）
        return "「%s」的方向是%s，比「%s」细，不是同一个岗位" % (t, b, intent)
    return ""


def align_jobs_detail(intended, jobs, sem=None, biz=""):
    """同 align_jobs，另外挑出「只是层级不符」和「大类岗位名待问」两类岗位。

    层级不符（主管 / 资深 / 经理）：同职能但档位高，校招投不进 → `level_blocked`，
    报告写一句「可现场问是否按应届招」，企业不删。

    大类岗位名（管培生 / 储备干部 / 研究员 / 专员）：名字不带方向，小类可以进大类、
    大类不能反推小类 → `ask`。**除非公司业务本身就是那个细分**（biz 参数，
    例：供应链公司的管培生），否则不算对口证据，只在提问里给一句。
    """
    intended = [str(x).strip() for x in (intended or []) if str(x).strip()]
    kept, dropped, level_blocked, ask = [], [], [], []
    for t in jobs or []:
        t = str(t).strip()
        if not t:
            continue
        # 大类岗位名（管培生 / 储备干部 / 研究员）先走原逻辑：公司业务就是这个细分
        # 才算对口，否则只问一句。
        if is_bare_generic(t):
            best, best_s = None, -1.0
            for it in intended:
                if is_bare_generic(it):
                    continue
                if biz_ok(it, biz):
                    best, best_s = it, -1.0
                    break
                if len(ask) < 3 and not any(a["job"] == t for a in ask):
                    ask.append({"job": t, "intent": it, "why": _ask_why(t, it)})
                    break
            if best:
                kept.append((t, best))
                continue
            dropped.append(t)
            for it in intended:
                if _role_ok(it, t, ignore_level=True) and _share_core(it, t) \
                        and not job_level.check(it, t)[0]:
                    lv = job_level.level_of(t)
                    level_blocked.append({"job": t, "intent": it,
                                          "level": job_level.LEVEL_NAMES.get(lv, "未识别"),
                                          "why": job_level.check(it, t)[1]})
                    break
            continue

        # 非大类岗：只在「方向合法」的配对里挑最强的意向。同一岗位挂在「开发工程师」
        # 下合法、挂在「后端开发工程师」下不合法时取前者 —— 三个意向里只要有一个对得上，
        # 这一岗才算命中；并列方向的配对绝不生成命中。
        best, best_s, asks = None, -1.0, []
        for it in intended:
            if not (_role_ok(it, t) and _share_core(it, t)):
                continue
            if is_wider_job(t, it) and not biz_ok(it, biz):
                # 岗名比意向宽一层（算法工程师 × AI算法研究工程师）：沾边但看不出细分。
                asks.append((it, "wider"))
                continue
            v = pair_verdict(t, it)
            if v == "ok":
                s = _strength(it, t, True)
                if s > best_s:
                    best, best_s = it, s
            else:
                asks.append((it, v))
        if best:
            kept.append((t, best))
            continue
        if asks:
            # 方向对不上：岗位更细 → 问一句；并列方向（后端 × 量化 / 前端）也说清
            # 「这家确实有开发岗，只是方向不是你的」，既不假装对口，也不说人家不招。
            it, v = asks[0]
            why = {"wider": _ask_why_wider, "ask": _ask_why_spec,
                   "no": _ask_why_parallel}[v](t, it)
            if len(ask) < 3 and not any(a["job"] == t for a in ask):
                ask.append({"job": t, "intent": it, "why": why})
            dropped.append(t)
            continue
        dropped.append(t)
        for it in intended:
            if _role_ok(it, t, ignore_level=True) and _share_core(it, t) \
                    and not job_level.check(it, t)[0]:
                lv = job_level.level_of(t)
                level_blocked.append({"job": t, "intent": it,
                                      "level": job_level.LEVEL_NAMES.get(lv, "未识别"),
                                      "why": job_level.check(it, t)[1]})
                break
    kept.sort(key=lambda x: -_strength(x[1], x[0], True))
    return kept, dropped, level_blocked, ask


def ask_jobs(intended, titles, biz=""):
    """只写大类名（管培生 / 储备干部 / 研究员 / 专员）的岗位 → 现场问一句。

    这类岗位不是对口证据（大类不能反推小类），但值得去问一句：制造业企业招的
    管培生，可能就是供应链方向，也可能统一轮岗后再定岗。除非公司业务本身就是
    那个细分（biz），那种情况已经算成对口证据，不再重复问。
    """
    out, seen = [], set()
    for t in titles or []:
        t = str(t).strip()
        if not t or t in seen or not job_level.is_job_title(t):
            continue
        for it in (intended or []):
            it = str(it).strip()
            if not it or is_bare_generic(it) or biz_ok(it, biz):
                continue
            bare = is_bare_generic(t)
            # 岗位比意向宽一层（算法工程师 × AI算法研究工程师）也值得问一句：
            # 它确实沾边，但看不出是不是那个细分方向。
            wider = (not bare) and is_wider_job(t, it) \
                and _role_ok(it, t, ignore_level=True) and _share_core(it, t)
            if not (bare or wider):
                continue
            seen.add(t)
            out.append({"job": t, "intent": it,
                        "why": _ask_why(t, it) if bare else _ask_why_wider(t, it)})
            break
    return out[:3]


def level_blocked(intended, titles):
    """挑出「同一职能、但档位比应届可投的高」的岗位名（只用于报告里那句提示）。"""
    out, seen = [], set()
    for t in titles or []:
        t = str(t).strip()
        if not t or t in seen or not job_level.is_job_title(t):
            continue
        for it in (intended or []):
            ok_lv, why = job_level.check(it, t)
            if ok_lv:
                break
            if _role_ok(it, t, ignore_level=True) and _share_core(it, t):
                seen.add(t)
                out.append({"job": t, "intent": it,
                            "level": job_level.LEVEL_NAMES.get(job_level.level_of(t), "未识别"),
                            "why": why})
                break
    return out


def align_reason(pairs, sem=None):
    """用对齐后的 (岗位, 意向) 重写行理由，替换掉早期按别的画像写的那段。

    优先用语义层里该岗位的专属理由（模型判定、已净化）；没有专属理由时按
    「同名 / 同条线」给可解释文案，绝不写「同属相关类岗位，干的活相近」
    这种什么都不说明的填充句。
    """
    if not pairs:
        return ""
    bits, plain_all, has_det = [], True, False
    for t, it in pairs[:3]:
        # 方向不一样时，模型那句「另一种说法」是错的（量化开发 ≠ 开发工程师），
        # 一律换成确定性句子：是哪个大类下面的哪个方向，学生自己判断要不要去问。
        det = _reason_by_direction(t, it)
        if det:
            bits.append(det)
            has_det = True
            continue
        r = ((sem or {}).get(it) or {}).get("reasons", {}).get(t) or ""
        r = _strip_tag(r)
        ts, its = _syn(t), _syn(it)
        if ts == its:
            # 本家岗位名就是学生勾的意向名时，不要写「「X」即「X」的另一种说法」
            r = "本家岗位名与你的意向岗位同名"
        elif not r:
            if its in ts and len(its) >= 4:
                r = "「%s」即「%s」的另一种说法" % (t, it)
            elif ts in its and len(ts) >= 4:
                r = "「%s」与「%s」是同一岗位的不同写法" % (t, it)
            else:
                fam = _func_family(its) & _func_family(ts)
                label = "、".join(FAMILY_LABEL[f] for f in fam if f in FAMILY_LABEL)
                shared = ((_grams(its) - GEN_GRAMS) & _grams(ts)) & MEANING_WORDS
                if label:
                    r = "「%s」与「%s」同属%s条线，岗位名不同但职责同类" % (t, it, label)
                elif shared:
                    key = sorted(shared, key=len, reverse=True)[0]
                    r = "「%s」与「%s」都以「%s」为核心方向，岗位名不同但职责同类" % (t, it, key)
                else:
                    r = "「%s」与「%s」同属一个职能，岗位名不同但职责同类" % (t, it)
                plain_all = False
        elif "另一种说法" not in r:
            plain_all = False
        bits.append(r)
    body = "；".join(dict.fromkeys(bits))
    if has_det:
        # 有确定性句子时不再挂「大模型判断」的帽子，免得把规则话术冒充成模型判定。
        return body
    return body if plain_all else ("大模型判断二者类似工作内容：" + body)


def _fill_reasons(intent, pairs, titles):
    """保留模型给出的岗位专属 reason；种子岗（规则补回来的同类）补一句可解释说明，
    学生才知道这是「同职能不同叫法」而不是模型瞎猜。"""
    reasons = {}
    for t, r in pairs:
        if t not in titles or not r:
            continue
        # 方向不一样时，模型那句「另一种说法」直接作废（量化开发 ≠ 开发工程师）。
        reasons[t] = _reason_by_direction(t, intent) or r
    for t in titles:
        if t in reasons:
            continue
        det = _reason_by_direction(t, intent)
        if det:
            reasons[t] = det
        elif t == intent:
            reasons[t] = "本家岗位名与你的意向岗位同名"
        elif intent in t or (t in intent and len(t) >= 4):
            reasons[t] = "「%s」即「%s」的另一种说法" % (t, intent)
        else:
            reasons[t] = ("大模型判断二者类似工作内容：「%s」与「%s」同属一个职能，"
                          "岗位名不同但职责同类" % (t, intent))
    return reasons


def _dir_filter(intent, titles, reasons=None):
    """两类岗位名不许留在该意向的词表里，都在**入口**上清掉，不等展示时再滤：

    1. 并列方向（后端 × 量化 / 前端 / 嵌入式；计划 × 供应链）—— 同一个大类下的
       两条不同的路，模型按「都是开发」挑的，留着就会被说成另一个说法。
    2. 模型自己在理由里承认「不是同一细分」的配对（关务、供应商质量…）——
       意向带方向时，模型都说不细分的，不许再挂成命中。
       意向没写方向（开发工程师 → 量化开发）不算这条：那是「无方向收一切细分」，
       学生自己认可的规则。
    """
    _, at = _dir_core(intent)
    out = []
    for t in titles:
        if pair_verdict(t, intent) == "no":
            continue
        if at and reasons and "不是同一细分" in str(reasons.get(t) or ""):
            # 同一条线的细分（采购/物流/仓储 ⊂ 供应链）不受模型措辞影响：
            # 确定性层级判据说了算，模型就算嘴上说「不是同一细分」也不降。
            _, bt = _dir_core(t)
            if not _same_line(at, bt):
                continue
        out.append(t)
    return out


def _clean_map(mp, title_set):
    """缓存里的旧结果也走同一套净化，改规则后不必重调模型。"""
    cleaned = {}
    for k, v in (mp or {}).items():
        # 并列方向（后端 × 量化 / 前端 / 嵌入式）直接从词表里清掉：这类配对不是
        # 「同职能不同叫法」，留着就会在页面和理由里被说成另一个说法。
        titles = _dir_filter(k, _sanitize(k, v.get("titles") or [], title_set),
                             v.get("reasons") or {})
        reasons = {t: _tag_reason(r) for t, r in (v.get("reasons") or {}).items()
                   if t in titles and r}
        cleaned[k] = dict(v, titles=titles, reasons=reasons)
    return cleaned


def build_map(intended, all_titles, timeout=45):
    """返回 {意向岗位: {titles, terms, reason, via}}；失败返回上次判定或 {}。"""
    intended = [str(x).strip() for x in (intended or []) if str(x).strip()]
    titles = sorted({str(t).strip() for t in (all_titles or []) if str(t).strip()})
    if not intended or not titles:
        return {}
    title_set = set(titles)
    cfg = _load_env()
    # 模型名 / prompt / **本模块代码指纹** 三者都进 key：换模型、改提示词、改
    # _role_ok/_sanitize/align_* 中的任何一行逻辑，旧缓存都必须失效。少了代码
    # 指纹，就会出现「规则明明改了，页面还是旧判定」——靠人记得删缓存不可靠。
    model_tag = (os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "")
    digest = hashlib.md5(
        ("|".join(sorted(intended)) + "||" + "|".join(titles) + "||" + model_tag
         + "||" + PROMPT + "||" + cache_guard.code_fp(name="intent_semantics")
         ).encode("utf-8")
    ).hexdigest()
    cached_map = {}
    cached_obj = {}
    same_code = False
    if os.path.isfile(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            cached_obj = cached
            cached_map = cached.get("map") or {}
            # 旧缓存没有指纹 -> 视为上一版代码的结果，只在「本次也调不动模型」时
            # 兜底，且不会被当成命中。
            same_code = (cached.get("_cg") or {}).get("fp") == \
                cache_guard.code_fp(name="intent_semantics")
            if cached.get("key") == digest and cached_map:
                return _clean_map(cached_map, title_set)
        except Exception:
            pass

    def _fallback():
        """模型不可用时的兜底：只沿用它自己这一版代码算出的结果。"""
        return _clean_map(cached_map, title_set) if same_code else {}

    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")):
        return _fallback()
    short = _candidate_titles(titles, intended)
    pool = "、".join(short or titles[:240])

    out = {}
    for it in intended:
        try:
            data = _call(cfg, PROMPT.format(intent=it, titles=pool), timeout)
            raw = (data or {}).get("titles") or []
            # 兼容旧缓存：可能是字符串列表，也可能是 {title, reason} 对象列表。
            pairs = []
            for x in raw:
                if isinstance(x, dict):
                    t = str(x.get("title") or "").strip()
                    r = _tag_reason(str(x.get("reason") or "").strip())
                else:
                    t = str(x).strip()
                    r = ""
                if t:
                    pairs.append((t, r))
            titles_only = [t for t, _ in pairs][:25]
            sani = _dir_filter(it, _sanitize(it, titles_only, title_set),
                               dict(pairs))
            reasons = _fill_reasons(it, pairs, sani)
        except Exception as exc:
            # 偶发超时/断网：沿用上一次成功的大模型判定，别让功能直接退化成字面匹配。
            # 但只能沿用它自己这一版代码的结果——旧代码的 map 里可能写着已经修掉的
            # 错误归因，沿用等于把 bug 又装回去。
            print("[intent_semantics] %s 判定失败，沿用上次结果：%s" % (it, exc))
            return _fallback()
        out[it] = {"titles": sani, "reasons": reasons, "terms": [],
                   "reason": "大模型判定为同类校招岗位", "via": "llm"}

    if not any(v["titles"] for v in out.values()):
        return _fallback()
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"key": digest, "map": out,
                       "_cg": cache_guard.stamp("intent_semantics", model=model_tag)},
                      f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out


def _live_entry(cfg, intent, titles, title_set, timeout):
    short = _candidate_titles(titles, [intent]) or titles[:240]
    data = _call(cfg, PROMPT.format(intent=intent, titles="、".join(short)), timeout)
    pairs = []
    for x in (data or {}).get("titles") or []:
        if isinstance(x, dict):
            t = str(x.get("title") or "").strip()
            r = _tag_reason(str(x.get("reason") or "").strip())
        else:
            t = str(x).strip()
            r = ""
        if t:
            pairs.append((t, r))
    sani = _dir_filter(intent, _sanitize(intent, [t for t, _ in pairs][:25], title_set),
                       dict(pairs))
    return {"titles": sani, "reasons": _fill_reasons(intent, pairs, sani), "terms": [],
            "reason": "大模型判定为同类校招岗位", "via": "llm"}


def live_map(intended, all_titles, timeout=25):
    """按需补判「payload 没覆盖到的意向岗位」。

    背景：payload 里的 intent_semantics 只覆盖构建时**那份生效画像**的意向。
    学生换画像、现场新勾一个岗位后，前端拿不到映射就只能退回本地字面规则，
    报告里如实写「本地规则判定」。这里按意向逐个短调用模型补上。

    与 build_map 的区别：只判传入的意向、不重算整份映射、不覆盖主缓存；
    结果存 data/intent_semantics_live.json，key 含代码指纹 + 模型名 + 岗位名全集；
    判不动时返回空 map（前端继续用本地规则兜底），绝不编一个结论糊过去。
    """
    intents = [str(x).strip() for x in (intended or []) if str(x).strip()][:6]
    titles = sorted({str(t).strip() for t in (all_titles or []) if str(t).strip()})
    if not intents or not titles:
        return {"map": {}, "model": "", "done": [], "failed": intents,
                "note": "没有可判的意向或岗位名"}
    title_set = set(titles)
    cfg = _load_env()
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    fp = cache_guard.code_fp(name="intent_semantics")
    store = {"_cg": cache_guard.stamp("intent_semantics", model=model), "items": {}}
    if os.path.isfile(LIVE_CACHE):
        try:
            with open(LIVE_CACHE, encoding="utf-8") as f:
                old_store = json.load(f)
            if isinstance(old_store, dict) and old_store.get("_cg", {}).get("fp") == fp:
                store = old_store
                store.setdefault("items", {})
        except Exception:
            pass
    items = store["items"]
    have_key = bool(os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", ""))
    out, done, failed = {}, [], []
    for it in intents:
        ck = hashlib.md5(("|".join([it, model, fp, "|".join(titles)])).encode("utf-8")).hexdigest()
        old = items.get(it) or {}
        if old.get("key") == ck and old.get("value"):
            out[it] = old["value"]
            done.append(it)
            continue
        if not have_key:
            failed.append(it)
            continue
        try:
            v = _live_entry(cfg, it, titles, title_set, timeout)
        except Exception as exc:
            print("[intent_semantics] live 判定失败 %s：%s" % (it, exc))
            failed.append(it)
            continue
        out[it] = v
        items[it] = {"key": ck, "value": v}
        done.append(it)
    try:
        with open(LIVE_CACHE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return {"map": out, "model": model, "done": done, "failed": failed}
