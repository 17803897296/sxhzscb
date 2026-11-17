"""把企业库里的「通用岗位」补上可能方向，落到数据库 + JSON。

为什么要在数据层做
------------------
采集端抓来的岗位名，很大一部分不带方向：「工艺工程师」「质量工程师」「检验员」。
学生学化工，看到「工艺工程师」不知道是不是自己的；系统比对时也不知道该不该算命中。

两条做法：

  · **现场靠大模型判**：把学生意向塞给模型问「这家招的工艺工程师是不是化工方向」。
    模型有强动机回答「是」——它在迎合提问。这正是旧链路编出「电解铝设备工程师」
    的原因。
  · **提前按主营补方向**：主营是「农药中间体、精细化工」，那这家所有工艺/质量/
    检验岗就是化工方向；主营是「汽车内饰件注塑」，就是机械方向。这件事只看企业
    自己的资料，跟学生要找什么工作无关，所以**不会被迎合污染**，而且一次算好、
    145 家随时可查。

本脚本做第二条。判决权仍在下游：这里只写「这个岗在这家单位大概率是什么方向」，
并留下依据词（「石油化工」「汽车零部件」），任何一条都能人工复核。

用法：
    python -X utf8 _dir_fill.py            # 写入库与 JSON
    python -X utf8 _dir_fill.py --dry      # 只打印统计
"""

import json
import os
import sqlite3
import sys

import job_direction as D

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "careersafari.db")
# 只读种子：干净 clone 首次启动会用它复制出活库。只写活库的话，补出来的岗位
# 方向在下一次 clone 时全部消失（与 _biz_fill.py 同样的理由）。
DBS = [DB, os.path.join(HERE, "data", "careersafari.db.seed")]
OUT = os.path.join(HERE, "data", "company_job_dir.json")


def _rows():
    con = sqlite3.connect(DB)
    out = []
    for name, it in con.execute("SELECT name, item_json FROM company_jobs"):
        try:
            o = json.loads(it)
        except Exception:
            continue
        out.append((name, o))
    con.close()
    return out


def build():
    """返回 {企业名: {岗位名: {dir, cn, kw, src}}}，只收推定出方向的那些。"""
    out, stat = {}, {"jobs": 0, "kept": 0, "no_dir": 0, "has_dir_in_name": 0}
    for name, o in _rows():
        biz = D.biz_text(o.get("industry"), o.get("segment"), o.get("biz_scope"))
        book = {}
        for j in o.get("jobs") or []:
            t = str(j.get("title") or "").strip()
            if not t:
                continue
            stat["jobs"] += 1
            r = D.resolve(t, biz)
            if r["src"] == "title":
                stat["has_dir_in_name"] += 1
                continue
            if not r["dirs"]:
                stat["no_dir"] += 1
                continue
            book[t] = {"dir": r["dirs"][0][0], "cn": r["cn"],
                       "kw": r["dirs"][0][1], "src": "biz",
                       # 其余可能方向也留着：多元集团两边都招人
                       "alts": [c for c, _k in r["dirs"][1:]]}
            stat["kept"] += 1
        if book:
            out[name] = book
    return out, stat


def write_db(book, db=None):
    con = sqlite3.connect(db or DB)
    con.execute("""CREATE TABLE IF NOT EXISTS job_dir_hint(
        name TEXT NOT NULL, title TEXT NOT NULL,
        dir TEXT, dir_cn TEXT, kw TEXT, alts TEXT,
        PRIMARY KEY(name, title))""")
    con.execute("DELETE FROM job_dir_hint")
    n = 0
    for name, jobs in book.items():
        for t, v in jobs.items():
            con.execute("INSERT OR REPLACE INTO job_dir_hint"
                        "(name,title,dir,dir_cn,kw,alts) VALUES(?,?,?,?,?,?)",
                        (name, t, v["dir"], v["cn"], v["kw"],
                         "、".join(v.get("alts") or [])))
            n += 1
    con.commit()
    con.close()
    return n


def main():
    book, stat = build()
    print("企业岗位 %d 条：岗名自带方向 %d 条、并按主营补出方向 %d 条、推不出 %d 条"
          % (stat["jobs"], stat["has_dir_in_name"], stat["kept"], stat["no_dir"]))
    print("覆盖企业 %d 家（共 %d 家）" % (len(book), len(_rows())))

    # 抽样：看补出来的方向是否可信
    print("\n抽样（企业 | 岗位 → 方向，依据）：")
    shown = 0
    for name, jobs in book.items():
        for t, v in list(jobs.items())[:2]:
            print("  %-16s| %-14s → %-4s 依据「%s」"
                  % (name[:16], t[:14], v["cn"], v["kw"]))
            shown += 1
            if shown >= 12:
                break
        if shown >= 12:
            break

    if "--dry" in sys.argv:
        print("\n（--dry：未写入）")
        return
    n = 0
    for _db in DBS:
        if not os.path.isfile(_db):
            print("跳过（不存在）：%s" % os.path.basename(_db))
            continue
        _n = write_db(book, _db)
        n = max(n, _n)
        print("写入 job_dir_hint %d 条 → %s" % (_n, os.path.basename(_db)))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                   "note": "岗位方向按主营推定，dir_src=biz，仅供比对参考",
                   "companies": book}, f, ensure_ascii=False, indent=1)
    print("已写 JSON → %s（%d 条）" % (os.path.basename(OUT), n))


if __name__ == "__main__":
    main()
