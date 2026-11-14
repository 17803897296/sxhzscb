"""Offline companies.json builder. Python standard library only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

UNKNOWN = "未知"
COMPANY_KEYS = (
    "id", "name", "nature", "city", "industry", "jobs", "jobSource",
    "jobEvidence", "source", "date",
)
JOB_KEYS = ("title", "requirements", "headcount", "urgency", "group", "salary")
NATURE = {"央企", "国企", "民企", "外企", "合资", "事业单位", UNKNOWN}
INDUSTRY = {"制造", "医药", "IT", "金融", "建筑", "能源", "消费", "物流", "教育", "政府事业", "其他", UNKNOWN}
URGENCY = {"高", "中", "低", UNKNOWN}
# v17：联网搜索得到的岗位记录标 websearch；「主营业务推测」只作为历史遗留值保留，
# 新数据一律不再产生（只搜到业务介绍 → 未知 + jobs 空）。
# v18：无具体岗企业再分两档，均由匹配端大模型按学生意向岗位判断：
#   未知1 = 有粗略业务描述但无岗位公告；未知2 = 联网检索仍无岗位。
JOB_SOURCE = {"校招公告", "社招信息", "websearch", "主营业务推测",
              "未知1", "未知2", UNKNOWN}
CACHE_KEYS = set(COMPANY_KEYS) - {"id"} | {"text"}


class ContractError(ValueError):
    pass


def data_path(value):
    """All runtime input/output paths must be relative and inside data/."""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "data":
        raise ContractError("路径必须位于相对目录 data/ 下，且不能包含 ..")
    root = Path("data").resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise ContractError("路径不能通过链接指向 data/ 之外")
    return path


def read_json(path):
    def unique_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ContractError("JSON 出现重复 key：" + key)
            obj[key] = value
        return obj

    with data_path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle, object_pairs_hook=unique_keys)


def text_value(value, label):
    if value is None:
        return UNKNOWN
    if not isinstance(value, str):
        raise ContractError(label + " 必须是字符串")
    return value.strip() or UNKNOWN


def enum_value(value, allowed, label):
    result = text_value(value, label)
    if result not in allowed:
        raise ContractError(label + " 枚举不合法：" + result)
    return result


def keys_exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ContractError(label + " 字段必须严格为：" + " ".join(keys))


def reject_missing(value, label="output"):
    if value is None or isinstance(value, str) and not value.strip():
        raise ContractError(label + " 不允许 null 或空字符串")
    if isinstance(value, dict):
        for key, item in value.items():
            reject_missing(item, label + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_missing(item, label + "[" + str(index) + "]")


def valid_date(value):
    if value == UNKNOWN:
        return
    from datetime import date
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("date 必须为 YYYY-MM-DD 或 未知") from exc


def validate_companies(companies):
    if not isinstance(companies, list):
        raise ContractError("companies.json 顶层必须是数组")
    reject_missing(companies)
    seen = set()
    for company in companies:
        keys_exact(company, COMPANY_KEYS, "企业对象")
        for key in COMPANY_KEYS:
            if key != "jobs" and not isinstance(company[key], str):
                raise ContractError(key + " 必须是字符串")
        if company["id"] == UNKNOWN or company["name"] == UNKNOWN:
            raise ContractError("企业 id、name 不能为 未知")
        if company["id"] in seen:
            raise ContractError("企业 id 重复：" + company["id"])
        seen.add(company["id"])
        for key, allowed in [("nature", NATURE), ("industry", INDUSTRY), ("jobSource", JOB_SOURCE)]:
            if company[key] not in allowed:
                raise ContractError(key + " 枚举不合法：" + company[key])
        valid_date(company["date"])
        jobs = company["jobs"]
        if not isinstance(jobs, list):
            raise ContractError("jobs 必须是数组")
        if company["jobSource"] == UNKNOWN and jobs:
            raise ContractError("jobSource=未知 时 jobs 必须为空数组")
        if company["jobSource"] == "主营业务推测" and company["jobEvidence"] == UNKNOWN:
            raise ContractError("推测岗位必须提供 jobEvidence")
        for job in jobs:
            keys_exact(job, JOB_KEYS, "岗位对象")
            for key in JOB_KEYS:
                if key != "headcount" and not isinstance(job[key], str):
                    raise ContractError("岗位 " + key + " 必须是字符串")
            if job["title"] == UNKNOWN:
                raise ContractError("无法确认或推测岗位名时，不创建岗位项")
            count = job["headcount"]
            if count != UNKNOWN and (type(count) is not int or count < 0):
                raise ContractError("headcount 必须是非负整数或 未知")
            if job["urgency"] not in URGENCY:
                raise ContractError("urgency 枚举不合法：" + job["urgency"])
    return companies


def normalize_job(raw):
    if not isinstance(raw, dict) or set(raw) - set(JOB_KEYS):
        raise ContractError("缓存岗位存在未约定字段")
    job = {}
    for key in JOB_KEYS:
        value = raw.get(key)
        if key == "headcount":
            if value is None or isinstance(value, str) and not value.strip():
                value = UNKNOWN
            if value != UNKNOWN and (type(value) is not int or value < 0):
                raise ContractError("headcount 必须是非负整数或 未知")
            job[key] = value
        elif key == "urgency":
            job[key] = enum_value(value, URGENCY, key)
        else:
            job[key] = text_value(value, key)
    return job


def build_companies(names, cache):
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        raise ContractError("企业名单必须是非空企业名组成的数组；无企业时可用 []")
    if not isinstance(cache, list):
        raise ContractError("research_cache.json 必须是数组")
    index = {}
    for item in cache:
        if not isinstance(item, dict) or set(item) - CACHE_KEYS:
            raise ContractError("缓存企业存在未约定字段，仅允许企业字段（不含 id）及 text")
        name = text_value(item.get("name"), "缓存 name")
        if name == UNKNOWN or name in index:
            raise ContractError("缓存 name 必须明确且唯一：" + name)
        index[name] = item
    result = []
    seen_names = set()
    for value in names:
        name = value.strip()
        if name in seen_names:
            continue
        seen_names.add(name)
        raw = index.get(name, {})
        company = {
            "id": "C" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
            "name": name,
            "nature": enum_value(raw.get("nature"), NATURE, "nature"),
            "city": text_value(raw.get("city"), "city"),
            "industry": enum_value(raw.get("industry"), INDUSTRY, "industry"),
            "jobs": [],
            "jobSource": enum_value(raw.get("jobSource"), JOB_SOURCE, "jobSource"),
            "jobEvidence": text_value(raw.get("jobEvidence"), "jobEvidence"),
            "source": text_value(raw.get("source"), "source"),
            "date": text_value(raw.get("date"), "date"),
        }
        raw_jobs = raw.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise ContractError("缓存 jobs 必须是数组；无岗位时写 []")
        company["jobs"] = [normalize_job(j) for j in raw_jobs]
        if company["jobSource"] == "主营业务推测":
            original = text_value(raw.get("text"), "缓存原文 text")
            evidence = company["jobEvidence"]
            if evidence == UNKNOWN or original == UNKNOWN or evidence not in original:
                raise ContractError(name + "：jobEvidence 必须是缓存 text 中能逐字对应的原文片段")
            if company["source"] == UNKNOWN:
                raise ContractError(name + "：推测岗位还需注明公开材料 source")
        result.append(company)
    return validate_companies(result)


def write_json(path, payload):
    target = data_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(target.parent),
                                         suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(serialized)
        os.replace(temp_name, target)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线生成企业契约 JSON，不发起网络请求")
    parser.add_argument("--names", default="data/company_names.json")
    parser.add_argument("--cache", default="data/research_cache.json")
    parser.add_argument("--output", default="data/companies.json")
    parser.add_argument("--validate", metavar="DATA_FILE", help="只校验 data/ 下的企业 JSON")
    args = parser.parse_args(argv)
    try:
        if args.validate:
            companies = validate_companies(read_json(args.validate))
            print("校验通过：%d 家企业" % len(companies))
            return 0
        output = data_path(args.output)
        if output.resolve() in {data_path(args.names).resolve(), data_path(args.cache).resolve()}:
            raise ContractError("输出文件不能覆盖企业名单或资料缓存")
        companies = build_companies(read_json(args.names), read_json(args.cache))
        write_json(output, companies)
        missing = sum(c["jobSource"] == UNKNOWN for c in companies)
        print("已生成 %s：%d 家企业，其中 %d 家岗位来源未知。" % (output.as_posix(), len(companies), missing))
        print("自检：字段名一致；无 null/空串；枚举合法；未过滤软偏好；无联网和固定绝对路径。")
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print("处理失败：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
