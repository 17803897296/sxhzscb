# -*- coding: utf-8 -*-
"""
采集端 · 岗位推荐：专业 + 实习 / 竞赛 / 论文 -> 校招岗位名，支持逐层放宽。

v14（2026-11-14）：
  1. 三类经历**分开**传给模型（实习 / 竞赛 / 论文），不再混成一个 experiences，
     避免模型只看实习、把竞赛和论文当噪声。
  2. 「换一批」按层级逐步放宽，每层维护一个固定候选池，本层展示完才升级：
       tier 0  专业 + 实习 + 竞赛 + 论文（最贴切）
       tier 1  放宽到本专业（忽略经历细节）
       tier 2  放宽到专业大类（同学科门类的通用岗位）
       tier 3  放宽到无专业要求岗位（教培 / 销售 / 运营等），
               **仅当学生在问卷里选了「愿意考虑教培」**才启用
     每次请求带 shown（历次已展示过的岗位名）；服务端从当前层的池子里
     取没展示过的 6 个，池子空了自动升层并在响应里标明。
  3. 库里真实在招的岗位仍优先，标 from_db。

边界（不变）：输出只是给学生勾选的建议，不写进企业事实字段，
不生成「某企业招什么」的断言；模型失败一律规则兜底，演示可离线。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import cache_guard

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
POOL_FILE = os.path.join(DATA, "job_recommend_pool.json")
# 改 LLM_PROMPT 的内容或口径时手动 +1：池子缓存随之失效，避免继续吃旧提示词的结果。
PROMPT_VER = "rec-v6"
_LOCK = threading.Lock()

# 规则兜底用的「泛化校招岗位族」：刻意不写年份专属的公告岗位名。
MAJOR_BASE = {
    "管理科学与工程": ["管培生", "运营管理岗", "供应链/计划岗", "数据分析岗", "项目管理岗"],
    "运营与供应链管理": ["供应链管培生", "计划专员", "采购专员", "物流运营岗", "供应链数据分析岗"],
    "大数据分析与决策": ["数据分析岗", "BI/报表岗", "数据运营岗", "数据开发岗", "策略分析岗"],
    "金融服务与金融工程": ["管培生（金融方向）", "风险管理岗", "行业研究岗", "量化研究岗", "客户经理"],
    "复杂工程与智能管理": ["项目管理岗", "工程咨询岗", "工业工程师", "质量工程师", "造价/合约岗"],
    "智能系统管理": ["产品经理", "数据分析岗", "系统运营岗", "ERP/实施顾问", "信息管理岗"],
    "工业工程": ["工业工程师", "生产计划岗", "质量工程师", "精益改善岗", "制造管培生"],
    "计算机": ["后端开发工程师", "前端开发工程师", "算法工程师", "测试开发工程师", "数据开发工程师"],
    "软件工程": ["软件开发工程师", "后端开发工程师", "前端开发工程师", "测试开发工程师", "运维开发工程师"],
    "金融": ["管培生（金融方向）", "客户经理", "风险管理岗", "行业研究岗", "金融数据分析岗"],
    # 理科 / 化工类（2026-11-15 补：此前只有经管+IT，理科专业全兜底成管理通用岗）
    "化学": ["研发工程师", "合成研究员", "化验/质检岗", "分析测试岗", "工艺工程师", "实验室技术员", "EHS安全工程师", "质量工程师"],
    "化学工程与工艺": ["工艺工程师", "生产技术员", "化验/质检岗", "EHS安全工程师", "设备工程师", "质量工程师"],
    "材料": ["材料研发工程师", "失效分析工程师", "工艺工程师", "质量工程师", "实验室技术员", "化验/质检岗"],
    "生物": ["生物研发工程师", "发酵/细胞培养技术员", "实验员", "质检QA", "注册专员", "生产技术员"],
    "药学": ["药物分析研究员", "药品注册专员", "QA/QC岗", "生产技术员", "医学信息沟通专员"],
    "数学": ["数据分析岗", "统计分析师", "算法工程师", "精算助理", "量化研究岗", "数据开发工程师"],
    "环境工程": ["EHS工程师", "环保工程师", "水处理工程师", "环境检测员", "化验/质检岗"],
}

SIGNAL_RULES = [
    (["供应链", "库存", "补货", "采购", "物流", "仓储", "周转", "供应商", "共享治理"],
     ["供应链/计划岗", "采购专员", "物流运营岗", "供应链数据分析岗"],
     "经历里做过供应链 / 采购 / 库存相关的事"),
    (["产品", "需求文档", "原型", "用户", "PRD", "竞品"],
     ["产品经理", "产品运营岗", "商业分析岗"],
     "经历里接触过产品或需求工作"),
    (["项目", "协调", "排期", "交付", "流程", "推进"],
     ["项目管理岗", "流程/精益改善岗"],
     "经历里有项目协调或流程推进"),
    (["销售", "客户", "商务", "BD", "渠道", "市场"],
     ["客户经理", "市场/商务岗"],
     "经历里接触过客户或市场"),
    (["财务", "会计", "审计", "预算", "成本"],
     ["财务分析岗", "成本分析岗", "风控/内审岗"],
     "经历里接触过财务或成本"),
]

# 「数据 / 建模」和「论文 / 科研」这两条信号原来不分专业地给结果：
# 食品专业写「乳酸菌发酵 + SPSS 统计分析」，会被推成 BI/报表岗、策略分析岗、算法工程师；
# 任何专业的毕业论文都会被推成 行业研究 / 策略分析 / 咨询分析师。
# 这两条正是「老是偏向计算机、管理岗」的来源，改成按大类分流。
_BIZ_OR_CS = ("管理学（经管大类）", "经济学（经管大类）",
              "工学·计算机与信息类", "理学·数理大类")

DATA_KWS = ["数学建模", "建模", "预测", "xgboost", "python", "sql", "数据",
            "分析", "报表", "指标", "取数"]
DATA_JOBS = {
    True: ["数据分析岗", "BI/报表岗", "策略分析岗", "算法工程师"],
    # 非经管 / 非计算机专业：只给通用的数据岗，不硬塞 BI、策略、算法这些
    # 属于另一个专业的入口名。
    False: ["数据分析岗", "数据运营岗"],
}
PAPER_KWS = ["论文", "研究", "课题", "学术", "治理策略", "实证"]
# 三档而不是两档：人文社科写论文既不该落到咨询分析，也不该落到「研发工程师」。
PAPER_JOBS = {
    "biz": ["行业研究岗", "策略分析岗", "咨询分析师"],
    "stem": ["研发工程师", "技术文档工程师", "专利工程师", "实验技术员"],
    "hum": ["内容编辑", "文案策划", "研究助理"],
}


def paper_jobs(cat):
    if cat in ("管理学（经管大类）", "经济学（经管大类）"):
        return PAPER_JOBS["biz"]
    if cat == "人文社科大类":
        return PAPER_JOBS["hum"]
    return PAPER_JOBS["stem"]

TIER_LABELS = [
    "最贴切：专业 + 实习 + 竞赛 + 论文",
    "已放宽到本专业主流入口",
    "已放宽到相邻方向（有专业门槛，外专业进不来）",
    "已放宽到通用职能岗（大量专业都能投）",
    "已放宽到不限专业岗位（教培等）",
]

# 前两层共用的硬约束：不许把学生推到「换个专业才投得进」的岗上。
# 实测过的两个跑偏：自动化（电气信息）的 T0/T1 混进「管培生」，T2 混进
# 「前端开发工程师」。前者的根因是模型把「求稳」当成了对口，后者的根因是
# 相邻大类整表照搬。这条在提示词里再挡一次。
_NO_CROSS_MAJOR = (
    "**硬约束：学生大类不是经管类时，禁止给「管培生 / 职能管培生 / 储备干部 / "
    "运营培训生 / 业务运营岗」这类不看专业的纯管理岗；学生大类不是计算机/信息类时，"
    "禁止给「软件开发 / 前端 / 后端 / 测试开发 / 数据开发 / 算法」这类编程开发岗**——"
    "除非他上面的实习、竞赛、论文里确实有对应的项目支撑。"
    "这一层的岗位要是「这个专业的人正常会投的」，不是「谁都能投的」。"
)

TIER_RULES = [
    # 0 专业 + 经历
    "岗位要同时贴合专业与下面的实习、竞赛、论文，越具体越贴切越好；"
    "竞赛体现的建模/分析能力、论文体现的研究方向都要用上。" + _NO_CROSS_MAJOR,
    # 1 切合专业
    "忽略实习 / 竞赛 / 论文的具体细节，只按这个专业在校园招聘里的主流入口给岗位。"
    + _NO_CROSS_MAJOR,
    # 2 相邻方向：有专业门槛，但方向偏离
    "给**方向偏离这个专业、但仍要求专业背景**的岗位：本专业的人投得进，"
    "其他大类（文科 / 纯管理 / 商科）投不进来。"
    "例如电气信息大类可以给「电力运维、仪表、电气设计、调试」，化工可以给"
    "「化工设备、中试放大、分析测试」，机械可以给「焊接、热处理、模具、工装」。"
    "**禁止给隔壁专业的核心岗**：机械设计 / 结构 / 仿真对自动化、结构设计对机械，"
    "都不是「放宽」，是换专业——那些岗位筛的就是隔壁的背景，本专业投了会被筛掉。"
    "判断标准：这个岗的招聘要求里写的是「XX 专业」，那 XX 不是学生的大类就不要给。",
    # 3 通用职能岗：本专业能去 + 大量专业都能去
    "给**不看专业细分、大量专业都能投**的通用职能岗：质量、生产管理、生产计划、"
    "采购、供应商管理、技术销售、售前/售后技术支持、项目助理、测试、仓储物流、EHS。"
    "这些岗筛的是通用能力（细致、沟通、抗压、组织），不是某专业的课程。"
    "**禁止出现要专业深度的岗**：研发、设计、算法、工艺开发这些不属于这一层；"
    "**也禁止出现教培岗**（学科教师 / 课程顾问），那是下一层的事。",
    # 4 不限专业
    "给不限专业的校招岗位：**至少 6 个必须是教培机构的岗位**"
    "（学科教师 / 课程顾问 / 学习规划师 / 校区运营管培生 / 教务专员 / 招生专员），"
    "其余可有销售管培、行政人事等；这些岗位都不要求专业对口。"
    "**不要给学生自己专业 / 大类的岗位**（食品专业就别再给食品研发），那些前几层已经给过。",
]

# 大类识别表。顺序有关：先匹配到的赢，所以「食品」要排在「工程」类之前，
# 「化学工程与工艺」这类含多个关键词的专业以更具体的在前为准。
#
# 旧表只有 8 类，且把「材料」同时挂在化学化工与工学下、把「环境」并进医学类；
# 结果是食品 / 机械 / 车辆 / 土木 / 纺织 / 农学这些专业一个都匹配不到，
# major_category 返回「与该专业相近的学科大类」，category_pool 再退到经管表 ——
# 一个食品专业的学生，第二轮就被推荐「管培生 / 职能管培生 / 储备干部」。
# 这不是模型的问题，是词表只覆盖了一个管理学院的 5 个方向和 IT/金融。
CATEGORY_HINT = [
    # 这几条必须排在前面：「电子商务」会被「电子」抓走、「信息管理与信息系统」
    # 会被「信息」抓走，长词优先才不会被短词截胡。
    (["电子商务", "信息管理与信息系统", "工商管理", "市场营销", "会计学", "财务管理",
      "人力资源", "物流管理", "供应链管理", "审计学", "旅游管理"], "管理学（经管大类）"),
    (["食品", "粮食", "乳品", "营养与食品", "食品质量与安全"], "农学·食品大类"),
    (["农学", "作物", "园艺", "植保", "植物保护", "动物", "畜牧", "水产", "兽医",
      "茶学", "林学", "园林", "种子"], "农学·农林牧渔大类"),
    (["化学", "化工", "材料", "冶金", "高分子", "无机非金属", "复合材料"], "理学·化学化工大类"),
    (["数学", "物理", "统计", "天文", "信息与计算科学"], "理学·数理大类"),
    (["计算机", "软件", "人工智能", "数据科学", "大数据", "信息安全", "网络工程",
      "物联网", "数字媒体技术"], "工学·计算机与信息类"),
    (["机械", "车辆", "汽车", "航空", "航天", "船舶", "轮机", "能源", "动力",
      "工程力学", "过程装备", "机器人", "智能制造", "轨道交通"], "工学·机械与运载"),
    (["电气", "自动化", "控制", "通信", "电子", "微电子", "光电", "仪器", "测控"],
     "工学·电气信息"),
    (["土木", "建筑", "水利", "测绘", "地质", "给排水", "工程造价", "工程管理",
      "城乡规划"], "工学·土木建筑"),
    (["环境工程", "环境科学", "安全工程", "矿业", "资源勘查", "消防"], "工学·资源环境与安全"),
    (["纺织", "服装", "轻化", "轻工", "包装", "印刷", "皮革"], "轻工·纺织大类"),
    (["管理", "供应链", "物流", "工业工程", "工商", "会计", "营销", "运营",
      "人力资源", "旅游", "电子商务"], "管理学（经管大类）"),
    (["金融", "经济", "投资", "保险", "财政", "贸易"], "经济学（经管大类）"),
    (["生物", "医学", "临床", "护理", "药学", "中医", "公共卫生", "口腔", "中医"],
     "理学 / 医学大类"),
    (["中文", "外语", "英语", "新闻", "传播", "教育", "历史", "哲学", "法学",
      "社会学", "心理", "汉语言"], "人文社科大类"),
]

# 识别不出大类时的标记。以前这里返回「与该专业相近的学科大类」这种话，
# 下游 category_pool 查不到就默默退到经管表，等于把「不知道」判成了「管培生」。
UNKNOWN_CAT = "未标注大类"

# tier 2 / 3 的规则兜底池（模型失败时也能逐层放宽）
# tier 2「专业大类」层：按学科大类分表——放宽到大类后给的应是「这个大类的毕业生
# 普遍投得进的岗位」，而不是把所有专业都放宽到管理通用岗（旧版单表已被替换）。
# 不收「流程/精益改善岗」「信息系统实施顾问」这类偏单一细分方向的岗位。
CATEGORY_JOBS_MAP = {
    "农学·食品大类": [
        "食品研发工程师", "品控专员", "食品检验员", "生产工艺员", "质量管理专员",
        "化验/质检岗", "研发助理", "感官评价专员", "注册/法规专员", "生产技术员",
        "技术销售工程师", "供应链专员", "微生物检测工程师", "化学分析工程师",
        "质量体系专员", "生产计划专员", "采购专员", "门店/渠道运营专员",
    ],
    "农学·农林牧渔大类": [
        "农艺师", "育种技术员", "植保技术员", "养殖技术员", "饲料配方师",
        "兽医助理", "农产品品控岗", "农业技术推广员", "化验/质检岗",
        "技术销售工程师", "基地管理专员", "食品研发工程师",
    ],
    "理学·化学化工大类": [
        "研发工程师", "工艺工程师", "质量工程师", "化验/质检岗", "分析测试岗",
        "实验室技术员", "EHS安全工程师", "生产管理培训生", "注册专员",
        "技术销售工程师", "专利工程师", "实验员",
    ],
    "理学·数理大类": [
        "数据分析岗", "算法工程师", "软件开发工程师", "统计分析师", "精算助理",
        "风控专员", "量化研究助理", "数据开发工程师", "商业分析岗", "运筹优化岗",
        "机器学习工程师", "数据库工程师",
    ],
    "管理学（经管大类）": [
        "管培生", "职能管培生", "储备干部", "运营培训生", "业务运营岗",
        "产品运营岗", "市场/商务岗", "项目管理岗", "客户成功专员",
        "商业分析岗", "数据分析岗", "经营分析岗",
    ],
    "工学·计算机与信息类": [
        "软件开发工程师", "前端开发工程师", "测试开发工程师", "运维工程师",
        "数据开发工程师", "算法工程师", "网络安全工程师", "嵌入式软件工程师",
        "技术支持工程师", "实施工程师", "产品经理", "数据分析岗",
    ],
    "经济学（经管大类）": [
        "客户经理", "行业研究岗", "风险管理岗", "金融数据分析岗", "信贷专员",
        "合规专员", "投资顾问助理", "管培生（金融方向）", "金融市场岗",
        "财富管理培训生", "审计专员", "运营培训生",
    ],
    "工学大类": [
        "机械工程师", "工艺工程师", "电气工程师", "设备工程师", "质量工程师",
        "生产管理培训生", "技术支持工程师", "自动化工程师", "结构工程师",
        "测试工程师", "采购工程师", "研发工程师",
    ],
    "工学·机械与运载": [
        "机械设计工程师", "结构工程师", "工艺工程师", "设备工程师", "质量工程师",
        "仿真/CAE工程师", "测试工程师", "电气工程师", "技术支持工程师",
        "生产管理培训生", "采购工程师", "研发工程师",
    ],
    "工学·电气信息": [
        "电气工程师", "自动化工程师", "硬件工程师", "嵌入式软件工程师", "测试工程师",
        "工艺工程师", "设备工程师", "技术支持工程师", "质量工程师",
        "系统集成工程师", "电力运维工程师", "研发工程师",
    ],
    "工学·土木建筑": [
        "结构工程师", "土建工程师", "施工技术员", "造价/预算岗", "BIM工程师",
        "安全工程师", "监理员", "测量员", "给排水工程师", "项目管理岗",
        "技术资料员", "质检员",
    ],
    "工学·资源环境与安全": [
        "EHS工程师", "环保工程师", "水处理工程师", "环境检测员", "安全工程师",
        "化验/质检岗", "工艺工程师", "生产技术员", "运维工程师", "检测工程师",
    ],
    "轻工·纺织大类": [
        "纺织工程师", "面料开发工程师", "染整技术员", "工艺工程师", "质量工程师",
        "检测工程师", "生产计划专员", "外贸业务员", "采购专员", "产品设计助理",
    ],
    "理学 / 医学大类": [
        "研发工程师", "实验技术员", "检测工程师", "质量检验员（QC）", "注册专员",
        "医药代表", "临床研究助理", "医药信息沟通专员", "生产技术员", "EHS专员",
        "医学编辑", "生物信息分析工程师",
    ],
    "人文社科大类": [
        "新媒体运营", "内容编辑", "文案策划", "品牌市场专员", "人力资源专员",
        "行政专员", "公关专员", "翻译", "编辑/记者", "教育运营",
        "客户成功专员", "产品运营岗",
    ],
    # 识别不出专业时用这张「中性入口表」：任何专业的应届生都投得进，
    # 且不含管培生 / 储备干部 / 运营培训生这类经管专属入口，
    # 避免「查不到」被悄悄判成「你是管理专业」。
    UNKNOWN_CAT: [
        "生产技术员", "质量管理专员", "化验/质检岗", "检测工程师", "销售专员",
        "采购专员", "市场专员", "行政专员", "人事专员", "客户服务专员",
        "项目助理", "技术文档工程师",
    ],
}
# 未匹配到大类时的兜底：以前这里是「管理学（经管大类）」整张表，
# 于是所有没登记的专业在 tier1/tier2 都被推成管培生 / 业务运营岗。
CATEGORY_JOBS = CATEGORY_JOBS_MAP[UNKNOWN_CAT]


def category_pool(major: str):
    """tier1 相邻岗位 / tier2 大类通用岗位共用：按学科大类取表，查不到才退通用表。"""
    return CATEGORY_JOBS_MAP.get(major_category(major), CATEGORY_JOBS)


# 相邻大类：tier2「放宽到专业大类」如果只给自己那一张表，会和 tier0/tier1 完全重合
# （机械专业的大类表就是机械岗），学生点两次「换一批」就只剩万金油可看。
# 放宽的语义本来就是「隔壁大类也投得进」，所以合并相邻大类的通用岗。
CATEGORY_NEIGHBORS = {
    "工学·机械与运载": ["工学·电气信息", "工学·资源环境与安全"],
    "工学·电气信息": ["工学·机械与运载", "工学·计算机与信息类"],
    "工学·土木建筑": ["工学·资源环境与安全", "工学·机械与运载"],
    "工学·资源环境与安全": ["理学·化学化工大类", "工学·土木建筑"],
    "理学·化学化工大类": ["理学 / 医学大类", "农学·食品大类", "工学·资源环境与安全"],
    "农学·食品大类": ["理学·化学化工大类", "农学·农林牧渔大类"],
    "农学·农林牧渔大类": ["农学·食品大类", "理学 / 医学大类"],
    "轻工·纺织大类": ["理学·化学化工大类", "工学·机械与运载"],
    "理学 / 医学大类": ["理学·化学化工大类", "农学·农林牧渔大类"],
    "工学·计算机与信息类": ["理学·数理大类", "工学·电气信息"],
    "理学·数理大类": ["工学·计算机与信息类", "经济学（经管大类）"],
    "管理学（经管大类）": ["经济学（经管大类）", "人文社科大类"],
    "经济学（经管大类）": ["管理学（经管大类）", "理学·数理大类"],
    "人文社科大类": ["管理学（经管大类）"],
    UNKNOWN_CAT: [],
}


# ── 岗位三档归类：切合专业 / 相邻有门槛 / 通用职能 ──────────────────────
#
# 为什么不再「按大类整表放宽」：旧实现把邻居大类的表整张搬过来，于是自动化
# （电气信息）的第三层给出「机械设计工程师 / 结构工程师 / 仿真工程师」。
# 这三个岗筛的就是机械背景，自动化学生投了会被筛掉——这不叫「放宽」，
# 是让他去投别人的核心岗。真正的放宽应该是**本专业投得进、其他大类进不来**。
#
# 所以改成按岗位的用人要求分三档：
#   L1 切合专业    招聘要求写的就是这个专业 → 本大类表里扣掉下面两档后剩下的
#   L2 相邻有门槛  要求工程技术背景、但方向偏离（电力运维、工艺、焊接、仪表）
#   L3 通用职能    筛通用能力不筛专业（质量、生产管理、采购、销售、项目助理）
# L2 / L3 都是显式名单，不再由「隔壁大类的表」推导——表一合并就会串专业。
NEAR_JOBS = {
    # 电气信息：电力/配电/仪表/调试是「懂电但不在自动化核心」的岗
    "工学·电气信息": ["工艺工程师", "电力运维工程师", "仪表工程师", "电气设计工程师", "调试工程师",
                      "系统集成工程师", "电机设计工程师", "配电设计工程师",
                      "电梯/暖通电气工程师"],
    # 机械：焊接/热处理/模具/工装/装配工艺——招的是机械背景，但方向偏制造工艺，
    # 不放进 L2 的话机械学生第一层就是设计岗，第二层直接跳到通用职能。
    # 「工艺工程师」「设备工程师」不放这里：它们是机械设计的平级出口，属 L1。
    "工学·机械与运载": ["焊接工程师", "热处理工程师", "模具设计工程师",
                        "工装设计工程师", "装配工艺工程师", "铸造工艺工程师",
                        "NVH工程师", "液压工程师", "可靠性工程师"],
    # 土木：施工员/造价/BIM/测量/监理是土木的主战场，属 L1；L2 只留偏专业的
    "工学·土木建筑": ["机电安装工程师", "市政工程师", "试验检测员",
                      "岩土工程师", "隧道/地下结构工程师"],
    "工学·资源环境与安全": ["消防工程师", "职业卫生检测", "给排水工程师",
                            "土壤修复工程师", "碳核查/碳管理岗"],
    # 化工：工艺/化验/分析测试是化工核心出口，属 L1；L2 留偏流程与配方侧的
    "理学·化学化工大类": ["化工设备工程师", "中试/放大工程师", "配方工程师",
                          "实验室技术员", "涂料/胶粘剂研发", "催化研发工程师",
                          "危化品安全工程师"],
    "农学·食品大类": ["中央厨房品控", "食品法规专员", "烘焙研发工程师",
                      "调味品研发工程师", "宠物食品研发", "食品工艺员"],
    "农学·农林牧渔大类": ["测土配方师", "种子检验员", "基地管理员",
                          "农业物联网技术员", "有机认证专员", "粮油检化验员",
                          "农资技术服务"],
    "轻工·纺织大类": ["服装工艺师", "面料跟单员", "纺织品检测员",
                      "印染技术员", "家纺设计师"],
    # 医学：QA/QC、药品注册、药物分析是核心，属 L1；L2 留临床与器械侧
    "理学 / 医学大类": ["临床监查员（CRA）", "医疗器械注册专员",
                        "生物制药工艺工程师", "医学写作", "药品国际注册"],
    "工学·计算机与信息类": ["驻场实施工程师", "IT运维工程师", "实施工程师",
                            "系统集成工程师", "售前解决方案工程师"],
    "理学·数理大类": ["精算助理", "风控建模助理", "量化研究助理",
                      "运筹优化岗", "统计分析师"],
    "管理学（经管大类）": ["内审/风控岗", "HRBP", "供应链计划专员",
                          "经营分析岗", "渠道运营专员"],
    "经济学（经管大类）": ["合规专员", "精算助理", "金融科技产品岗"],
    "人文社科大类": ["企业文化专员", "党群干事", "政府事务专员",
                     "活动策划", "短视频编导", "校对"],
    UNKNOWN_CAT: ["生产技术员", "化验/质检岗", "检测工程师", "技术文档工程师"],
}

# L3：不看专业细分、大量专业都能投的通用职能岗。全局一张表，不按大类分——
# 质量、生产管理、采购、技术销售这些岗，机械 / 化工 / 食品 / 经管的学生都在投，
# 按大类各写一张只会写出四份几乎一样的名单。
BROAD_JOBS = [
    "质量工程师", "质量管理专员", "质量检验员（QC）", "生产管理培训生",
    "生产计划专员", "生产跟单员", "采购专员", "采购工程师",
    "供应商质量管理（SQE）", "技术销售工程师", "售前技术支持工程师",
    "售后技术支持工程师", "项目助理", "测试工程师", "仓储物流专员",
    "EHS专员", "客户服务专员", "技术支持工程师", "供应链专员",
    "质量体系专员", "门店/渠道运营专员", "生产设备操作员",
]

# L1 要扣掉的岗：属于 L2 / L3 的不算「切合专业」。
# 注意是**按大类扣**，不是全局扣：「工艺工程师」对化工是本专业核心出口（L1），
# 对机械才是相邻方向（L2）。全局一张黑名单会把化工的工艺岗也一起扣掉，
# 化工学生的第一层就只剩「研发工程师、注册专员、实验员」。
_CORE_EXCLUDE = set(BROAD_JOBS)


def near_jobs(major):
    """L2：相邻方向（有专业门槛、方向偏离）。"""
    return NEAR_JOBS.get(major_category(major), NEAR_JOBS[UNKNOWN_CAT])


def core_jobs(major):
    """L1：本大类表里扣掉「本大类的相邻岗」和「通用职能岗」之后剩下的。"""
    cat = major_category(major)
    drop = set(NEAR_JOBS.get(cat, [])) | _CORE_EXCLUDE
    return [j for j in category_pool(major) if j not in drop]


NO_MAJOR_JOBS = [
    "学科教师", "课程顾问", "学习规划师", "校区运营管培生", "教务专员",
    "招生专员", "教育产品运营", "销售管培生", "行政/人事专员", "客户成功专员",
    "客服运营专员", "储备干部（不限专业）",
]

LLM_PROMPT = """你是双选会会前规划助手，帮学生想清楚「校园招聘里一般可以投哪些岗位」。

