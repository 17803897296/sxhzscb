# -*- coding: utf-8 -*-
"""医药细分产业带：把「医药」这一个行业枚举拆成几条产业带。

2026-11-18 用户实测（生物专业）：11 家上榜企业全是「医药」，其中科伦（大输液）、
久安（注射剂）、普洛（原料药）排在最前，真正做疫苗 / 细胞基因治疗的挤在第 3；
还有两家名字带「生物」但主营是纺织印染助剂与卫生杀虫剂。

用户的原话：
  「药企和化学明显对胃，生物的话应该是生物制药更对胃吧」

也就是说：行业枚举里「医药」只有一格，学生择业时却是几条不同的产业带——生物
对的是生物药（抗体 / 疫苗 / 细胞 / 基因 / 发酵），化学对的是原料药 / 中间体 /
制剂。行业码判到这里就断了，剩下的必须看**主营产品**，而不是企业名里的两个字。

口径：
  · 只判「医药」这个行业码下的企业（`industry == "医药"`），别的行业不参与，
    免得把食品厂的发酵车间、化工厂的助剂线也拖进来；
  · 判据只看主营描述（细分业务 + 经营范围 + 岗位简介），**企业名不算证据**
    ——「多恩生物科技」做的是印染助剂，名字里的「生物」是工商名不是业务；
  · 只影响排序（同层内的先后）与名次理由，**不删企业**：化学药企业对学生物的人
    不是错，只是隔一层；同理生物药企业对学生化的人也一样。
"""
import re

# 产业带：顺序即并列时的优先级（同位置命中取靠前的）
BAND_BIOLOGIC = "生物药"
BAND_CHEMICAL = "化学药"
BAND_TCM = "中药"
BAND_DEVICE = "器械诊断"
BAND_CHANNEL = "医药流通"
BAND_OTHER = "非医药"
BAND_UNKNOWN = "未细分"

LABELS = {
    BAND_BIOLOGIC: "生物药（抗体/疫苗/细胞基因治疗）",
    BAND_CHEMICAL: "化学药（原料药/中间体/制剂）",
    BAND_TCM: "中药（中成药/饮片/配方颗粒）",
    BAND_DEVICE: "器械诊断（IVD/检测试剂/医械）",
    BAND_CHANNEL: "医药流通（连锁药房/批发）",
    BAND_OTHER: "非医药（化工助剂/食品保健/医美）",
    BAND_UNKNOWN: "医药（未细分）",
}

# 词表按「越具体越优先」排序：同一条主营里既有「食品」又有「检测试剂」时，
# 判断依据是「检测试剂」——那是这家企业真正在卖的东西（上成生物就是这样）。
_DEVICE_DECISIVE = (
    "体外诊断", "IVD", "诊断试剂", "检测试剂", "试剂盒", "试纸条", "检测卡",
    "化学发光", "免疫分析",
)
_DEVICE = _DEVICE_DECISIVE + ("医疗器械", "医疗设备", "医用耗材", "神经调控", "刺激器")
_BIOLOGIC = (
    "生物药", "生物制品", "生物制剂", "抗体", "单抗", "疫苗", "细胞治疗",
    "基因治疗", "细胞基因", "重组蛋白", "血液制品", "生物类似药", "细胞培养",
    "发酵", "药效学", "CRO", "生物安全", "基因编辑", "mRNA", "核酸药物",
)
_TCM = ("中药", "中成药", "饮片", "中药材", "配方颗粒", "经典名方")
_CHEMICAL = (
    "原料药", "中间体", "仿制药", "制剂", "注射剂", "输液", "片剂", "胶囊",
    "化学药", "化学合成", "CDMO", "手性", "催化剂", "医药包装",
)
_CHANNEL = ("药品零售", "医药零售", "连锁药房", "药店", "药房", "药品批发",
            "医药流通", "医药商业")
_OTHER = (
    "纺织", "印染", "面料", "助剂", "表面活性剂", "农药", "杀虫", "白蚁", "灭鼠",
    "驱蚊", "饲料", "食品", "保健", "营养", "美容", "医美", "化妆品", "日化",
)

# 判词表：(产业带, 词表)；顺序即「同一位置并列」时的优先级
_BANDS = (
    (BAND_BIOLOGIC, _BIOLOGIC),
    (BAND_CHEMICAL, _CHEMICAL),
    (BAND_TCM, _TCM),
    (BAND_CHANNEL, _CHANNEL),
    (BAND_OTHER, _OTHER),
    # 器械排在最后只影响「同一位置并列」；正常情况按最早出现定主业，
    # 益丰「药品零售…医疗器械」仍是流通，不会被「医疗器械」抢走。
    (BAND_DEVICE, _DEVICE),
)

