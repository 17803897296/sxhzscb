"""把大模型查回来的原始资料整理成 research_cache.json。

只做三件事：
1. 枚举归一（"民营企业" -> "民企"）
2. 数字归一（"60人" -> 60，"若干" -> "未知"）
3. 自动从 text 里截 jobEvidence，截不到就按规则降级

纯标准库，不联网，路径全部相对 data/。
"""

import argparse
import json
import sys
from pathlib import Path

UNKNOWN = "未知"

NATURE_MAP = {
    "央企": "央企", "中央企业": "央企", "中央国有企业": "央企",
    "国企": "国企", "国有企业": "国企", "国资": "国企", "国有": "国企", "省属国企": "国企", "市属国企": "国企",
    "民企": "民企", "民营": "民企", "民营企业": "民企", "私营": "民企", "私营企业": "民企",
    "股份制": "民企", "股份制企业": "民企", "上市公司": "民企",
    "外企": "外企", "外资": "外企", "外商独资": "外企", "外资企业": "外企",
    "合资": "合资", "中外合资": "合资", "合资企业": "合资",
    "事业单位": "事业单位", "高校": "事业单位", "医院": "事业单位", "科研院所": "事业单位",
}

INDUSTRY_MAP = {
    "制造": "制造", "制造业": "制造", "机械": "制造", "机械制造": "制造", "电子": "制造",
    "汽车": "制造", "装备": "制造", "装备制造": "制造", "电气": "制造", "电力设备": "制造",
    "医药": "医药", "医疗": "医药", "医疗器械": "医药", "制药": "医药", "生物医药": "医药", "生物": "医药",
    "IT": "IT", "互联网": "IT", "软件": "IT", "信息技术": "IT", "计算机": "IT",
    "通信": "IT", "通信设备": "IT", "电子信息": "IT",
    "金融": "金融", "银行": "金融", "证券": "金融", "保险": "金融", "基金": "金融",
    "建筑": "建筑", "建筑施工": "建筑", "工程": "建筑", "土木": "建筑", "房地产": "建筑",
    "能源": "能源", "电力": "能源", "石油": "能源", "新能源": "能源", "化工": "能源",
    "消费": "消费", "零售": "消费", "快消": "消费", "食品": "消费", "服装": "消费", "家电": "消费",
    "物流": "物流", "快递": "物流", "运输": "物流", "供应链": "物流",
    "教育": "教育", "培训": "教育", "学校": "教育",
    "政府事业": "政府事业", "政府": "政府事业", "事业单位": "政府事业", "公共管理": "政府事业",
}

JOB_SOURCE_MAP = {
    "校招公告": "校招公告", "校招": "校招公告", "校园招聘": "校招公告", "校招简章": "校招公告",
    "招聘公告": "校招公告", "2026届校招": "校招公告",
    "社招信息": "社招信息", "社招": "社招信息", "社会招聘": "社招信息", "招聘平台": "社招信息",
    "websearch": "websearch", "联网搜索": "websearch", "网络搜索": "websearch",
    "公开页面": "websearch", "搜索引擎": "websearch",
    "未知1": "未知1", "未知2": "未知2",   # v18 无具体岗两档（由分类脚本打标，非模型直接输出）
    "主营业务推测": "主营业务推测", "推测": "主营业务推测", "推断": "主营业务推测", "业务推测": "主营业务推测",
}

URGENCY_MAP = {
    "高": "高", "急": "高", "急聘": "高", "急招": "高", "热招": "高", "紧急": "高", "扩招": "高", "急需": "高",
    "中": "中", "常规": "中", "正常": "中", "一般": "中",
    "低": "低", "不急": "低", "长期": "低",
}

JOB_KEYS = ("title", "requirements", "headcount", "urgency", "group", "salary")
CACHE_KEYS = ("name", "nature", "city", "industry", "jobs", "jobSource",
              "jobEvidence", "source", "date", "text")


def norm(value, table, field, report, name):
    raw = UNKNOWN if value is None else str(value).strip()
    if not raw or raw == UNKNOWN:
        return UNKNOWN
    if raw in table:
        return table[raw]
    key = raw.replace(" ", "").replace("／", "/")
    if key in table:
        return table[key]
    for pattern, target in table.items():
        if pattern and pattern in key:
            report.append("%s：%s「%s」按包含关系归一到「%s」" % (name, field, raw, target))
            return target
    report.append("%s：%s「%s」无法识别，置为「未知」" % (name, field, raw))
    return UNKNOWN


def coerce_count(value, report, name):
    if isinstance(value, bool):
        report.append("%s：headcount 为布尔，置为「未知」" % name)
        return UNKNOWN
    if isinstance(value, int):
        return value if value >= 0 else UNKNOWN
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    if not text or text in ("若干", "若干人", "不限", "数名", "多名"):
        return UNKNOWN
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        if digits != text:
            report.append("%s：headcount「%s」提取为 %s" % (name, text, digits))
        return int(digits)
    return UNKNOWN


