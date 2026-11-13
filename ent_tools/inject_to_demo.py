"""把企业端产出的 companies.json 灌进 demo/index.html 的 COMPANIES 常量。

只改两处：
1. `const COMPANIES = [...]` 整段替换
2. RESULTS 里的 id 按顺序改成新 id（顺序不变，避免打乱演示分档）

不改任何字段名，不改页面代码。
"""

import json
import re
import sys
from pathlib import Path

HTML = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../demo/index.html")
DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/companies.json")

html = HTML.read_text(encoding="utf-8")
companies = json.loads(DATA.read_text(encoding="utf-8-sig"))

# 1) 替换 COMPANIES
start = html.index("const COMPANIES = [")
end = html.index("\n];", start) + len("\n];")
# 保留键顺序，按契约里的顺序输出，方便人读
ORDER = ["id", "name", "nature", "city", "industry", "jobs",
         "jobSource", "jobEvidence", "source", "date"]
JOB_ORDER = ["title", "requirements", "headcount", "urgency", "group", "salary"]

normalized = []
for c in companies:
    item = {k: c[k] for k in ORDER}
    item["jobs"] = [{k: j[k] for k in JOB_ORDER} for j in c["jobs"]]
    normalized.append(item)

block = "const COMPANIES = " + json.dumps(normalized, ensure_ascii=False, indent=2) + ";"
html = html[:start] + block + html[end:]

# 2) 重写 RESULTS 的 id
old_ids = re.findall(r'\{ id:"(E\d\d)"', html)
new_ids = [c["id"] for c in companies]
mapping = dict(zip(old_ids, new_ids))
for old, new in mapping.items():
    html = html.replace('{ id:"%s"' % old, '{ id:"%s"' % new)

HTML.write_text(html, encoding="utf-8")
print("COMPANIES 已替换：%d 家" % len(normalized))
print("RESULTS id 已重映射：%d 条" % len(mapping))
missing = [c["name"] for c in normalized if not c["jobs"]]
if missing:
    print("无岗位（页面会隐藏岗位区）：" + "、".join(missing))
