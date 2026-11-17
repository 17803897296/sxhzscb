"""把交接包的对话式采集页打包成离线单页，并把「名单 + 匹配」接在对话后面。

输入：_handoff/shuangxuan/app/static/{fair.html,fair.js,fair.css,style.css}
      demo/index.html（取 COMPANIES 真实企业数据与排序规则）
输出：demo/fair.html

页面流程（全在一页内）：
  画像对话 → 生成画像 → 导入企业名单 → 点「开始匹配」 → 结果列表
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from fair_report_js import REPORT_JS

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
JS_DIR = HERE / "js"


def _js(name):
    """读 ent_tools/js/ 下的前端源码。

    用 bytes 读再用 utf-8 解，避免 Windows 上 read_text 把换行翻译成 \r\n ——
    这几段 JS 是逐字内联进页面的，翻译了字节就变，页面 diff 会全是噪声。
    """
    return (JS_DIR / name).read_bytes().decode("utf-8")
HANDOFF = ROOT / "_handoff" / "shuangxuan" / "app" / "static"
DEMO = ROOT / "demo"

# v10：把打分用的城市/企业类型/规模规则从 rules.py 生成成 JS 常量，注入页面端，
# 保证服务端（fair_bridge.score_parts）与页面端（fair_report_js.calcOne）用同一套数据。
sys.path.insert(0, str(HERE / "demo_v2"))
import rules as R

def _rules_js():
    payload = {
        "city_cats": R.CITY_CATS,
        "city_first": R.CITY_FIRST,
        "city_new1": R.CITY_NEW1,
        "city_cap": R.CITY_CAPITAL,
        "city_econ": R.CITY_ECON,
        "city_transit": R.CITY_TRANSIT,
        "city_eco": R.CITY_ECO,
        "PM": R.CITY_PROVINCE,
        "adj": R.ADJ_PROVINCES,
        "ct_buckets": R.COMPANY_TYPE_BUCKETS,
        "scale_tiers": R.SCALE_TIERS,
        "industry_status_tiers": R.INDUSTRY_STATUS_TIERS,
        "industry_status_floor": R.INDUSTRY_STATUS_FLOOR,
        "growth_tiers": R.GROWTH_TIERS,
        "growth_floor": R.GROWTH_FLOOR,
        "city_rank_def": R.CITY_RANK_DEFAULT,
        "ct_rank_def": R.COMPANY_TYPE_RANK_DEFAULT,
        "ind_rank_def": R.INDUSTRY_RANK_DEFAULT,
    }
    return "const RULES = " + json.dumps(payload, ensure_ascii=False) + ";"



html = (HANDOFF / "fair.html").read_text(encoding="utf-8")
style = (HANDOFF / "style.css").read_text(encoding="utf-8")
fair_css = (HANDOFF / "fair.css").read_text(encoding="utf-8")
# 2026-11-17：采集端 JS 已固化成 ent_tools/js/fair.js（真实源文件），构建时不再对
# 交接包做 20 处字符串替换。改采集流程直接改那个文件；要从交接包重新生成，
# 跑 _freeze_fair_js.py（会打印逐行差异，确认后 --write）。
fair_js = _js("fair.js")
PATCH_LOG = ["采集端 JS 读 js/fair.js（已固化，构建时零替换）"]
questionnaire = json.loads((DEMO / "questionnaire.json").read_text(encoding="utf-8"))

# ⑤ 企业类型选项对齐匹配端的 nature 枚举
questionnaire["fair"]["company_types"] = ["央企", "国企", "民企", "外企", "合资", "事业单位"]
PATCH_LOG.append("企业类型改为 nature 枚举")

# ⑥ v6：偏好卡换成真正参与排序的五项 100（去掉六维价值观与双休）。
#    键名与 rules.DIMENSIONS / fair_bridge.weights_from 完全一致，
#    学生填的分就是匹配端权重，不再有两套语义错配的偏好。
questionnaire["value_dimensions"] = [
    ["city", "城市条件（一线 / 新一线 / 省会 / 其他）"],
    ["home", "离家近"],
    ["company_type", "企业类型（央企 / 国企 / 民企 / 外企）"],
    ["industry", "行业"],
    ["scale", "企业规模档"],
    ["industry_status", "行业地位（500强 / 行业龙头）"],
    ["growth", "成长性（高速成长企业）"],
]
questionnaire["value_dimensions_note"] = (
    "七项合计 100 分，就是排序权重；会前拿不到的薪酬 / 双休不在这里打分，"
    "会转成报告里的现场提问。")
PATCH_LOG.append("偏好卡改为五项 100（与匹配端同键）")

def _companies_from_db(limit_req=300):
    """采集端「开始匹配」用的内置企业数组（index.html 里那份是空的）。

    直接从活库 company_jobs.item_json 生成，字段对齐 match() 需要的：
    id / name / city / province / nature / industry / jobs[] / jobSource。
    岗位描述只截前 300 字（仅供关键词打分，避免页面体积暴涨）。
    """
    import sqlite3
    db = ROOT / "ent_tools" / "data" / "careersafari.db"
    if not db.exists():
        return "[]"
    out = []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("SELECT name, item_json FROM company_jobs ORDER BY name"):
            try:
                it = json.loads(row["item_json"] or "{}")
            except Exception:
                continue
            jobs = []
            for j in (it.get("jobs") or []):
                title = (j.get("title") or "").strip()
                if not title:
                    continue
                jobs.append({
                    "title": title,
                    "requirements": (j.get("requirements") or "")[:limit_req],
                    "salary": j.get("salary") or "未知",
                    "urgency": j.get("urgency") or "未知",
                    "headcount": j.get("headcount") or "未知",
                })
            out.append({
                "id": it.get("id") or row["name"],
                "name": it.get("name") or row["name"],
                "city": it.get("city") or "未知",
                "province": it.get("province") or "",
                "nature": it.get("nature") or "",
                "industry": it.get("industry") or "",
                "jobs": jobs,
                "jobSource": it.get("jobSource") or "",
            })
    finally:
        con.close()
    return json.dumps(out, ensure_ascii=False)


# 从 index.html 里取真实企业数据；为空则直接用活库补齐（否则「开始匹配」是 0 家）
index_html = (DEMO / "index.html").read_text(encoding="utf-8")
m = re.search(r"const COMPANIES = (\[.*?\n\]);", index_html, re.S)
COMPANIES_JSON = m.group(1) if m else "[]"
if COMPANIES_JSON.count('"id"') == 0:
    COMPANIES_JSON = _companies_from_db()
    PATCH_LOG.append("采集端内置企业数组改为从活库 company_jobs 生成（原先为空，导致「开始匹配」0 家）")

names_file = ROOT / "ent_tools" / "data" / "company_names.json"
NAMES = json.loads(names_file.read_text(encoding="utf-8-sig")) if names_file.exists() else []
# 名单与库对齐，避免「内置名单 118 家」但报告 122 家对不上
try:
    _db_names = [c["name"] for c in json.loads(COMPANIES_JSON)] if COMPANIES_JSON != "[]" else []
    if _db_names and len(_db_names) != len(NAMES):
        NAMES = _db_names
        PATCH_LOG.append("内置名单与活库企业数对齐（%d 家）" % len(NAMES))
except Exception:
    pass

SHIM = _js("shim.js")
SHIM = SHIM.replace("__QUESTIONNAIRE__", json.dumps(questionnaire, ensure_ascii=False))

# 采集端「岗位推荐」：按专业 + 实习给几个具体岗位名，学生勾选后写进 intended_jobs[]。
# 输出不是岗位族、不是企业事实；联网时由 /api/fair/recommend-jobs 调模型，离线时规则兜底。
RECOMMEND_JS = _js("recommend.js")

MATCH = _js("match.js")

MATCH = MATCH.replace("__COMPANIES__", COMPANIES_JSON) \
             .replace("__NAMES__", json.dumps(NAMES, ensure_ascii=False))

# ── 组装 ───────────────────────────────────────────────
html = html.replace('<link rel="stylesheet" href="/static/style.css">', "<style>\n" + style + "\n</style>")
html = html.replace('<link rel="stylesheet" href="/static/fair.css">', "<style>\n" + fair_css + "\n</style>")
html = re.sub(r'<script src="/static/fair\.js"[^>]*></script>', "", html)
html = html.replace('<a class="toplink" href="/">完整版 →</a>', "")
html = html.replace(
    '<p class="muted small">点下面的选项，或者直接打字。只问 4 个问题，一分钟左右，最后给你一份可以下载的 JSON。</p>',
    '<p class="muted small">面向研三 / 大四：按校招画像生成。点选项或直接打字，一分钟左右，最后给你一份可以下载的 JSON。</p>')
html = html.replace(
    '<span>CareerSafari · 双选会采集端 · 只采集与导出画像，不做岗位推荐</span>',
    '<span>CareerSafari · 双选会采集端 · 采集画像 + 按专业/实习推荐候选岗位（由学生勾选）</span>')

# 三端协调报告：把采集端画像 + 企业端 118 家 + 匹配端模型结果内联进页面。
# 页面形式不变，只把 #mres 的内容换成完善报告（见 fair_report_js.py）。
TRI_PATH = Path(__file__).resolve().parent / "data" / "fair_payload.json"
TRI_JSON = TRI_PATH.read_text(encoding="utf-8") if TRI_PATH.exists() else "{}"
REPORT_BUNDLE = REPORT_JS.replace("__TRI_DATA__", TRI_JSON).replace("__RULES_JS__", _rules_js())

bundle = (SHIM + RECOMMEND_JS + "<script>\n" + fair_js + "\n</script>\n"
          + MATCH + REPORT_BUNDLE + "</body>")
html = html.replace("</body>", bundle, 1)

out = DEMO / "fair.html"
out.write_text(html, encoding="utf-8")

# v12 匹配端独立页（2026-11-17 三端分工）：同一份页面、同一套内联 JS，
# 启动即进报告视图（window.__FORCE_REPORT 让 init() 走 ?report=1 分支），
# 不再依赖查询参数——portal 的「匹配端」标签直接指向这份文件。
match_html = html.replace("<head>",
                          "<head>\n<script>window.__FORCE_REPORT=1;</script>", 1)
if "<title>" in match_html:
    match_html = re.sub(r"<title>.*?</title>",
                        "<title>匹配端 · 双选会匹配排行</title>",
                        match_html, count=1, flags=re.S)
match_out = DEMO / "match.html"
match_out.write_text(match_html, encoding="utf-8")

# 构建戳：health.py 靠它判断「demo/fair.html 有没有被人直接手改」。
# fair.html 是生成物，手改它下次构建就全丢，改的人却以为自己改好了。
# 只在正常构建时刷新；直接编辑 html 不会动这个文件，于是下次体检立刻露馅。
_stamp_p = HERE / "data" / ".page_stamp.json"
try:
    _stamp = {}
    if _stamp_p.exists():
        _stamp = json.loads(_stamp_p.read_text(encoding="utf-8"))
    _stamp.setdefault("_note", "build_fair_page.py 自动维护。手改 demo/fair.html 会让 md5 对不上。")
    _stamp[str(out.resolve())] = {
        "md5": hashlib.md5(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _stamp_p.write_text(json.dumps(_stamp, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
except Exception as _e:      # 构建戳写不进去不该阻断页面生成
    print("  [warn] 构建戳写入失败：%s" % _e)

print("生成", out)
print("  改动：" + "、".join(PATCH_LOG))
print("  大小 %.1f KB；内置企业 %d 家；内置名单 %d 家" %
      (out.stat().st_size / 1024, COMPANIES_JSON.count('"id"'), len(NAMES)))