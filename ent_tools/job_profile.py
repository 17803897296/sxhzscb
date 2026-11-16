# -*- coding: utf-8 -*-
"""匹配端 · 先建「企业岗位画像」，再和学生的意向比对（方案一，2026-11-16 用户拍板）。

为什么要换路线：
    旧链路（biz_fit.py）是「把学生的意向塞给模型，问这家单位接不接得住」。
    作答空间被意向框住了：模型会顺着「汽车零部件 → 需要机械」这条最短路径编一个
    岗位名交差，于是报告里出现「电解铝设备工程师」「钻探设备维护工程师」
    「激光雷达装调工程师」——业务词 + 岗位词粘出来的名字，招聘网站搜不到。
    用户的原话是「推断还是太多，不真实」「是行业、主营业务全部考虑的结果吗」。

    方案一把顺序倒过来：**先不看学生要什么**，只问模型「按这家单位的主营业务，
    业务链条上实际设哪些岗」；拿到清单之后，比对放到本地代码里做（same_dir）。
    好处三条：
      1. 模型没有动机拼岗位名 —— 它不知道有谁在找工作，只描述这类单位的编制；
      2. 结论可复核 —— 画像落盘成 data/company_job_profile.json，谁都能看
         这家单位列了哪些岗、哪些环节，不必信一句结论；
      3. 画像按企业缓存、**不含意向** —— 换专业、换意向不用重算。这是
         「在采集端就做画像」的前提：一次建好，报告端和测试端反复用。

三档（yes/ask/no）保留，但由本地判据给出，不再由模型直接判：
    yes  画像里有与学生意向同方向的岗位，且该岗名通过了通用性校验（非拼造）
    ask  画像不完整（岗位数 < NO_MIN 或写不出业务链条）→ 不下结论，现场问
    no   画像完整，且链条上没有任何一个岗位是学生方向
    「完整」才敢判 no —— 与旧版「证据不足不判 no」同一条纪律，只是判据从
    「模型说得出」换成了「画像列得全」。

红线（与 biz_fit 一脉相承）：
    - no 只表示「这个展位这次别排队」，不是「人家不招人」；0 岗 ≠ 不招。
    - 模型不可用 / 超时 / 解析失败 → ask（中性），绝不默认 no。
    - 画像里的岗位名是「这类单位的常规编制」，不是「这家单位今年的招聘计划」；
      报告里必须写明来源是模型按行业惯例列的清单。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import intent_semantics
import job_direction
import cache_guard
from intent_semantics import _load_env

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "job_profile_cache.json")
DUMP = os.path.join(HERE, "data", "company_job_profile.json")
TIMEOUT = 40
MAX_CACHE = 900

PROMPT_VER = "jobprof-v3"

# 判 no 的门槛：岗位数够多、链条写得出、覆盖环节够散，才叫「画像完整」。
NO_MIN_JOBS = 8
NO_MIN_STAGES = 3

# —— 关于「万金油岗」为什么没有加过滤（2026-11-16 试过，撤了）——
# 36 家画像里「设备维护工程师」出现在 30 家（83%）、「质量工程师」25 家、
# 「工艺工程师」21 家，看着像「谁都能上」的岗，一度加过「跨批出现 ≥45% 的岗
# 不能单独作 yes 证据」的规则，结果 yes 从 29 家塌到 2 家、ask 涨到 28 家：
#   · 这 36 家绝大多数是制造业，制造业都设设备/质量/工艺岗是**真实共性**，
#     不是「没有信息量」——自动化专业能进制造业做设备岗，正是要告诉学生的结论；
#   · 被压成 ask 的里面有万达商管（物业工程部真有电气/暖通/设备岗），
#     一刀切会把真结论也压掉。
# 真正该防的是「岗位名是编的」，那由 is_generic_title 在校验层挡，不在这里。
# 只有 weak 档不进 yes 证据（weak = 常外包或别的岗兼），这一条保留。

PROMPT = """你是招聘岗位分析助手。下面是**一家**参会单位的信息。
请只回答一个问题：**按这家单位的主营业务，它的业务链条上实际设置哪些岗位？**

注意：你不知道有谁在找工作，也没有人给你求职意向。你只描述「这类单位通常的
部门与编制」，不做匹配、不做推荐、不猜谁适合。

【输出格式】第一行写业务链条，之后每个岗位一行，全部用竖线 | 分隔。
不要表头、不要空行、不要解释、不要编号：
CHAIN | 主营做什么、经过哪几个环节（不超过 40 字）
岗位名 | 强度 | 环节
岗位名 | 强度 | 环节
……

强度只能是下面三个词之一：
- strong：这类单位设这个岗是常规配置，几乎必有
- medium：业务有 this 方向、规模够才设
- weak：可能有，但常外包或由别的岗兼

环节只能是下面六个词之一：研发 / 生产 / 质量 / 供应链 / 销售 / 职能