学生专业：{major}
所属学科大类：{category}
【实习】{internships}
【竞赛】{competitions}
【论文 / 科研】{papers}
【技能】{skills}

本轮推荐口径（重要）：{tier_rule}

历次已经展示过、本轮尽量不要重复的岗位：{shown}

【本次双选会·校招公告里明确在招的岗位（优先从这里挑，命中就标 from_db=true；这类会额外打「校招」标）】
{campus_jobs}

【本次双选会·库里有记录的其他来源岗位（企业官网 / 招聘平台等；一样是企业真实在招的岗，命中同样标 from_db=true）】
{other_jobs}

请给出 12 个岗位名称，要求：
1. 基于通用认知回答「这类学生在普遍的校园招聘里一般能投哪些岗位」，
   不要复述某家公司今年的招聘公告，也不要写年份专属说法。
2. 岗位名用中国大陆校招通行的写法：「管培生 / 培训生 / 专员 / 分析师 / 助理 / 工程师」。
   不要输出「运营优化」「系统方案」这类职能描述。
   **档位必须是应届投得进的**：不要写「主管 / 资深 / 高级 / 组长 / 主任 / 总监 / 负责人 / 专家」；
   「经理」只保留校招真正常见的三种（客户经理、产品经理、业务经理），其余经理级一律不写。
   同一类活里也要区分入口档：给学生的是「管培生 / 专员」，不是「供应链主管」。
   **岗位名里不要带地区、行业、产品线、客户名等企业定制限定**：
   「运营产品经理(AI医疗)」「区域销售经理（IVD业务 广东）」「嵌入式软件工程师（心电贴片）」都不合格，
   要写成通用的「运营产品经理」「区域销售经理」「嵌入式软件工程师」。
