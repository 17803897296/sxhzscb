# -*- coding: utf-8 -*-
"""合并一批 biz_scope 补采结果到 todo 跟踪文件与 careersafari.db。

用法：在 ent_tools/ 下运行  python -X utf8 merge_biz.py
读取 data/biz_incoming.json => {企业名: 主营范围/主要产品 字符串}
- 更新 data/biz_scope_todo.json 的 biz_scope / done
- 把 biz_scope 写进 company_jobs.item_json（新增字段，不破坏其他字段）
"""
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
TODO = os.path.join(DATA, "biz_scope_todo.json")
INCOMING = os.path.join(DATA, "biz_incoming.json")
DB = os.path.join(DATA, "careersafari.db")


def main():
    todo = json.load(open(TODO, encoding="utf-8"))
    inc = json.load(open(INCOMING, encoding="utf-8")) if os.path.isfile(INCOMING) else {}
    if not inc:
        print("biz_incoming.json 为空或不存在，跳过。")
        return

    by_name = {t["name"]: t for t in todo}
    updated = 0
    missing = []
    for name, scope in inc.items():
        if name in by_name:
            by_name[name]["biz_scope"] = scope
            by_name[name]["done"] = bool(str(scope).strip())
            updated += 1
        else:
            missing.append(name)
    json.dump(todo, open(TODO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 写库
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_updated = 0
    for r in con.execute("SELECT name,item_json FROM company_jobs"):
        name = r["name"]
        if name in inc:
            try:
                item = json.loads(r["item_json"])
            except Exception:
                item = {}
            item["biz_scope"] = inc[name]
            con.execute("UPDATE company_jobs SET item_json=? WHERE name=?",
                        (json.dumps(item, ensure_ascii=False), name))
            db_updated += 1
    con.commit()
    con.close()

    done_total = sum(1 for t in todo if t["done"])
    print("todo 更新条目: %d / %d" % (updated, len(todo)))
    print("DB 写回条目: %d" % db_updated)
    print("已完成(done=true)总数: %d / %d" % (done_total, len(todo)))
    if missing:
        print("未在 todo 中找到(跳过): %s" % ", ".join(missing))


if __name__ == "__main__":
    main()