【岗位名要求】
1. 只写招聘网站上**通用**的岗位名，由「方向词 + 岗位词」构成：
   机械设计工程师、工艺工程师、设备维护工程师、质量工程师、采购专员、
   财务主管、销售工程师、人力资源专员。
2. **不许拼造**。不许把产品名、材料名、技术名、行业名、公司名和岗位词粘起来。
   「电解铝设备工程师」「家纺工艺工程师」「激光雷达装调工程师」
   「测风系统控制工程师」「速冻食品工艺工程师」这类名字，招聘网站上搜不到，
   一律不许写。想表达「做设备维护」就写「设备维护工程师」。
3. 数量 12~20 个，覆盖上面链条的各个环节 —— 宁可多列几个，不要漏。
   漏了会让下游误判成「这家没有这个方向」。
   **技术方向必须写细，不要用一个词包住所有方向**：
   - 机械方向 → 机械设计工程师 / 结构工程师
   - 电气与自动化方向 → 电气工程师 / 自动化工程师 / 控制工程师
   - 软件与算法方向 → 软件工程师 / 算法工程师 / 嵌入式工程师
   - 测试方向 → 测试工程师 / 试验工程师
   - 工艺方向 → 工艺工程师
   一家有产线、有设备的单位，**几乎一定有电气或自动化方向的岗**（产线控制、
   设备电气、PLC、仪表），不要只写「设备维护工程师」就交差。
   **工艺岗和质量岗要写清是哪个方向**：「工艺工程师」「质量工程师」这种不指明方向的
   不算，要写化工工艺工程师、注塑工艺工程师、焊接工艺工程师、涂装工艺工程师、
   食品工艺工程师、供应商质量工程师。这不是拼造 —— 这些都是招聘网站上的原名。
   职能类（财务、人力、行政、法务、采购、物流、销售、市场、客服）合计不超过 5 个，
   每个方向最多 1 个 —— 这类岗每家都有，写多了对判断没有帮助。
4. 不写「管理培训生」「储备干部」「管培生」「实习生」这类不指向具体方向的岗。
5. 不写薪资、人数、学历要求，那不是你的任务。

【怎么判断这家单位是干什么的】
- 「主营」为空或只有「（未查到主营）」时，按单位名称能看出的业务判断
  （XX 铝业 = 铝加工，XX 汽车部件 = 汽车零部件，XX 中学 = 学历教育）。
- 名称含 中学/小学/大学/学校/职业技术学院 → 学历教育，岗位围绕教学、教务、行政、后勤；
- 含 组织部/人民政府/管理局 → 党政机关，岗位是综合管理、政策研究、文秘，无生产经营岗；
- 含 财务/银行/保险/证券/基金 → 金融，岗位是客户经理、风控、信贷、柜员、投资研究；
- 含 商业管理/广场/商管/物业 → 商业综合体运营，岗位是招商、运营、物业工程、市场推广；
- 含 升学/辅导/培训/教育科技 → 课外辅导与升学服务，岗位是学科教师、课程顾问、教研。
- 名称与行业标签对不上时，以名称和主营写出的实际业务为准，别跟着错的标签走。
- 单位性质（民企/国企/事业单位）不是判断依据。事业单位的院所、调查大队、
  设计院、检测中心可能大量设技术岗，看主营。

【反例，照这个纠正】
错：电解铝设备工程师  → 对：设备维护工程师
错：家纺工艺工程师    → 对：工艺工程师
错：测风系统控制工程师 → 对：自动控制工程师
错：速冻食品研发工程师 → 对：食品研发工程师

