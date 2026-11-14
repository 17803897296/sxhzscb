# -*- coding: utf-8 -*-
"""把新一场双选会的展位表导入当前企业库。

背景：booths.json + company_jobs 之前是按 118 家旧名单建的。换场次后名单
完全不同（145 家，仅 22 家与旧库重名），必须：
  1. 解析 xlsx 得到 booth / name / headcount；
  2. 旧 company_jobs 全量归档到 company_jobs_prev（同库表，不丢数据）；
  3. company_jobs 只保留本场名单，缺的企业插骨架（jobs=[] 待采集）；
  4. booths.json 覆盖为本场名单（旧文件存 booths.prev.json）。

用法：
    python -X utf8 import_roster.py --xlsx "D:/xx.xlsx"          # 预览
    python -X utf8 import_roster.py --xlsx "D:/xx.xlsx" --apply  # 真写
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
BOOTHS = os.path.join(DATA, "booths.json")


def parse_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        header_row = None
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if any("单位名称" in c for c in cells):
                header_row = i
                # 定列：单位名称 / 新展位号 或 展位号 / 招聘人数
                idx = {}
                for j, c in enumerate(cells):
                    if "单位名称" in c or c in ("企业名称", "公司名称"):
                        idx["name"] = j
                    elif "展位" in c:
                        idx["booth"] = j
                    elif "人数" in c:
                        idx["headcount"] = j
                break
        if header_row is None:
            continue
        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            name = cells[idx["name"]] if idx.get("name") is not None and idx["name"] < len(cells) else ""
            if not name or len(name) < 3:
                continue
            booth = cells[idx["booth"]] if idx.get("booth") is not None and idx["booth"] < len(cells) else ""
            hc = cells[idx["headcount"]] if idx.get("headcount") is not None and idx["headcount"] < len(cells) else ""
            try:
                headcount = int(float(hc)) if hc else 0
            except ValueError:
                headcount = 0
            out.append({"booth": booth or "", "name": name, "headcount": headcount})
    seen, uniq = set(), []
    for r in out:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        uniq.append(r)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    roster = parse_xlsx(args.xlsx)
    print("名单家数：%d" % len(roster))
    if not roster:
        return 1
    names = [r["name"] for r in roster]

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    old = {r["name"]: json.loads(r["item_json"])
           for r in cur.execute("SELECT name,item_json FROM company_jobs")}
    keep = [n for n in names if n in old]
    add = [n for n in names if n not in old]
    drop = [n for n in old if n not in set(names)]
    with_jobs = sum(1 for n in keep if old[n].get("jobs"))
    print("重叠 %d 家（其中 %d 家有岗位）  新增 %d 家  移出 %d 家"
          % (len(keep), with_jobs, len(add), len(drop)))

    if not args.apply:
        print("预览模式，未写库。加 --apply 执行。")
        return 0

    # 1. 旧库归档
    cur.execute("""CREATE TABLE IF NOT EXISTS company_jobs_prev (
        name TEXT PRIMARY KEY, item_json TEXT NOT NULL,
        archived_at TEXT NOT NULL, batch TEXT NOT NULL)""")
    batch = time.strftime("%Y%m%d_%H%M%S")
    for n, d in old.items():
        cur.execute("INSERT OR REPLACE INTO company_jobs_prev(name,item_json,archived_at,batch) "
                    "VALUES (?,?,?,?)", (n, json.dumps(d, ensure_ascii=False),
                                         time.strftime("%Y-%m-%d %H:%M:%S"), batch))
    # 2. 移出不在本场名单的企业
    for n in drop:
        cur.execute("DELETE FROM company_jobs WHERE name=?", (n,))
    # 3. 新企业插骨架
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for n in add:
        skeleton = {
            "id": "web-" + str(abs(hash(n)) % 10 ** 8), "name": n,
            "nature": "未知", "city": "未知", "industry": "未知",
            "jobs": [], "jobSource": "未知", "jobEvidence": "未知",
            "source": "未知", "date": "未知", "note": "本场新名单，待采集",
            "roster_only": True,
        }
        cur.execute("INSERT OR REPLACE INTO company_jobs(name,item_json,updated_at) VALUES (?,?,?)",
                    (n, json.dumps(skeleton, ensure_ascii=False), now))
    # 4. 重叠企业：丢掉上一场的场次残留标记，保留岗位
    for n in keep:
        d = old[n]
        if d.get("roster_only"):
            d.pop("roster_only", None)
        cur.execute("UPDATE company_jobs SET item_json=? WHERE name=?",
                    (json.dumps(d, ensure_ascii=False), n))

    # 5. booths.json 覆盖（旧文件留一份）
    if os.path.isfile(BOOTHS):
        prev = os.path.join(DATA, "booths.prev.json")
        if not os.path.isfile(prev):
            with open(prev, "w", encoding="utf-8") as f:
                f.write(open(BOOTHS, encoding="utf-8").read())
    with open(BOOTHS, "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=1)

    con.commit()
    cur.execute("SELECT count(*) FROM company_jobs")
    total = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM company_jobs_prev")
    prev_total = cur.fetchone()[0]
    con.close()

    todo = [r["name"] for r in roster
            if not old.get(r["name"], {}).get("jobs")]
    print("写入完成：company_jobs %d 家 / 归档 %d 家 / 待采集 %d 家"
          % (total, prev_total, len(todo)))
    with open(os.path.join(DATA, "_roster_todo.json"), "w", encoding="utf-8") as f:
        json.dump(todo, f, ensure_ascii=False, indent=1)
    print("待采集清单 -> data/_roster_todo.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