def pick_evidence(text, given, jobs, report, name):
    """返回 (evidence, ok)。ok=False 表示推测来源拿不到可验证原文，必须降级。"""
    if given and given in text:
        return given, True
    if given:
        report.append("%s：给定 jobEvidence 不是 text 的子串，尝试自动截取" % name)
    for job in jobs:
        title = job.get("title") or ""
        if title and title != UNKNOWN:
            for sentence in split_sentences(text):
                if title in sentence:
                    report.append("%s：从 text 截出含「%s」的句子作为 jobEvidence" % (name, title))
                    return sentence, True
    for sentence in split_sentences(text):
        if sentence and sentence != UNKNOWN:
            report.append("%s：未能对上岗位名，退用首句作为 jobEvidence" % name)
            return sentence, True
    return UNKNOWN, False


def split_sentences(text):
    if not text or text == UNKNOWN:
        return []
    buffer, out = "", []
    for ch in text:
        buffer += ch
        if ch in "。；;！？\n":
            if buffer.strip():
                out.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def build_entry(item, report):
    name = str(item.get("name") or "").strip()
    text = str(item.get("text") or UNKNOWN).strip() or UNKNOWN

    nature = norm(item.get("nature"), NATURE_MAP, "nature", report, name)
    industry = norm(item.get("industry"), INDUSTRY_MAP, "industry", report, name)
    job_source = norm(item.get("jobSource"), JOB_SOURCE_MAP, "jobSource", report, name)

    jobs = []
    for raw in (item.get("jobs") or []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title or title == UNKNOWN:
            report.append("%s：有岗位缺 title，已丢弃该条" % name)
            continue
        jobs.append({
            "title": title,
            "requirements": str(raw.get("requirements") or UNKNOWN).strip() or UNKNOWN,
            "headcount": coerce_count(raw.get("headcount"), report, name),
            "urgency": norm(raw.get("urgency"), URGENCY_MAP, "urgency", report, name),
            "group": str(raw.get("group") or UNKNOWN).strip() or UNKNOWN,
            "salary": str(raw.get("salary") or UNKNOWN).strip() or UNKNOWN,
        })

    evidence = str(item.get("jobEvidence") or "").strip()
    evidence, ok = pick_evidence(text, evidence, jobs, report, name)

    if job_source == "主营业务推测":
        if not ok:
            report.append("%s：推测来源但截不到原文，整条降级为「未知」并清空岗位" % name)
            job_source, jobs, evidence = UNKNOWN, [], UNKNOWN
        elif str(item.get("source") or "").strip() in ("", UNKNOWN):
            report.append("%s：推测来源但没写 source，按「未知」处理" % name)
            job_source, jobs, evidence = UNKNOWN, [], UNKNOWN

    if job_source == UNKNOWN:
        if jobs:
            report.append("%s：来源未知却有岗位，已清空岗位" % name)
        jobs = []
        evidence = UNKNOWN

    if not jobs:
        evidence = UNKNOWN if job_source == UNKNOWN else evidence

    entry = {
        "name": name,
        "nature": nature,
        "city": str(item.get("city") or UNKNOWN).strip() or UNKNOWN,
        "industry": industry,
        "jobs": jobs,
        "jobSource": job_source,
        "jobEvidence": evidence,
        "source": str(item.get("source") or UNKNOWN).strip() or UNKNOWN,
        "date": str(item.get("date") or UNKNOWN).strip() or UNKNOWN,
        "text": text,
    }
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description="大模型原始资料 -> research_cache.json")
    parser.add_argument("--raw", default="data/raw_research.json")
    parser.add_argument("--names", default="data/company_names.json")
    parser.add_argument("--out", default="data/research_cache.json")
    parser.add_argument("--report", default="data/build_report.md")
    args = parser.parse_args(argv)

    root = Path(".")
    raw_path, names_path = root / args.raw, root / args.names
    if not raw_path.exists():
        print("找不到 " + args.raw, file=sys.stderr)
        return 1

    raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        raw = raw.get("companies", raw.get("items", []))
    if not isinstance(raw, list):
        print("raw_research.json 顶层必须是数组", file=sys.stderr)
        return 1

    names = []
    if names_path.exists():
        names = json.loads(names_path.read_text(encoding="utf-8-sig"))

    report, entries, seen = [], [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            report.append("有一条记录没有 name，已跳过")
            continue
        if name in seen:
            report.append("%s：缓存里重复出现，保留第一条" % name)
            continue
        seen.add(name)
        entries.append(build_entry(item, report))

    # 名单里有、资料里没有的企业，补一条全未知
    for name in names:
        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            entries.append({
                "name": name, "nature": UNKNOWN, "city": UNKNOWN, "industry": UNKNOWN,
                "jobs": [], "jobSource": UNKNOWN, "jobEvidence": UNKNOWN,
                "source": UNKNOWN, "date": UNKNOWN, "text": UNKNOWN,
            })
            report.append("%s：名单里有但没查到资料，整条填「未知」" % name)

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    full = sum(1 for e in entries if e["jobs"])
    report_path = root / args.report
    lines = ["# 资料整理报告", "",
             "- 名单企业：%d" % len(names),
             "- 生成条目：%d" % len(entries),
             "- 带岗位：%d" % full,
             "- 全未知：%d" % sum(1 for e in entries if e["jobSource"] == UNKNOWN),
             "", "## 归一与降级明细", ""]
    lines += ["- " + item for item in report] or ["- 无"]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("已生成 %s：%d 条，其中 %d 条带岗位。" % (args.out, len(entries), full))
    print("明细见 %s（%d 条记录）" % (args.report, len(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
