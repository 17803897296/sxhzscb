"""把在线 lookup 缓存里已经抓到的岗位合并进企业端离线 companies.json。

用途：`lookup_cache.json` 是网页搜索的实时结果；`companies.json` 是离线交付契约。
这个脚本只补“已经有岗位”的企业，不覆盖 companies.json 里已有的岗位。
"""
import json
import pathlib
import sys

DATA = pathlib.Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA / "lookup_cache.json"
COMPANIES_FILE = DATA / "companies.json"


def main():
    if not CACHE_FILE.exists():
        print("没有 lookup_cache.json，跳过在线岗位合并")
        return 0
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8-sig"))
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8-sig"))

    updated = 0
    for item in companies:
        hit = cache.get(item.get("name"))
        if not hit or not hit.get("jobs"):
            continue
        if item.get("jobs"):
            continue
        item["jobs"] = hit["jobs"]
        for key in ("jobSource", "jobEvidence"):
            if hit.get(key) not in (None, "", "未知"):
                item[key] = hit[key]
        for key in ("source", "date"):
            if item.get(key) in (None, "", "未知") and hit.get(key) not in (None, "", "未知"):
                item[key] = hit[key]
        for key in ("city", "nature", "industry"):
            if item.get(key) in (None, "", "未知") and hit.get(key) not in (None, "", "未知"):
                item[key] = hit[key]
        updated += 1

    COMPANIES_FILE.write_text(
        json.dumps(companies, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    jobs = sum(1 for x in companies if x.get("jobs"))
    print("合并 %d 家；companies.json 现在 %d 家，其中 %d 家有岗位"
          % (updated, len(companies), jobs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