3. 两栏里与本轮口径对口的岗位名都可作为优先选项（**两栏都算企业真实在招**，来源只影响
   我们打不打「校招」标，不影响算不算命中），去掉企业定制的括号限定后写成通用岗位名，
   命中就把 from_db 标 true；两栏里都没有对口的就自己生成，不要为了凑数硬挑库里的岗位。
4. 依据只能来自上面的专业、大类、实习、竞赛、论文、技能，不要编造经历，不要提企业名称。
5. **竞赛 / 论文信号必须真正落到岗位选择上**：数学建模、预测建模类竞赛要落到
   数据分析 / 算法 / 策略分析 / 商业分析岗；治理策略、机制研究类论文要落到
   行业研究 / 咨询分析 / 供应链分析岗。不要只在 why 里提一句却给无关岗位。
6. 12 个岗位要尽量互不重复，也尽量避开已展示过的岗位。
7. 严格输出 JSON：{{"candidates":[{{"title":"岗位名","why":"一句话依据","from_db":true/false}}]}}
"""


def major_category(major: str) -> str:
    for keys, cat in CATEGORY_HINT:
        if any(k in (major or "") for k in keys):
            return cat
    return UNKNOWN_CAT


def base_jobs(major: str):
    """专业基线岗位：MAJOR_BASE 有就用，否则取所属大类的通用岗。

    旧版兜底写死 ["管培生", "数据分析岗", "项目管理岗", "业务运营岗"]——那是
    管理学院 5 个方向的入口表。食品 / 机械 / 车辆 / 土木 / 纺织 / 农学都不在
    MAJOR_BASE 里，于是「没登记过的工科」被当成管理学院学生推荐，
    31 个候选里一个食品岗都没有。兜底必须跟着大类走，不能跟着经管走。
    """
    major = major or ""
    base = MAJOR_BASE.get(major)
    if not base:
        for k, v in MAJOR_BASE.items():
            if k in major or (major and major in k):
                base = v
                break
    return list(base or category_pool(major)[:8])


def _as_list(xs):
    out = []
    for x in (xs or []):
        if isinstance(x, dict):
            t = str(x.get("text") or x.get("name") or x.get("title") or "").strip()
        else:
            t = str(x or "").strip()
        if t and t not in out:
            out.append(t)
    return out


# 岗位来源等级：只有 LV_CAMPUS 才配叫「这场双选会确实在校招这个岗」。
LV_CAMPUS = 2   # 企业 jobSource=校招公告，或该岗 requirements 里写明「招聘阶段：校招」
LV_OTHER = 1    # 库里有记录，但来源是社招 / 企业官网 / 新闻检索，不等于校招在招


def db_job_index(limit=None):
    """读库内岗位名及其来源等级，返回有序 dict {岗位名: 等级}。

    为什么要分等级：`from_db=true` 是给学生「这家有，去现场投」的承诺。旧版把
    849 个岗位名不分来源一起塞进 prompt，模型只要在清单里看见就标 true ——
    于是「明确社招」和「官网扒来、无 JD」的岗位也被承诺成校招在招。实测 150 条
    from_db=true 里只有 96 条（64%）真有校招依据。

    同名岗位取**最高**等级：只要有一家校招公告在招这个岗位名，这个岗位名就算
    校招有据；全都不是校招才降级为 LV_OTHER。

    limit 默认不截断：原来写死 160，而去重后有 849 个岗位名，等于把 81% 的库内
    岗位挡在 prompt 之外。截断按 SQL 行序而不是相关性，被丢掉的「数据分析师」
    「前端开发工程师」恰恰是软件工程最该看到的岗；模型看不到清单，只能标
    from_db=false，页面上就成了「推荐了一个这家双选会没有的岗位」。
    849 个名字顿号拼起来约 6.7KB，换一次准确得多的判断，值得。
    """
    try:
        import sqlite3
        db = os.path.join(DATA, "careersafari.db")
        if not os.path.isfile(db):
            return {}
        con = sqlite3.connect(db, timeout=8)
        try:
            rows = con.execute(
                "SELECT item_json FROM company_jobs WHERE item_json IS NOT NULL").fetchall()
        finally:
            con.close()
        idx = {}
        for (raw,) in rows:
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            src = str(item.get("jobSource") or "").strip()
            jobs = item.get("jobs")
            if not isinstance(jobs, list):
                continue
            for j in jobs:
                t = (j.get("title") if isinstance(j, dict) else str(j)) or ""
                t = t.strip()
                if not t or len(t) > 30:
                    continue
                req = str((j.get("requirements") if isinstance(j, dict) else "") or "")
                # 岗位级校招证据优先：有的企业整级来源写 websearch，但个别岗位的
                # requirements 里明确写了「招聘阶段：校招」，这一条就是校招有据。
                if src == "校招公告" or re.search(r"招聘阶段[:：]\s*[^；;]*校招", req):
                    lv = LV_CAMPUS
                else:
                    lv = LV_OTHER
                if lv > idx.get(t, 0):
                    idx[t] = lv
                else:
                    idx.setdefault(t, lv)
        if limit:
            return dict(list(idx.items())[:limit])
        return idx
    except Exception:
        return {}


def db_job_titles(limit=None, level=None):
    """库内岗位名列表。level=None 全给；level=LV_CAMPUS 只给校招有据的。"""
    idx = db_job_index(limit=limit)
    return [t for t, lv in idx.items() if level is None or lv >= level]


def _load_env() -> dict:
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


_TITLE_JUNK = re.compile(r"[（(][^)）]*[)）]")


def _clean_title(t):
    """去掉企业定制的括号限定与年份/编号尾巴，保证输出是通用校招岗位名。"""
    t = str(t or "").strip()
    t = _TITLE_JUNK.sub("", t)
    t = re.sub(r"20\d{2}\s*届?(?:校园招聘|秋招|春招)?", "", t)
    t = re.sub(r"MJ\d+", "", t, flags=re.I)
    t = re.sub(r"\s+", "", t)
    return re.sub(r"[·、,，/\-—]+$", "", t).strip()


def _major_ish(title, major):
    """岗位名是否属于「这个专业 / 大类」的岗。用来把专业岗挡在万金油层之外。

    判据两条，都只做字面匹配，不需要懂语义：
      ① 岗位名里带专业名的头两个字（「食品」研发工程师 / 机械设计工程师）；
      ② 岗位名里带本专业基线岗或大类池的核心词（「食品检验」→ 食品检验员）。
    名字太泛（管培生 / 专员）的核心词会被剥空，不会被误判。

    头两个字做通用词排除：「运营与供应链管理」的头两字是「运营」，
    否则「校区运营管培生」这种真万金油会被当成经管专业岗剔掉。
    """
    t = _clean_title(title)
    head = (major or "").strip()[:2]
    if len(head) == 2 and head not in _GENERIC_HEAD and head in t:
        return True
    for kw in base_jobs(major) + category_pool(major):
        core = re.sub(r"[（(][^)）]*[)）]", "", kw)
        core = re.sub(r"(岗|专员|工程师|管培生|经理|助理|分析师|技术员|管理员|员|师)$",
                      "", core)
        if len(core) >= 3 and core in t:
            return True
    return False


# 这些两字开头在专业名里是通用词，不能当「专业特征」用（见 _major_ish）
_GENERIC_HEAD = {"运营", "管理", "工程", "技术", "信息", "科学", "金融", "经济",
                 "数据", "服务", "设计", "教育", "文化", "智能", "系统", "应用"}


def _pool_key(major, cats, tier, accept, model_tag="", use_llm=True, prev=None):
    """池子缓存的键。

    必须带上模型名、提示词版本、**本模块代码指纹**与**是否走模型**：
      · 少了模型名：换模型（glm -> deepseek）后旧池子仍被命中，页面标着
        mode=llm 其实是旧模型结果，看起来像"新模型没生效"；
      · 少了代码指纹：改 MAJOR_BASE / rule_pool / LLM_PROMPT 后旧池子照样命中，
        页面给出的还是改之前的岗位名前缀；
      · 少了 use_llm：模型偶尔超时 / 断网时写进缓存的规则池（12 条泛岗位），
        会在网络恢复后继续被当成「模型结果」发给学生，而且看不出是兜底；
        门禁脚本用 --no-llm 跑一遍也会污染真实学生的模型池。
    """
    raw = json.dumps([major, cats["internships"], cats["competitions"],
                      cats["papers"], cats["skills"], int(tier), bool(accept),
                      model_tag, PROMPT_VER, bool(use_llm), list(prev or []),
                      cache_guard.code_fp(name="job_recommend_pool")],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:20]


def _load_pools():
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pools(pools):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pools, f, ensure_ascii=False)
    except Exception:
        pass


# 每层**不设数量上限**：一层的岗位给完才升层，学生不会因为「配额到 12 个」
# 被提前推到下一层。层与层之间靠内容区分（专业+经历 / 切合专业 / 相邻有门槛 /
# 通用职能 / 不限专业），不靠条数切。
#
# 原先设过 TIER_CAP = [10, 12, 12, 14, 12]，副作用是：T0 被砍到 10 个后，T1 里
# 同一批岗（电气/自动化/硬件…）已在 T0 展示过，去重后 T1 只剩 1~2 条，看起来
# 像是「第二层没了」。现在 L1 只放经历支撑得到的岗，L2 放本专业入口表的其余岗，
# 两者内容本身不重叠，也就不需要靠条数配额来错开。
MIN_FILL = 10

def rule_pool(major, cats, tier, accept, db_jobs, campus_jobs=None):
    """模型不可用时的分层规则池。三类经历都参与 tier 0 的信号匹配。"""
    text = " ".join(cats["internships"] + cats["competitions"] +
                    cats["papers"] + cats["skills"])
    _cset = {str(x).strip() for x in (campus_jobs or []) if str(x).strip()}
    _dbn = {str(x).strip() for x in (db_jobs or []) if str(x).strip()}
    base = base_jobs(major)
    _wide = major_category(major) in _BIZ_OR_CS

    items, seen = [], set()

    def add(title, why, from_db=False, level=""):
        t = _clean_title(title)
        if not t or t in seen:
            return
        seen.add(t)
        items.append({"title": t, "why": why, "from_db": from_db,
                      "db_level": level})

    if tier == 0:
        # 库里真实岗位 + 三类经历命中的方向 + 专业基线
        for dj in (db_jobs or []):
            for kw in base:
                core = re.sub(r"[（(].*?[)）]", "", kw)
                core = re.sub(r"(岗|专员|工程师|管培生|经理|助理|分析师)$", "", core)
                # 核心词至少 3 字：「研发」这种 2 字泛词会把 数据库研发工程师/
                # C++研发工程师 等计算机岗全误吸进来；3 字起才做库内匹配。
                if core and len(core) >= 3 and core in dj:
                    # 库里有就算命中；来源只决定打不打「校招」标，不决定命中与否
                    if dj in _cset:
                        add(dj, "校招公告里在招，与专业方向对口", True, "campus")
                    elif dj in _dbn:
                        add(dj, "企业库在招（非校招公告来源），与专业方向对口",
                            True, "other")
                    break
        for kws, jobs, why in SIGNAL_RULES:
            if any(k.lower() in text.lower() for k in kws):
                for j in jobs:
                    add(j, why)
        # 经历里直接点到的专业岗：实习写「品控部」「工艺科」，就把 品控专员 /
        # 工艺工程师 抬到 L1。这一步只认字面命中，不猜语义，宁可漏也不乱补。
        for kw in base:
            core = re.sub(r"[（(].*?[)）]", "", kw)
            core = re.sub(r"(岗|专员|工程师|管培生|经理|助理|分析师|技术员|员|师)$", "", core)
            if len(core) >= 2 and core in text:
                add(kw, "实习 / 竞赛里做过「%s」，与专业方向直接对得上" % core)
        # 数据 / 论文两条信号按大类分流（见 DATA_JOBS / PAPER_JOBS 上的说明）
        if any(k in text.lower() for k in DATA_KWS):
            for j in DATA_JOBS[bool(_wide)]:
                add(j, "竞赛或论文里用到建模、预测、数据分析")
        if any(k in text for k in PAPER_KWS):
            for j in paper_jobs(major_category(major)):
                add(j, "有论文 / 科研训练，适合%s" % (
                    "研究分析类岗位" if _wide else "研发与技术类岗位"))
        # L1 的语义是「专业 + 经历」：先放经历信号命中的岗。
        # 但信号词表只覆盖供应链/数据/产品/项目/销售/财务这几类，像「PLC 调试、
        # 伺服参数整定、电赛闭环控制」这种很实的工科经历一个词也命中不了，L1 会只剩
        # 「技术文档工程师、专利工程师」这类论文岗。所以信号太稀时用专业入口表兜底，
        # 而且只补到 MIN_FILL 的一半——L2 还要用这张表，L1 全吃掉 L2 就空了。
        if len(items) < MIN_FILL // 2:
            for j in base:
                add(j, "专业「%s」的主流入口，与你上面的经历同方向" % major)
                if len(items) >= MIN_FILL // 2:
                    break
    elif tier == 1:
        # 切合专业：本大类表里扣掉「相邻有门槛」和「通用职能」之后剩下的
        for j in core_jobs(major) or base:
            add(j, "按专业「%s」的主流校招入口" % major)
        # base 里没被归类的也算切合（有些大类的核心岗不在大类表里）
        for j in base:
            if j in _CORE_EXCLUDE:
                continue
            add(j, "与专业「%s」的培养方向对口" % major)
    elif tier == 2:
        # 相邻方向：有专业门槛、方向偏。本专业的人投得进，其他大类进不来。
        for j in near_jobs(major):
            add(j, "方向偏离「%s」，但仍要求本专业背景" % major)
    elif tier == 3:
        # 通用职能：筛能力不筛专业，大量专业都能投
        for j in BROAD_JOBS:
            add(j, "不看专业细分的通用职能岗，大量专业都能投")
    else:
        if not accept:
            return []
        for j in NO_MAJOR_JOBS:
            add(j, "不限专业岗位；你已选择愿意考虑教培类企业")
    return items


def llm_pool(major, cats, tier, accept, key, base_url, model, db_jobs, shown,
             campus_jobs=None):
    _campus = [str(x).strip() for x in (campus_jobs or []) if str(x).strip()]
    _cset = set(_campus)
    _other = [str(x).strip() for x in (db_jobs or [])
              if str(x).strip() and str(x).strip() not in _cset]
    prompt = LLM_PROMPT.format(
        major=major or "未填写",
        category=major_category(major),
        internships="；".join(cats["internships"]) or "无",
        competitions="；".join(cats["competitions"]) or "无",
        papers="；".join(cats["papers"]) or "无",
        skills="、".join(cats["skills"]) or "无",
        tier_rule=TIER_RULES[tier],
        shown="、".join(shown) or "无",
        campus_jobs="、".join(_campus) or "（本轮没读到校招公告岗位）",
        other_jobs="、".join(_other) or "（无）")
    payload = {"model": model,
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.3}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = json.loads(r.read().decode("utf-8"))
        data = _parse_json(raw["choices"][0]["message"]["content"])
        out, seen = [], set()
        for c in data.get("candidates", []):
            if not (isinstance(c, dict) and c.get("title")):
                continue
            t = _clean_title(c["title"])
            if not t or t in seen:
                continue
            seen.add(t)
            out.append({"title": t, "why": str(c.get("why", "")),
                        "from_db": bool(c.get("from_db"))})
        # from_db 是模型的自我声明，实测不可尽信：234 条里 15 条标了「库里在招」，
        # 但清单里根本没有那个名字（数据库管理 / 软件开发工程师 / 行业研究员……）。
        # 这个字段是给学生「这家有，去现场投」的承诺，标错比漏标伤得多——拿着一个
        # 不存在的岗位名去展位转一圈，比不知道更糟。所以用真实清单复核一遍。
        #
        # 口径（重要）：**库里有这个岗位就算命中，不按来源降级**。
        #   校招公告里有 → from_db=true, db_level="campus"（唯一区别：多打一个「校招」标）
        #   其他来源有   → from_db=true, db_level="other" （命中就是命中，只是不打校招标）
        #   库里没有     → from_db=false, db_level="inferred"（模型自己推的，待核实）
        # 来源只用于显示，不参与「算不算命中」——非校招来源的岗位一样是企业真实在招的岗，
        # 把它降级成 false 等于让人错过一个真实机会。折叠是另一套门槛（没命中 且 推断没有）。
        # 认「同名」或「库名里带这个说法」（库里写「数据分析师(北京)」→ 推荐名
        # 「数据分析师」成立）。
        _dbn = {str(x).strip() for x in (db_jobs or []) if str(x).strip()}

        def _in(name, pool):
            return name in pool or any(name in x for x in pool)

        for c in out:
            t = c["title"]
            if _cset and _in(t, _cset):
                c["from_db"], c["db_level"] = True, "campus"
            elif _dbn and _in(t, _dbn):
                c["from_db"], c["db_level"] = True, "other"
            else:
                c["from_db"] = False
                # 读库失败（_dbn 为空）时不要误标成「模型瞎编」
                c["db_level"] = "inferred" if _dbn else ""
        return out or None
    except Exception as e:
        print("[job_recommend] 模型调用失败，回退规则：%s" % e, file=sys.stderr)
        return None


def get_pool(major, cats, tier, accept, cfg, db_jobs, use_llm=True,
             campus_jobs=None):
    """每个 (专业, 三类经历, 层级, 模型) 一个固定池子，缓存到磁盘，避免换一批时反复调模型。

    层的先后顺序是「由紧到松」，同一批岗本来会在好几层都成立，所以高层必须知道
    低层已经给过什么，否则模型的答案会层层重复、去重后高层所剩无几（实测「相邻
    有门槛」那一层只剩 3 个）。这里把低层池子的岗位名作为 shown 一起喂给模型。
    低层池子本身是确定性的（同样的输入 → 同样的缓存），所以这不会破坏缓存键。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
                or "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"

    prev = []
    for i in range(max(0, int(tier))):
        p, _ = get_pool(major, cats, i, accept, cfg, db_jobs, use_llm,
                        campus_jobs=campus_jobs)
        prev.extend(c["title"] for c in p)

    key = _pool_key(major, cats, tier, accept, model, bool(use_llm and api_key),
                    prev=prev)
    with _LOCK:
        pools = _load_pools()
        hit = pools.get(key)
        if isinstance(hit, dict) and hit.get("items"):
            return hit["items"], hit.get("mode", "rules")

    items = None
    if api_key and use_llm:
        items = llm_pool(major, cats, tier, accept, api_key, base_url, model,
                         db_jobs, prev, campus_jobs=campus_jobs)
        if items:
            # 模型可能仍然给出低层已经展示过的岗（prompt 里写了「尽量不重复」，
            # 不是硬保证）。这里按层序剔一遍，保证每一层的池子内部就是干净的。
            _p = set(prev)
            items = [c for c in items if c["title"] not in _p] or None
    mode = "llm" if items else "rules"
    if not items:
        items = rule_pool(major, cats, tier, accept, db_jobs,
                          campus_jobs=campus_jobs)
    elif len(items) < MIN_FILL:
        # 模型常只给 6~8 个：用同层规则池补到 10 个，保证「换一批」本层还有货。
        # 只设下限、不设上限——这一层有多少就给多少，凑够 10 就停，不多塞。
        # 补的时候同样跳过低层已给过的岗，否则补出来的还是上一层的货。
        seen = {c["title"] for c in items} | set(prev)
        for c in rule_pool(major, cats, tier, accept, db_jobs,
                           campus_jobs=campus_jobs):
            if c["title"] not in seen:
                items.append(c)
                seen.add(c["title"])
            if len(items) >= MIN_FILL:
                break
    if tier == 4 and accept:
        # 保底：这一层是「放宽到不限专业」，教培岗位必须真的出现在最前面，
        # 不能全靠模型自觉（实测模型有时只给市场/人事这类泛岗位）。
        #
        # 同时把模型混进来的「本专业岗」剔掉：实测 tier3 会补出「食品研发工程师 /
        # 食品质量工程师」，排在校区运营管培生之后 —— 万金油层里混进最专的岗，
        # 顺序就成了「专业 → 大类 → 万金油 → 又回到专业」，与「最后万金油」相反。
        seed = [{"title": t, "why": "不限专业岗位；你已选择愿意考虑教培类企业",
                 "from_db": False} for t in NO_MAJOR_JOBS[:6]]
        seed_titles = {s["title"] for s in seed}
        items = seed + [c for c in items
                        if c["title"] not in seed_titles
                        and not _major_ish(c["title"], major)]
    with _LOCK:
        pools = _load_pools()
        pools[key] = {"tier": tier, "mode": mode, "items": items,
                      "model": model, "prompt_ver": PROMPT_VER,
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "_cg": cache_guard.stamp("job_recommend_pool", model=model)}
        _save_pools(pools)
    return items, mode


