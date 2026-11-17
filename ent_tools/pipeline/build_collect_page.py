"""把谭美娟的企业名单采集页打包成离线单文件。

输入：_new_review/new/index.html + ent_tools/data/company_names.json
输出：demo/collect.html

做法：注入 fetch 垫片，把 POST /api/company-csv 换成本地规则抽取：
- 用户在输入框里打了字 → 按 、，\\n 切分并过滤
- 没打字（或只传了文件）→ 用内置的真实名单（南大鼓楼场 118 家）兜底
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "_new_review" / "new" / "index.html"
NAMES = ROOT / "ent_tools" / "data" / "company_names.json"
OUT = ROOT / "demo" / "collect.html"

html = SRC.read_text(encoding="utf-8")
names = json.loads(NAMES.read_text(encoding="utf-8-sig"))

SHIM = r"""
<script>
/* ── 离线垫片：把 /api/company-csv 换成本地规则，不需要后端和模型 ───────── */
const DEMO_NAMES = __NAMES__;

function _csvBytes(list) {
  const rows = ["company_name"].concat(list);
  return new TextEncoder().encode(rows.join("\r\n") + "\r\n");
}

function _extractFromText(text) {
  return String(text || "")
    .split(/[、，,;\n\r\t]+/)
    .map(s => s.trim())
    .filter(s => s && s.length >= 2 && s.length <= 60);
}

const _realFetch = window.fetch;
window.fetch = function (url, opts) {
  if (String(url).indexOf("/api/company-csv") < 0) {
    return _realFetch.apply(this, arguments);
  }
  const body = (opts && opts.body) || new FormData();
  let typed = "";
  try { typed = body.get("text") || ""; } catch (e) { typed = ""; }

  let list = _extractFromText(typed);
  if (!list.length) list = DEMO_NAMES.slice();

  const seen = new Set();
  list = list.filter(n => (seen.has(n) ? false : (seen.add(n), true)));

  const bytes = _csvBytes(list);
  try { localStorage.setItem("company_names", JSON.stringify(list)); } catch (e) {}
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: k => (String(k).toLowerCase() === "x-company-count" ? String(list.length) : null) },
    blob: async () => new Blob([bytes], { type: "text/csv;charset=utf-8" }),
    json: async () => ({ count: list.length }),
    text: async () => new TextDecoder().decode(bytes)
  });
};
</script>
"""

SHIM = SHIM.replace("__NAMES__", json.dumps(names, ensure_ascii=False))
html = html.replace("</body>", SHIM + "\n</body>", 1)
OUT.write_text(html, encoding="utf-8")

print("生成", OUT)
print("  大小 %.1f KB，内置名单 %d 家" % (OUT.stat().st_size / 1024, len(names)))