# 专业 → 产业带排序（数值小 = 更对胃）。判不出专业的返回 None：不参与排序。
# 只登记「方向本身就分带」的专业；化学 / 药学不在此列——化学药与原料药本来就是
# 它们的主场，现在的榜单已经按分数排得好好的，不动（用户 2026-11-18 定的范围）。
_PREF = {
    # 生物类：生物药 > 器械诊断 > 化学药（原料药/制剂）> 非医药
    "生物药方向": {BAND_BIOLOGIC: 0, BAND_DEVICE: 1, BAND_UNKNOWN: 1,
                BAND_CHEMICAL: 2, BAND_TCM: 2, BAND_CHANNEL: 2, BAND_OTHER: 3},
    # 生物医学工程 / 医疗器械：以器械诊断为主
    "器械方向": {BAND_DEVICE: 0, BAND_BIOLOGIC: 1, BAND_UNKNOWN: 1,
              BAND_CHEMICAL: 2, BAND_TCM: 2, BAND_CHANNEL: 2, BAND_OTHER: 3},
}

# 匹配顺序有讲究：生物医学工程要先于「生物」，否则会被判成生物药方向
_FAMILY = (
    ("器械方向", ("生物医学工程", "医疗器械", "医学影像", "智能医学工程")),
    ("生物药方向", ("生物", "微生物", "生命科学", "生物技术", "生物工程",
                 "生物科学", "生物制药", "生物化工")),
)


def family_of(major):
    """专业 → 偏好族（'生物药方向' / '器械方向'）；判不出返回 None。"""
    m = str(major or "").strip()
    if not m:
        return None
    for fam, keys in _FAMILY:
        if any(k in m for k in keys):
            return fam
    return None


def _first_pos(text, words):
    """text 里最早出现的词与位置；都没有返回 (None, -1)。"""
    best, pos = None, -1
    for w in words:
        p = text.find(w)
        if p < 0:
            continue
        if pos < 0 or p < pos or (p == pos and len(w) > len(best or "")):
            best, pos = w, p
    return best, pos


def classify(industry, segment="", scope="", brief=""):
    """主营描述 → {band, label, kw, why}。

    只对「医药」行业码生效：别的行业返回 band=None，调用方据此跳过
    —— 行业码是建库时打好的枚举，这里不越权重判行业。
    """
    if str(industry or "").strip() != "医药":
        return {"band": None, "label": "", "kw": "", "why": ""}
    text = re.sub(r"\s+", "", " ".join(
        str(x or "") for x in (segment, scope, brief)))
    if len(text) < 4:
        return {"band": BAND_UNKNOWN, "label": LABELS[BAND_UNKNOWN], "kw": "",
                "why": "主营描述没采到，分不出是哪条产业带"}

    # ① 试剂 / 体外诊断是决定性证据：一家做检测试剂的公司，主营简介里
    #    往往还写着「食品」「农药残留」这些应用场景，那些不是它的主业。
    kw, _pos = _first_pos(text, _DEVICE_DECISIVE)
    if kw:
        return _hit(BAND_DEVICE, kw)

    # ② 其余按「最早出现」定主业：主营开头的往往才是主业
    #    （华海「仿制药、生物药」→ 化学药；科伦「大输液」→ 化学药）。
    best = None
    for band, words in _BANDS:
        kw, pos = _first_pos(text, words)
        if pos < 0:
            continue
        if best is None or pos < best[1]:
            best = (band, pos, kw)
    if best:
        return _hit(best[0], best[2])
    return {"band": BAND_UNKNOWN, "label": LABELS[BAND_UNKNOWN], "kw": "",
            "why": "主营描述里没有产业带特征词"}


def _hit(band, kw):
    return {"band": band, "label": LABELS[band], "kw": kw,
            "why": "主营命中「%s」" % kw}


def annotate(major, info):
    """给一行报告补「细分方向」结论：{band, label, rank, why}。

    rank 为 None 表示这个专业不按细分排（化学 / 药学 / 判不出专业的），
    排序键上按 0 处理即等价于不参与。
    """
    info = info or {}
    band = info.get("band")
    fam = family_of(major)
    if not band or not fam:
        return {"band": band or "", "label": info.get("label") or "",
                "rank": None, "why": info.get("why") or ""}
    order = _PREF[fam]
    rank = order.get(band, 1)
    if band == BAND_UNKNOWN:
        return {"band": band, "label": info.get("label") or "",
                "rank": rank, "why": info.get("why") or ""}
    tail = {0: "与你这个方向同带", 1: "和你方向相邻", 2: "隔一层（偏化学药/中药）",
            3: "不是医药这行"}.get(rank, "")
    return {"band": band, "label": info.get("label") or "",
            "rank": rank,
            "why": "%s；%s" % (info.get("why") or info.get("label") or band, tail)}
