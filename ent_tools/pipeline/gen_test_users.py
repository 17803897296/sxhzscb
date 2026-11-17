# -*- coding: utf-8 -*-
"""生成随机测试用户画像（fair_profile 格式，可直接喂给 demo/fair.html 测排行）。

用途：验收「换一个专业 / 换一套偏好，排行榜是否跟着变」。
产出：
    data/test_users/u1.json … u3.json   完整 fair_profile 文档（可直接 Post 到
                                        /api/fair/profile，或塞进浏览器 localStorage）
    data/test_users/index.json          摘要（专业 / 意向岗位 / 排序 / 权重）
    demo/test_users.html                一键测试页：点一下就带这套画像打开 fair.html

设计约束（都来自真实数据，不是拍脑袋）：
  1. 专业取自 job_recommend.MAJOR_BASE（10 个专业，岗位名统一校招口径）；
  2. 意向岗位是**具体岗位名**，不写「运营优化」这类岗位族；
  3. 排序偏好把「与本专业对口的那一类」放第 1，其余随机 —— 否则随机排序会
     生成「计算机学生把教育排第一」这种没人会填的画像，测不出真问题；
  4. 权重七项合计 100，取整；未选中的维度是 0（不是缺省值），与采集端口径一致；
  5. 老家城市必须能在 province_map 里查到，否则「离家近」整维算不出来。

用法：
    python -X utf8 gen_test_users.py            # 默认 seed=20261115
    python -X utf8 gen_test_users.py --seed 7   # 换一批
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "test_users")

# ---------------------------------------------------------------- 专业族
# ind1 / ct1 / city1 是「这个专业的学生最可能把它们排第一」的类别，
# 用来生成**合理**的排序偏好；其余类别随机排列。
# group = 学科大类。抽人时 group 与 ind1 都不许重复 —— 否则会造出
# 「供应链管理 + 工业工程 + 智能系统管理」这种三套全是管理旁支的画像，
# 换专业等于没换，测不出跨行业的排序问题。
# skills 只做展示，不进打分；意向岗位一律是库里真出现过的具体岗位名，
# 且限定 2/3 档（4/5 档会被层级规则判成 level_blocked，不适合当"意向"）。
FAMILIES = [
    {
        "group": "工学", "major": ["计算机科学与技术", "软件工程"],
        "jobs": ["后端开发工程师", "软件开发工程师", "前端开发工程师",
                 "算法工程师", "数据开发工程师", "测试开发工程师"],
        "ind1": "IT", "ct1": "民企", "city1": "一线",
        "skills": ["Python", "Java", "MySQL", "Git", "Linux"],
        "w": {"industry": 30, "city": 20, "company_type": 15,
              "growth": 10, "scale": 10, "home": 10, "industry_status": 5},
    },
    {
        "group": "财经", "major": ["金融学", "金融服务与金融工程"],
        "jobs": ["管培生（金融方向）", "客户经理", "风险管理岗",
                 "行业研究岗", "金融数据分析岗", "审计专员"],
        "ind1": "金融", "ct1": "央企", "city1": "一线",
        "skills": ["Wind", "Excel 建模", "财务分析", "Python"],
        "w": {"city": 25, "industry": 25, "company_type": 20,
              "home": 10, "industry_status": 10, "scale": 5, "growth": 5},
    },
    {
        "group": "医学", "major": ["临床医学", "药学", "生物医学工程"],
        "jobs": ["医学专员", "临床试验助理", "药物分析研究员",
                 "原料药质量研究员", "医学信息沟通专员", "药品注册专员"],
        "ind1": "医药", "ct1": "民企", "city1": "新一线",
        "skills": ["药理学", "GCP 法规", "HPLC", "临床数据管理"],
        "w": {"industry": 30, "home": 20, "company_type": 15,
              "growth": 15, "city": 10, "scale": 5, "industry_status": 5},
    },
    {
        "group": "土建", "major": ["土木工程", "工程造价", "工程管理"],
        "jobs": ["施工员", "施工管理岗", "结构工程师", "商务造价岗",
                 "机电管理岗", "安全管理岗", "EHS安全工程师"],
        "ind1": "建筑", "ct1": "央企", "city1": "新一线",
        "skills": ["AutoCAD", "广联达", "BIM", "测量放线"],
        "w": {"industry": 25, "company_type": 20, "home": 15,
              "scale": 15, "city": 15, "growth": 5, "industry_status": 5},
    },
    {
        "group": "电气", "major": ["电气工程及其自动化", "能源与动力工程"],
        "jobs": ["电气工程师", "电气设计工程师", "热动集控工程师",
                 "新能源开发工程师", "电力交易专责", "综合能源工程师"],
        "ind1": "能源", "ct1": "国企", "city1": "新一线",
        "skills": ["电力系统分析", "继电保护", "PLC", "AutoCAD"],
        "w": {"industry": 30, "city": 20, "company_type": 20,
              "scale": 10, "home": 10, "growth": 5, "industry_status": 5},
    },
    {
        "group": "师范", "major": ["数学与应用数学（师范）", "汉语言文学（师范）",
                                  "英语（师范）"],
        "jobs": ["初中教师", "学科教师", "班课主讲教师", "教研老师",
                 "物理教师", "小学教师"],
        "ind1": "教育", "ct1": "民企", "city1": "其他省会",
        "skills": ["教师资格证", "课程设计", "板书", "班级管理"],
        "w": {"home": 25, "city": 20, "industry": 20,
              "company_type": 15, "growth": 10, "scale": 5, "industry_status": 5},
    },
    {
        "group": "商科", "major": ["市场营销", "电子商务", "工商管理"],
        "jobs": ["市场专员", "品牌专员", "销售管培生", "KA销售管培生",
                 "电商运营", "市场营销专员"],
        "ind1": "消费", "ct1": "民企", "city1": "一线",
        "skills": ["市场调研", "活动策划", "短视频运营", "Excel"],
        "w": {"city": 25, "industry": 20, "company_type": 15,
              "growth": 15, "scale": 10, "home": 10, "industry_status": 5},
    },
    {
        "group": "机械", "major": ["机械设计制造及其自动化", "材料成型及控制工程"],
        "jobs": ["机械设计工程师", "工艺工程师", "设备工程师",
                 "质量工程师", "生产管培生", "材料研发工程师"],
        "ind1": "制造", "ct1": "民企", "city1": "新一线",
        "skills": ["SolidWorks", "AutoCAD", "工艺文件", "FMEA"],
        "w": {"industry": 30, "home": 15, "scale": 15,
              "industry_status": 15, "city": 15, "company_type": 5, "growth": 5},
    },
    {
        "group": "管理", "major": ["工业工程", "复杂工程与智能管理"],
        "jobs": ["工业工程师", "生产计划岗", "质量工程师",
                 "精益改善岗", "制造管培生", "项目管理岗"],
        "ind1": "制造", "ct1": "国企", "city1": "新一线",
        "skills": ["精益生产", "Minitab", "AutoCAD", "项目管理"],
        "w": {"industry": 30, "home": 15, "scale": 15,
              "industry_status": 15, "city": 15, "company_type": 5, "growth": 5},
    },
    {
        "group": "管理", "major": ["运营与供应链管理", "管理科学与工程"],
        "jobs": ["供应链管培生", "采购寻源专员", "供应链计划专员",
                 "物流专员", "运营管理培训生", "数据分析岗"],
        "ind1": "制造", "ct1": "民企", "city1": "新一线",
        "skills": ["SAP MM", "Excel", "供应链建模", "采购谈判"],
        "w": {"city": 25, "home": 20, "industry": 20,
              "company_type": 15, "scale": 10, "industry_status": 5, "growth": 5},
    },
    {
        "group": "管理", "major": ["大数据分析与决策"],
        "jobs": ["数据分析岗", "BI/报表岗", "数据运营岗",
                 "数据开发岗", "策略分析岗"],
        "ind1": "IT", "ct1": "民企", "city1": "一线",
        "skills": ["SQL", "Python", "Tableau", "Hive"],
        "w": {"industry": 25, "city": 20, "company_type": 15,
              "growth": 15, "scale": 10, "home": 10, "industry_status": 5},
    },
    {
        "group": "管理", "major": ["智能系统管理"],
        "jobs": ["产品经理", "数据分析岗", "系统运营岗",
                 "ERP/实施顾问", "信息管理岗"],
        "ind1": "IT", "ct1": "外企", "city1": "新一线",
        "skills": ["Axure", "SQL", "ERP", "需求分析"],
        "w": {"industry": 25, "company_type": 20, "city": 20,
              "scale": 15, "home": 10, "growth": 5, "industry_status": 5},
    },
]

CITIES_BY_REGION = {
    "华东": ["南京", "苏州", "无锡", "杭州", "宁波", "合肥", "济南", "青岛"],
    "华北": ["北京", "天津", "石家庄", "太原"],
    "华中": ["武汉", "长沙", "郑州", "南昌"],
    "华南": ["广州", "深圳", "福州", "厦门"],
    "西南": ["成都", "重庆", "贵阳", "昆明"],
    "西北": ["西安", "兰州"],
    "东北": ["沈阳", "大连", "哈尔滨"],
}

# 三个维度的**默认梯度**（与学生真实填写习惯一致）。生成时把本专业首选提到第 1，
# 其余保持这个梯度，再做 1~2 次相邻微调——纯随机会造出「交通发达排第 2、新一线排第 5」
# 这种没人会填的画像，拿它测排序等于测垃圾。
CITY_CATS = ["一线", "新一线", "其他省会", "经济发达", "交通发达", "生态友好", "其他"]
CT_CATS = ["央企", "国企", "民企", "外企", "事业单位"]
IND_CATS = ["IT", "金融", "制造", "建筑", "医药", "能源", "教育", "消费"]

VALUE_KEYS = ["salary", "stability", "growth", "autonomy", "impact", "balance"]


def _ranked(first, pool, rnd):
    """本专业首选排第 1，其余沿默认梯度，再做少量相邻微调。

    不用全随机：真实问卷里城市/类型/行业的排序是「有梯度、个别位次调换」，
    全随机会生成「交通发达 > 新一线」「事业单位 > 国企」这类没人会那样填的画像，
    测出来的名次问题就分不清是逻辑错还是输入本身不合理。
    """
    order = [x for x in pool if x != first]
    order = [first] + order
    # 最多换 1 对相邻位次：真实问卷里是「个别位次调换」，连换两对会造出
    # 「一线 > 其他省会 > 经济发达 > 新一线」这种没人会那样填的排序。
    pos = rnd.randrange(1, len(order) - 1)
    if rnd.random() < 0.6:
        order[pos], order[pos + 1] = order[pos + 1], order[pos]
    return {k: i + 1 for i, k in enumerate(order)}


def _weights(base, rnd):
    """在族倾向权重上做扰动，取整后补齐到 100。"""
    w = {}
    for k, v in base.items():
        jitter = rnd.choice([-5, -3, 0, 0, 3, 5])
        w[k] = max(0, v + jitter)
    # 随机把 0~2 个「非核心维」清零：真实问卷里学生经常只勾 3~5 项。
    # 但城市、行业是这套排行的主判据，不能被测试数据清零——否则测出来的是
    # 「权重为 0 的极端情形」，不是正常学生的结果，问题也分不清是逻辑错还是输入怪。
    soft = [k for k in w if k not in ("city", "industry")]
    for k in rnd.sample(soft, rnd.randint(0, 2)):
        w[k] = 0
    total = sum(w.values()) or 1
    w = {k: int(round(v / total * 100)) for k, v in w.items()}
    diff = 100 - sum(w.values())
    if diff:
        # 补给当前权重最大的维度，避免出现小数
        top = max(w, key=lambda k: w[k])
        w[top] += diff
    return w


def _values_from_weights(w, rnd):
    """六维价值观（问卷展示用）：与七维权重大体呼应，再随机微调。"""
    v = {}
    for k in VALUE_KEYS:
        v[k] = rnd.randint(0, 100)
    mapping = {"growth": w.get("growth", 0) * 10, "impact": w.get("industry_status", 0) * 10,
               "stability": w.get("home", 0) * 8, "salary": w.get("company_type", 0) * 8}
    for k, val in mapping.items():
        v[k] = max(0, min(100, int((val + v[k]) / 2)))
    return v


def build(seed=20261115, inds=None):
    """inds: 指定行业首位（如 ["医药","建筑","能源"]）；None 则按「学科尽量不撞」抽。"""
    rnd = random.Random(seed)
    fams = FAMILIES[:]
    rnd.shuffle(fams)
    if inds:
        by_ind = {}
        for f in fams:
            by_ind.setdefault(f["ind1"], []).append(f)
        picked = []
        for ind in inds:
            pool = [f for f in by_ind.get(ind, []) if f["group"] not in
                    [p["group"] for p in picked]]
            if pool:
                picked.append(rnd.choice(pool))
        assert len(picked) == len(inds), "行业 %s 里有的抽不出可用专业族" % inds
    else:
        # 非管理类优先：先把「管理」放最后，保证三套画像学科跨得开
        order = [f for f in fams if f["group"] != "管理"] + \
                [f for f in fams if f["group"] == "管理"]
        picked, used_g, used_i = [], set(), set()
        for f in order:
            if f["group"] in used_g or f["ind1"] in used_i:
                continue
            picked.append(f)
            used_g.add(f["group"])
            used_i.add(f["ind1"])
            if len(picked) == 3:
                break
        for f in order:                 # 学科不够分时按顺序补齐
            if len(picked) == 3:
                break
            if f not in picked:
                picked.append(f)

    regions = rnd.sample(list(CITIES_BY_REGION), len(picked))
    users = []
    for i, (f, rg) in enumerate(zip(picked, regions), 1):
        major = rnd.choice(f["major"])
        pool = f["jobs"][:]
        rnd.shuffle(pool)
        n = rnd.randint(4, min(5, len(pool)))
        jobs = pool[:n]
        home = rnd.choice(CITIES_BY_REGION[rg])
        w = _weights(f["w"], rnd)
        values = _values_from_weights(w, rnd)
        skills = rnd.sample(f["skills"], rnd.randint(3, min(4, len(f["skills"]))))
        uid = "FAIR-TEST-U%d" % i
        name = "测试用户 %s（%s）" % ("ABC"[i - 1], major)
        doc = {
            "document_type": "fair_profile",
            "document_version": "1.1.0",
            "schema_version": "1.1",
            "generated_at": "2026-11-15T02:30:00+08:00",
            "fair_id": uid,
            "collector": {"version": "0.2.0", "engine": "test-user-generator"},
            "privacy": {"contact_masked": True,
                        "storage": "浏览器 localStorage（测试用，不写库）"},
            "consent": {"match": True},
            "identity": {"name": name, "major": major, "degree": "本科",
                         "school": "测试院校", "graduation_year": "2027"},
            "fair": {"event_date": "2026-11-20",
                     "target_stage": "2027 届",
                     "stage_detail": "应届校招",
                     "flagship_experience": "",
                     "target_company_types": [f["ct1"]],
                     "home_city": home},
            "profile": {"skills": skills, "values": values},
            "answers": {
                "intended_jobs": jobs,
                "tri_weights": w,
                "direction_interest": jobs[0],
                "region": home,
                "timeline": "2027 年 7 月入职",
                "major_fit": "专业对口优先",
                "values": values,
                "skills": skills,
                "city_rank": _ranked(f["city1"], CITY_CATS, rnd),
                "company_type_rank": _ranked(f["ct1"], CT_CATS, rnd),
                "industry_rank": _ranked(f["ind1"], IND_CATS, rnd),
                "hard_filter": "",
                "accept_no_major": False,
            },
            "resume": {"uploaded": False, "extraction": {}},
            "quality": {"score": 86, "level": "可直接进入匹配", "missing": [],
                        "warnings": ["测试画像：由 gen_test_users.py 随机生成，不代表真实学生"]},
            "summary": {
                "headline": "%s · %s · 应届校招" % (home, major),
                "traits": [
                    "意向岗位：" + "、".join(jobs),
                    "偏好排序（1=最优先）：城市 " + f["city1"]
                    + " / 类型 " + f["ct1"] + " / 行业 " + f["ind1"],
                    "老家城市：" + home,
                    "技能：" + "、".join(skills),
                ],
            },
            "notes": ["测试画像，仅用于验收排行与匹配逻辑"],
            "downstream": {"match_ready": True},
        }
        users.append(doc)

    # 摘要
    idx = []
    for d in users:
        a = d["answers"]
        idx.append({
            "fair_id": d["fair_id"], "name": d["identity"]["name"],
            "major": d["identity"]["major"], "home_city": d["fair"]["home_city"],
            "intended_jobs": a["intended_jobs"],
            "tri_weights": a["tri_weights"],
            "city_rank": a["city_rank"],
            "company_type_rank": a["company_type_rank"],
            "industry_rank": a["industry_rank"],
        })
    return users, idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261115)
    ap.add_argument("--ind", default="医药,建筑,能源",
                    help="行业首位，逗号分隔（默认 医药,建筑,能源；可用 "
                         "IT,金融,制造,建筑,医药,能源,教育,消费）")
    args = ap.parse_args()
    inds = [x.strip() for x in args.ind.split(",") if x.strip()]
    os.makedirs(OUT, exist_ok=True)
    users, idx = build(args.seed, inds)
    for i, d in enumerate(users, 1):
        p = os.path.join(OUT, "u%d.json" % i)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print("写 %s（%s / %s）" % (p, d["identity"]["major"], d["fair"]["home_city"]))
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "users": idx}, f, ensure_ascii=False, indent=2)
    print("\n=== 摘要 ===")
    for u in idx:
        print("%s  %s" % (u["fair_id"], u["name"]))
        print("   老家 %s | 意向 %s" % (u["home_city"], "、".join(u["intended_jobs"])))
        print("   权重 %s（合计 %d）" % (
            " ".join("%s%d" % (k, v) for k, v in u["tri_weights"].items() if v),
            sum(u["tri_weights"].values())))
        print("   城市1=%s 类型1=%s 行业1=%s" % (
            min(u["city_rank"], key=u["city_rank"].get),
            min(u["company_type_rank"], key=u["company_type_rank"].get),
            min(u["industry_rank"], key=u["industry_rank"].get)))


if __name__ == "__main__":
    main()