单位信息：
名称 | 行业 | 性质 | 主营范围/主要产品 | 岗位公告情况 | 已知岗位名
{row}
"""

# 岗位名的「通用方向词」。判一个岗位名是不是通用名，就看它能不能整名拆成
# 「这些词 + 岗位词」；拆不掉的残留片段就是业务前缀，说明是拼接出来的。
# 这张表宁可长 —— 它只用于**降级**（拆不掉的降为 weak、不进 yes 证据），
# 不用于删岗，所以漏收录一个词的代价只是「这个岗不进证据」，不会误伤整家。
DIR_WORDS = (
    # —— 学科/专业方向 ——
    "机械", "机电", "电气", "自动化", "电子", "微电子", "通信", "计算机", "软件",
    "硬件", "数据", "算法", "人工智能", "嵌入式", "网络", "信息安全", "系统",
    "化工", "化学", "材料", "高分子", "复合材料", "金属", "冶金", "食品", "生物",
    "制药", "医药", "药物", "临床", "医学", "护理", "环境", "环保", "安全",
    "土木", "建筑", "结构", "暖通", "给排水", "市政", "测绘", "地质", "采矿",
    "能源", "电力", "热动", "热能", "电气工程", "仪表", "测控", "光学", "光电",
    "工业工程", "物流工程", "包装", "纺织", "服装", "印刷", "工业设计",
    # —— 行业/产品方向（通用到能在招聘网站上独立成岗的） ——
    "汽车", "新能源", "电池", "锂电", "光伏", "风电", "储能", "半导体", "芯片",
    "显示", "面板", "家电", "家具", "钢", "铝", "铜", "造纸", "水泥", "玻璃",
    "陶瓷", "涂料", "塑料", "橡胶", "化肥", "农药", "饲料", "乳品", "饮料",
    "酒", "烟草", "珠宝", "玩具", "医疗器械", "机器人", "无人机", "传感",
    # —— 职能方向 ——
    "质量", "品质", "工艺", "设备", "生产", "制造", "研发", "设计", "测试",
    "试验", "验证", "运维", "实施", "售前", "售后", "技术支持", "技术",
    # 控制/管理 是岗位名里最高频的两个方向核，缺一个就会把
    # 「自动化控制工程师」「设备管理岗」全判成拼造名
    "控制", "管理", "运行", "值班", "检修",
    # 制造业现场岗位的方向词（模型常写「模具工程师」「焊接工程师」）
    "模具", "焊接", "热处理", "铸造", "锻造", "冲压", "注塑", "挤出", "喷涂",
    "装配", "机加工", "数控", "钳工", "电工", "焊工", "车工", "铣工", "叉车",
    "锅炉", "管道", "阀门", "制冷", "空调", "变电", "配电", "土建", "装修",
    "造价", "资料", "测量", "选矿", "冶炼", "轧制", "有色",
    "销售", "市场", "营销", "品牌", "渠道", "电商", "运营", "客服", "采购",
    "供应链", "物流", "仓储", "计划", "财务", "会计", "审计", "税务", "成本",
    "预算", "投资", "风控", "法务", "合规", "人力", "人力资源", "行政",
    "培训", "招聘", "绩效", "薪酬", "项目", "工程", "施工", "安装", "调试",
    "维修", "维护", "保养", "检验", "检测", "化验", "实验", "统计", "数据",
    "产品", "规划", "招商", "物业", "教师", "教学", "教研", "教务", "课程",
    "编辑", "翻译", "设计", "美术", "动画", "新媒体", "公关", "战略", "经营",
    "国际", "海外", "国内", "区域", "大客户", "客户", "业务", "商务", "单证",
    "报关", "货代", "运力", "调度", "安全", "环保", "能源管理", "设施",
    "仓管", "仓储", "总务", "后勤", "档案", "党群", "宣传", "内控", "保安",
    "司机", "厨师", "保洁", "绿化", "前台", "收银", "店长", "导购", "理货",
    # 行业简称与教育/金融/医疗的常设岗（真实岗位名里常见，缺了会误降级）
    "自控", "仪控", "工控", "人事", "出纳", "资金", "核算", "稽核", "文秘",
    "机要", "体系", "标准化", "计量", "理化", "无损", "工装", "夹具", "涂装",
    "电镀", "水处理", "污水", "固废", "环评", "安环", "消防", "弱电", "强电",
    "数据库", "前端", "后端", "全栈", "视觉", "交互", "标注", "辅导员",
    "讲师", "医师", "护士", "药师", "信贷", "检察官", "综合", "政策", "学科",
    "班主任", "教务", "教研", "课程", "网点", "柜面", "研究", "处理", "开发",
    # 电商 / 贸易 / 服务业（跨境电商企业的岗位名普遍带行业前缀，
    # 「跨境电商运营专员」「电商美工」「外贸业务员」都是真实岗名，不该降级）
    "服务", "仓库", "车间", "推广", "美工", "外贸", "跟单", "供应", "供应商",
    "选品", "内容", "投放", "跨境", "售后", "订单", "结算", "配送", "门店",
    "广告", "策划", "文案", "直播", "短视频",
)

# —— 「专业方向词」——
# 与 DIR_WORDS 的区别：DIR_WORDS 判「这个名字是不是通用岗名」（可以含职能词），
# DIR_STRONG 判「这个名字有没有指明专业方向」（只认学科/行业，不认职能）。
#
# 为什么需要这一层：换到化工画像后测出来的 —— 26 家 yes 里 25 家靠「工艺工程师」。
# 这个岗名在化工厂、汽车零部件厂、家纺厂、施工企业里都成立，但内涵完全不同：
# 化工厂的工艺是反应与分离，汽车厂的是注塑与涂装，施工企业的是工法与工序。
# 「工艺工程师」四个字撑不起「这家对口」的结论，只能撑起「去问一句」。
DIR_STRONG = (
    "机械", "机电", "电气", "自动化", "控制", "电子", "微电子", "通信", "计算机",
    "软件", "硬件", "嵌入式", "网络", "信息安全", "数据", "算法", "人工智能",
    "化工", "化学", "材料", "高分子", "复合材料", "金属", "冶金", "食品", "生物",
    "制药", "药物", "医药", "临床", "医学", "护理", "环境", "环保", "土木",
    "建筑", "结构", "暖通", "给排水", "市政", "测绘", "地质", "采矿", "能源",
    "电力", "热动", "热能", "仪表", "测控", "光学", "光电", "工业工程", "包装",
    "纺织", "服装", "印刷", "工业设计", "汽车", "新能源", "电池", "锂电", "光伏",
    "风电", "储能", "半导体", "芯片", "显示", "家电", "家具", "钢", "铝", "铜",
    "造纸", "水泥", "玻璃", "陶瓷", "涂料", "塑料", "橡胶", "化肥", "农药",
    "饲料", "乳品", "饮料", "医疗器械", "机器人", "无人机", "传感", "射频",
    "天线", "雷达", "导航", "卫星", "模具", "焊接", "热处理", "铸造", "锻造",
    "冲压", "注塑", "挤出", "喷涂", "装配", "数控", "钳工", "电工", "锅炉",
    "管道", "阀门", "制冷", "空调", "变电", "配电", "造价", "测量", "选矿",
    "冶炼", "轧制", "自控", "仪控", "工控", "无损", "涂装", "电镀", "水处理",
)

# 岗位词尾（一个岗位名去掉它之后，剩下的应当全部由 DIR_WORDS 拼成）
JOB_TAILS = (    "工程师", "技术员", "技术工", "设计师", "研究员", "分析师", "规划师",
    "管理师", "经济师", "会计师", "统计师", "专员", "主管", "经理", "总监",
    "助理", "文员", "操作工", "作业员", "检验员", "实验员", "调度员",
    "教师", "顾问", "讲师", "辅导员", "医师", "护士", "药师", "美工",
    "销售", "代表", "岗", "师", "员", "工", "长",
)

_D_BY_LEN = sorted(set(DIR_WORDS), key=len, reverse=True)
_T_BY_LEN = sorted(set(JOB_TAILS), key=len, reverse=True)

FIT_WORDS = ("yes", "ask", "no")
ASK = {"fit": "ask", "jobs": [], "evidence": [], "basis": "", "note": "",
       "via": "fallback", "chain": "", "profile_jobs": []}

MAX_WORKERS = max(1, int(os.environ.get("FAIR_LLM_WORKERS") or 8))


# ---------------------------------------------------------------- 模型调用

def _model_tag():
    try:
        cfg = _load_env()
    except Exception:
        cfg = {}
    return "%s|%s|%s" % (
        os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "",
        os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "",
        PROMPT_VER)


def _call_raw(cfg, prompt, timeout):
    """与 biz_fit._call_raw 同源。分开写是为了不动 biz_fit —— 那个模块的
    cache_guard 指纹按文件内容算，改一个字符就会让 36 家判定缓存全部作废。"""
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- 岗位名校验

def is_generic_title(title, extra_dirs=()):
    """这个岗位名是不是「通用名」（招聘网站上真的在用）。

    做法：去掉尾部岗位词，再把剩下的部分按 DIR_WORDS 从长到短贪心切分。
    全部吃掉 → 通用名；留了残片 → 那残片就是业务前缀，是拼出来的。

        「机械设计工程师」→ 机械|设计 + 工程师  ✓
        「设备维护工程师」→ 设备|维护 + 工程师  ✓
        「电解铝设备工程师」→ 电解铝(×) |设备 + 工程师  ✗ 拼造
        「家纺工艺工程师」  → 家纺(×) |工艺 + 工程师  ✗ 拼造

    extra_dirs 允许调用方补几个本行业的通用词（如「有色」「选矿」），
    但**不拿企业主营去补** —— 否则「电解铝设备工程师」会因为主营里有「电解铝」
    而被当成通用名，正好放过要挡的那种。
    """
    t = re.sub(r"[（(][^)）]*[)）]", "", str(title or "")).strip()
    # 英文缩写（UI / UX / PLC / ERP / CNAS）不是业务前缀，直接跳过——
    # 「UI设计师」不该因为 UI 切不掉就被判成拼造名
    t = re.sub(r"[A-Za-z0-9&+#\s]+", "", t)
    if not t:
        return bool(re.search(r"[A-Za-z]", str(title or "")))
    for tail in _T_BY_LEN:
        if t.endswith(tail) and len(t) > len(tail):
            t = t[:-len(tail)]
            break
    if not t:
        return True                      # 只剩「专员」「工程师」这种，属通用
    dirs = sorted(set(DIR_WORDS) | set(extra_dirs or ()), key=len, reverse=True)
    i = 0
    while i < len(t):
        hit = next((d for d in dirs if t.startswith(d, i)), "")
        if hit:
            i += len(hit)
        else:
            return False
    return True


# 看着像有方向、其实是别的职能的岗名。施工企业的「材料员」是买材料跑验收的，
# 不是材料研发 —— 它命中的是「材料」两个字，不是方向。
_GENERIC_EXACT = {"材料员", "资料员", "保管员", "材料管理员", "仓库管理员",
                  "业务员", "单证员", "跟单员", "报关员", "安全员", "质检员",
                  "施工员", "技术员", "测量员", "试验员", "预算员", "计划员"}


def is_specific_title(title):
    """岗位名**自己写着**专业方向吗（不看主营）。

        「自动化工程师」「化工工艺工程师」「材料研发工程师」 → 有
        「工艺工程师」「质量工程师」「设备维护工程师」「检验员」→ 没有

    注意：这个函数只回答「名字里有没有方向」，**不再用来否决匹配**。
    名字里没方向的岗，方向由 job_direction.resolve() 按主营推定 —— 化工企业的
    「工艺工程师」就是化工工艺，拦掉它等于把真对口的企业推成「待核实」。
    """
    return bool(job_direction.title_dir(title))


def _clean_title(title):
    """清掉括号补充、空格、书名号；保留原名（不做同义改写，证据要能对上原话）。"""
    t = re.sub(r"[（(][^)）]*[)）]", "", str(title or ""))
    t = re.sub(r"[\s、,，;；/]+", "", t)
    t = t.strip("《》\"'“”")
    return t[:16]


_NONE_TITLES = {"无", "—", "-", "暂无", "n/a", "none", "null", "岗位名", "岗位"}
_SKIP_TITLES = ("管理培训生", "储备干部", "管培生", "培训生", "实习生", "见习生")


def _parse(text):
    """解析成 {"chain": str, "jobs": [{title,strength,stage,suspect}]}。

    格式不对也要尽量留下东西：模型偶尔会把 CHAIN 写成「业务链条：」，
    或者只给岗位不给强度 —— 强度缺省按 medium，链条缺省空（缺链条就不判 no）。
    """
    chain, jobs, seen = "", [], set()
    for raw in (text or "").replace("｜", "|").splitlines():
        line = raw.strip().strip("`").strip()
        line = re.sub(r"^[-*•\d.、\s]+", "", line)
        if not line:
            continue
        head = re.split(r"[|:]", line, 1)[0].strip().lower()
        if head in ("chain", "业务链条", "链条") or line.lower().startswith("chain"):
            chain = re.split(r"[|:：]", line, 1)[-1].strip()[:80]
            continue
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = _clean_title(parts[0])
        if not title or title.lower() in _NONE_TITLES or len(title) < 2:
            continue
        if any(s in title for s in _SKIP_TITLES):
            continue
        strength = (parts[1].strip().lower() if len(parts) > 1 else "medium")
        strength = strength if strength in ("strong", "medium", "weak") else "medium"
        stage = re.sub(r"[^\u4e00-\u9fa5]", "", parts[2])[:4] if len(parts) > 2 else ""
        k = (title, stage)
        if k in seen:
            continue
        seen.add(k)
        jobs.append({"title": title, "strength": strength, "stage": stage})
    return {"chain": chain, "jobs": jobs}


def _mark(jobs):
    """给岗位名补 suspect 标记。**每次读都现算**，不写进缓存 ——
    suspect 只取决于 DIR_WORDS（本地词表），与模型输出无关；固化进缓存的话，
    往词表里补一个「售后」就得把 36 家的模型结果全部作废重跑，白花钱。
    """
    return [dict(j, suspect=not is_generic_title(j.get("title", "")))
            for j in (jobs or [])]


# ---------------------------------------------------------------- 画像构建

def _row_text(name, industry, nature, segment, scope, job_src, titles):
    main = "；".join(x for x in (
        str(segment or "").strip(), str(scope or "").strip()) if x)
    main = re.sub(r"\s+", " ", main)[:320] or "（未查到主营）"
    src = str(job_src or "").strip() or "未知"
    js = "有岗位公告" if (src not in ("未知", "未知1", "未知2", "")) else "无岗位公告"
    ts = "、".join([t for t in (titles or []) if t][:10]) or "—"
    return "%s | %s | %s | %s | %s | %s" % (
        name, industry or "未知", nature or "未知", main, js, ts)


def _key(row):
    blob = json.dumps({"row": row, "prompt": PROMPT, "model": _model_tag()},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:20]


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {}
    cur = cache_guard.code_fp(name="job_profile")
    maps = obj.get("maps") or {}
    kept = {k: v for k, v in maps.items() if (v.get("_code") == cur)}
    if len(kept) != len(maps):
        print("[job_profile] 丢弃 %d 条旧版缓存（代码指纹不符）"
              % (len(maps) - len(kept)))
    return kept


def _save_cache(maps):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"maps": maps, "model": _model_tag(),
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "_cg": cache_guard.stamp("job_profile", model=_model_tag())},
                      f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _compute(row, cfg, timeout):
    text = _call_raw(cfg, PROMPT.format(row=row), timeout)
    obj = _parse(text)
    if not obj["jobs"]:
        print("[job_profile] 解析不出岗位：%s" % (text or "")[:60].replace("\n", " "))
        return None
    obj["via"] = "llm"
    return obj


def build_many(ones, timeout=TIMEOUT):
    """并发建画像，返回 {企业名: {chain, jobs, via}}。**不看意向**，所以能复用。

    与 biz_fit.judge_many 一样只并发算、不并发写，最后由主线程一次性落盘。
    """
    out, maps, todo = {}, {}, []
    bizmap = {}
    maps = _load_cache()
    for one in (ones or []):
        name = str(one.get("name") or "")
        # 主营原文一并存进画像：方向判定要用它，而它也属于「数据」不属于「模型输出」，
        # 换词表不用重跑模型（match 里现算）。
        bz = job_direction.biz_text(one.get("industry"), one.get("segment"),
                                    one.get("scope"))
        bizmap[name] = bz
        row = _row_text(name, one.get("industry", ""), one.get("nature", ""),
                        one.get("segment", ""), one.get("scope", ""),
                        one.get("job_src", ""), one.get("titles"))
        key = _key(row)
        if key in maps:
            v = maps[key]
            out[name] = {"chain": v.get("chain", ""), "jobs": _mark(v.get("jobs")),
                         "via": v.get("via", "llm"), "biz": bz}
        else:
            todo.append((name, row, key))
    if not todo:
        return out

    try:
        cfg = _load_env()
    except Exception:
        cfg = {}
    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY")):
        for one in (ones or []):
            out[str(one.get("name") or "")] = {"chain": "", "jobs": [],
                                               "via": "fallback"}
        return out

    t0, added = time.time(), 0
    workers = min(MAX_WORKERS, len(todo))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_compute, row, cfg, timeout): (name, key)
                for name, row, key in todo}
        for fut in as_completed(futs):
            name, key = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                print("[job_profile] 建画像失败（%s）：%s" % (name[:12], str(exc)[:60]))
                res = None
            if res is None:
                out[name] = {"chain": "", "jobs": [], "via": "fallback"}
                continue
            out[name] = dict(res, jobs=_mark(res.get("jobs")),
                             biz=bizmap.get(name, ""))
            maps[key] = dict(res, _code=cache_guard.code_fp(name="job_profile"))
            added += 1
    while len(maps) > MAX_CACHE:
        maps.pop(next(iter(maps)))
    _save_cache(maps)
    print("[job_profile] %d 家待建 → %d 线程，新增缓存 %d 条，用时 %.1fs"
          % (len(todo), workers, added, time.time() - t0))
    return out


def load_biz_map(db=None):
    """从企业库读「企业名 → 主营原文」。给只读画盘的调用方（试跑脚本、测试）用：
    画像 JSON 里已经存了 biz，但早期落盘的没有，这里能补齐；也便于单独核对
    某家的方向推定依据。"""
    import sqlite3
    path = db or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "careersafari.db")
    out = {}
    try:
        con = sqlite3.connect(path)
        for (it,) in con.execute("SELECT item_json FROM company_jobs"):
            try:
                o = json.loads(it)
            except Exception:
                continue
            out[str(o.get("name") or "")] = job_direction.biz_text(
                o.get("industry"), o.get("segment"), o.get("biz_scope"))
        con.close()
    except Exception as exc:
        print("[job_profile] 读主营失败：%s" % str(exc)[:60])
    return out


def dump(profiles, path=DUMP):
    """画像落盘。采集端 / 报告端 / 测试端都读这一份，避免各算一遍。"""
    try:
        old = json.load(open(path, encoding="utf-8"))
        if isinstance(old, dict):
            old.update(profiles or {})
            profiles = old
    except Exception:
        pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=1)
    except Exception as exc:
        print("[job_profile] 落盘失败：%s" % str(exc)[:60])


# ---------------------------------------------------------------- 比对

def _good(jobs):
    """可以作为「yes 证据」的岗位：非拼造名，且强度不是 weak（weak 常外包）。"""
    return [j for j in jobs
            if not j.get("suspect") and j.get("strength") in ("strong", "medium")]


def match(prof, intended, biz=None):
    """画像 × 意向 → {fit, jobs, evidence, basis, note, via}（结构对齐 biz_fit）。

    比对是**本地确定性**的：同方向用 intent_semantics.same_dir，方向用
    job_direction（主营推定）。模型只负责「这家有哪些岗」，不负责「你适不适合」，
    所以报告里写的每个证据岗都能在画像文件里逐字找到，方向依据也能复算。

    方向只从**主营**推定，从不从意向倒推 —— 旧链路（把意向塞给模型问「这家接不接
    得住」）之所以编出「电解铝设备工程师」，就是让模型顺着意向找台阶。
    """
    if not prof or prof.get("via") != "llm":
        return dict(ASK)
    jobs = prof.get("jobs") or []
    good = _good(jobs)
    ints = [it for it in (intended or []) if it]
    chain = (prof.get("chain") or "").strip()
    bz = biz if biz is not None else (prof.get("biz") or "")

    # 意向自身的方向：只看学生写的字。
    ints_d = [(it, job_direction.title_dir(it)) for it in ints]
    # 学生整体的方向集：只要他写过某个方向（「食品研发工程师」），
    # 「工艺工程师」这种通用岗就不能跨行业一律算 yes ——
    # 否则食品专业会跟化工、建材、电子厂全判对口，等于没筛。
    # 反过来，学生只写「人力资源专员」「会计」这种（一个方向词都没有）时，
    # 这个集合为空，规则不生效：职能本身就是他的专业，不该被行业挡掉。
    stu_dirs = {di for _it, di in ints_d if di}

    hits, vague, veto = [], [], []
    for j in good:
        t = j["title"]
        info = job_direction.resolve(t, bz)
        for it, di in ints_d:
            if not (t == it or intent_semantics.same_dir(t, it)):
                continue
            # 这条意向自己的方向优先；意向本身没方向时，用学生的整体方向
            sds = {di} if di else stu_dirs
            chosen = job_direction.pick(info["dirs"], sds, t)
            if info["dirs"] and sds and not chosen:
                # 岗有方向、学生有方向、整组都对不上（化工 vs 机械）→ 不是同一个岗
                veto.append({"title": t, "intent": it,
                             "title_dir": "、".join(
                                 sorted({job_direction.LABEL.get(c, c)
                                         for c, _k in info["dirs"]})),
                             "intent_dir": "、".join(
                                 sorted(job_direction.LABEL.get(x, x) for x in sds))})
                continue
            if sds and not info["dirs"]:
                # 学生有方向，但这个岗名不写方向、主营也推不出是哪个方向
                # → 只能说「要问一句」。注意用的是 sds 而不是 di：
                # 学生写了「食品研发工程师」+「质量工程师」，那个「质量工程师」
                # 也得看方向，否则电子厂的质量岗会被当成食品方向的对口。
                vague.append({"title": t, "intent": it,
                              "why": info.get("why") or "岗位名不带方向"})
            else:
                cn = job_direction.LABEL.get(chosen[0], chosen[0]) if chosen else ""
                hits.append({"title": t, "intent": it, "cn": cn,
                             "src": info["src"]})
            break

    stages = len({(j.get("stage") or "") for j in jobs if j.get("stage")})
    complete = len(good) >= NO_MIN_JOBS and bool(chain) and stages >= NO_MIN_STAGES
    all_titles = [j["title"] for j in jobs]

    if hits:
        # 方向是从主营推的，报告里要把这个「推定」标出来，不能写成企业的话
        ev = ["%s（%s方向）" % (h["title"], h["cn"]) if h["src"] == "biz" else h["title"]
              for h in hits][:2]
        inferred = any(h["src"] == "biz" for h in hits)
        return {"fit": "yes",
                "jobs": [h["intent"] for h in hits][:2],
                "evidence": ev,
                "basis": "%s，业务链条上设%s这类岗%s" % (
                    chain or "主营有明确业务链条", "、".join(ev),
                    "，方向按主营业务推定" if inferred else ""),
                "note": "",
                "via": "llm", "mode": "profile",
                "chain": chain,
                "profile_jobs": all_titles}

    if vague:
        why = ("岗位名只写「%s」不带专业方向，%s，得现场确认具体方向"
               % (vague[0]["title"], vague[0]["why"]))
        return {"fit": "ask",
                "jobs": [vague[0]["intent"]],
                "evidence": [],
                "basis": "%s；%s" % (chain or "主营与意向只是沾边", why),
                "note": why,
                "via": "llm", "mode": "profile",
                "chain": chain,
                "profile_jobs": all_titles}

    # ② 方向对不上：这比「没找到同名岗」更硬 —— 这家有工艺岗，但是别的方向
    if veto:
        v = veto[0]
        why = ("这家主营链条属%s方向，其「%s」也是这个方向的岗，"
               "与你找的%s方向不是一回事" % (
                   v["title_dir"], v["title"], v["intent_dir"]))
        if not complete:
            return {"fit": "ask", "jobs": [], "evidence": [],
                    "basis": "%s；%s，但画像不全，不敢下结论" % (chain, why),
                    "note": why, "via": "llm", "mode": "profile",
                    "chain": chain, "profile_jobs": all_titles}
        show_pool = [j["title"] for j in good] or all_titles
        return {"fit": "no",
                "jobs": [],
                "evidence": [],
                "basis": "%s；%s。链条上的岗位是%s这几类" % (
                    chain, why, "、".join(show_pool[:4])),
                "note": why,
                "via": "llm", "mode": "profile",
                "chain": chain,
                "profile_jobs": all_titles}

    # ③ 没有同方向岗位：画像够完整才敢判 no
    if complete:
        # 举例只挑通过通用性校验的岗名 —— 降级的拼接名连自己都不信，
        # 不能再拿去当「这家有这些岗」的举例（通达致远的「跨境电商选品专员」）
        show_pool = [j["title"] for j in good] or all_titles
        show = "、".join(show_pool[:4])
        return {"fit": "no",
                "jobs": [],
                "evidence": [],
                "basis": "%s；链条上的岗位是%s这几类，没有你找的方向" % (
                    chain, show),
                "note": "画像列了 %d 个岗位，无一与意向同方向" % len(good),
                "via": "llm", "mode": "profile",
                "chain": chain,
                "profile_jobs": all_titles}

    # ④ 画像不完整 → 中性档，不替用户决定跳过
    why = ("岗位清单只列出 %d 个（判 no 需 ≥%d）" % (len(good), NO_MIN_JOBS)
           if len(good) < NO_MIN_JOBS else "业务链条写不清")
    return {"fit": "ask",
            "jobs": [],
            "evidence": [],
            "basis": "%s；%s" % (chain or "主营与意向只是沾边", why),
            "note": why,
            "via": "llm", "mode": "profile",
            "chain": chain,
            "profile_jobs": all_titles}

    # ② 没有同方向岗位：画像够完整才敢判 no
    if complete:
        # 举例只挑通过通用性校验的岗名 —— 降级的拼接名连自己都不信，
        # 不能再拿去当「这家有这些岗」的举例（通达致远的「跨境电商选品专员」）
        show_pool = [j["title"] for j in good] or all_titles
        show = "、".join(show_pool[:4])
        return {"fit": "no",
                "jobs": [],
                "evidence": [],
                "basis": "%s；链条上的岗位是%s这几类，没有你找的方向" % (
                    chain, show),
                "note": "画像列了 %d 个岗位，无一与意向同方向" % len(good),
                "via": "llm", "mode": "profile",
                "chain": chain,
                "profile_jobs": all_titles}

    # ③ 画像不完整 → 中性档，不替用户决定跳过
    why = ("岗位清单只列出 %d 个（判 no 需 ≥%d）" % (len(good), NO_MIN_JOBS)
           if len(good) < NO_MIN_JOBS else "业务链条写不清")
    return {"fit": "ask",
            "jobs": [],
            "evidence": [],
            "basis": "%s；%s" % (chain or "主营与意向只是沾边", why),
            "note": why,
            "via": "llm", "mode": "profile",
            "chain": chain,
            "profile_jobs": all_titles}


def fit_many(ones, intended, timeout=TIMEOUT):
    """给 fair_bridge 用的一站式接口：先建画像，再逐家比对。

    签名与 biz_fit.judge_many 一致（多一个 intended），返回结构也一致，
    这样 fair_bridge 只需要换一个函数名，其余分支不用动。
    """
    profs = build_many(ones, timeout=timeout)
    return {name: match(prof, intended) for name, prof in profs.items()}


def judge(name, industry="", nature="", segment="", scope="", job_src="",
          titles=None, intended=None, timeout=TIMEOUT):
    """判一家（单条接口，内部走 fit_many 同一套缓存与并发逻辑）。"""
    one = {"name": name, "industry": industry, "nature": nature,
           "segment": segment, "scope": scope, "job_src": job_src,
           "titles": titles}
    return fit_many([one], intended or [], timeout=timeout).get(name) or dict(ASK)


if __name__ == "__main__":
    import sys
    mode = (sys.argv[1] if len(sys.argv) > 1 else "test")
    if mode == "test":
        # 自检：通用名 / 拼造名 的判定结果
        good = ["机械设计工程师", "工艺工程师", "设备维护工程师", "财务主管",
                "销售工程师", "质量工程师", "采购专员", "食品研发工程师",
                "化工工艺工程师", "自动化控制工程师", "人力资源专员", "电气设计工程师"]
        bad = ["电解铝设备工程师", "家纺工艺工程师", "激光雷达装调工程师",
               "测风系统控制工程师", "速冻食品工艺工程师", "钻探设备维护工程师",
               "地质装备自动化工程师", "锅炉控制工程师", "汽车内外饰工艺工程师"]
        print("应判通用：")
        for t in good:
            print("  %-14s %s" % (t, "OK" if is_generic_title(t) else "!! 误判为拼造"))
        print("应判拼造：")
        for t in bad:
            print("  %-20s %s" % (t, "OK" if not is_generic_title(t) else "!! 漏判"))
