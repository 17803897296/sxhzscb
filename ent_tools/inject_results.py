"""把 rank.py 算出的 results 灌进 demo/index.html 的 RESULTS 常量。"""

import json
import sys
from pathlib import Path

HTML = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../demo/index.html")
DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/results.json")

html = HTML.read_text(encoding="utf-8")
rows = json.loads(DATA.read_text(encoding="utf-8-sig"))

ORDER = ["id", "tier", "score", "distanceKm", "nearestCity", "reasons", "gaps"]
out = []
for r in rows:
    item = {k: r.get(k) for k in ORDER}
    if item["distanceKm"] is None:
        item["distanceKm"] = "未知"
    out.append(item)

start = html.index("const RESULTS = [")
end = html.index("\n];", start) + len("\n];")
block = "const RESULTS = " + json.dumps(out, ensure_ascii=False, indent=2) + ";"
html = html[:start] + block + html[end:]
HTML.write_text(html, encoding="utf-8")

from collections import Counter
print("RESULTS 已替换：%d 条" % len(out))
print("分档:", dict(Counter(r["tier"] for r in out)))