def recommend(major, internships=None, competitions=None, papers=None,
              skills=None, use_llm=True, shown=None, tier=0,
              accept_no_major=False, experiences=None):
    """返回一批 6 个岗位；当前层池子展示完会自动升层。

    shown：采集端历次展示过的岗位名（全局累积），跨层也不重复。
    experiences：兼容旧调用，内容并入实习。
    """
    cats = {
        "internships": _as_list(list(internships or []) + list(experiences or [])),
        "competitions": _as_list(competitions),
        "papers": _as_list(papers),
        "skills": _as_list(skills),
    }
    shown = _as_list(shown)
    try:
        start = max(0, min(4, int(tier)))
    except Exception:
        start = 0
    max_tier = 4 if bool(accept_no_major) else 3

    cfg = _load_env()
    _idx = db_job_index()
    db_jobs = list(_idx)
    # 只有校招公告里在招的岗位名才允许 from_db=true（见 llm_pool 的复核说明）
    campus_jobs = [t for t, lv in _idx.items() if lv >= LV_CAMPUS]

    for t in range(start, max_tier + 1):
        pool, mode = get_pool(major, cats, t, accept_no_major, cfg, db_jobs,
                              use_llm, campus_jobs=campus_jobs)
        fresh = [c for c in pool if c["title"] not in shown]
        if fresh:
            batch = fresh[:6]
            left = len(fresh) - len(batch)
            note = ("第 %d 层「%s」：本层还剩 %d 个岗位，换完会自动放宽到下一层。"
                    % (t + 1, TIER_LABELS[t], left)) if t < max_tier else \
                   ("已是最宽口径「%s」。本层还剩 %d 个。" % (TIER_LABELS[t], left))
            return {"ok": True, "mode": mode, "candidates": batch,
                    "tier": t, "tier_label": TIER_LABELS[t],
                    "relaxed": t > start, "pool_left": left,
                    "accept_no_major": bool(accept_no_major),
                    "notice": note}
    # 所有层都展示完：如实说明，不再编新岗位
    return {"ok": True, "mode": "rules", "candidates": [],
            "tier": max_tier, "tier_label": TIER_LABELS[max_tier],
            "relaxed": True, "pool_left": 0,
            "accept_no_major": bool(accept_no_major),
            "notice": "各层岗位都展示过了，可以手写想投的岗位，或回到上面的问题改条件。"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.startswith("/api/fair/recommend-jobs"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        out = recommend(
            body.get("major", ""),
            internships=body.get("internships") or [],
            competitions=body.get("competitions") or [],
            papers=body.get("papers") or [],
            skills=body.get("skills") or [],
            use_llm=body.get("use_llm", True),
            shown=body.get("shown") or body.get("exclude") or [],
            tier=body.get("tier", 0),
            accept_no_major=bool(body.get("accept_no_major")),
            experiences=body.get("experiences") or [])
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--major", default="运营与供应链管理")
    ap.add_argument("--intern", action="append", default=[])
    ap.add_argument("--competition", action="append", default=[])
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--skill", action="append", default=[])
    ap.add_argument("--tier", type=int, default=0)
    ap.add_argument("--accept-no-major", action="store_true")
    ap.add_argument("--shown", action="append", default=[])
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8100)
    a = ap.parse_args()

    if a.serve:
        print("job_recommend 服务 http://127.0.0.1:%d/api/fair/recommend-jobs" % a.port)
        HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
        return

    intern = a.intern
    comp = a.competition
    paper = a.paper
    if a.demo or not (intern or comp or paper):
        intern = ["中国船舶集团第七一二研究所 供应链管理部实习：供应商评估体系优化、战略寻源、OTD 跟踪与风险预警、采购定价优化"]
        comp = ["美国大学生数学建模大赛 国际级 Finalist(2%) 核心队员：Python 数据清洗、XGBoost 预测建模、异常值处理"]
        paper = ["本科毕业论文《集团型企业供应商共享治理策略研究》：供应商激励机制、信息上报与风险治理"]
    skills = a.skill or ["Python", "R", "SQL", "供应链建模"]

    r = recommend(a.major, internships=intern, competitions=comp, papers=paper,
                  skills=skills, use_llm=not a.no_llm, shown=a.shown,
                  tier=a.tier, accept_no_major=a.accept_no_major)
    print("专业：%s ｜ 层级 %d：%s" % (a.major, r["tier"], r["tier_label"]))
    print("模式：%s ｜ %s" % (r["mode"], r["notice"]))
    print("\n推荐岗位（学生勾选，不写进企业事实字段）：")
    for i, c in enumerate(r["candidates"], 1):
        flag = "【企业库在招】" if c.get("from_db") else ""
        print("  %d. %-20s %s%s" % (i, c["title"], flag, c.get("why", "")))


if __name__ == "__main__":
    main()
