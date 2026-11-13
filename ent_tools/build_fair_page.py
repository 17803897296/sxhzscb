"""把交接包的对话式采集页打包成离线单页，并把「名单 + 匹配」接在对话后面。

输入：_handoff/shuangxuan/app/static/{fair.html,fair.js,fair.css,style.css}
      demo/index.html（取 COMPANIES 真实企业数据与排序规则）
输出：demo/fair.html

页面流程（全在一页内）：
  画像对话 → 生成画像 → 导入企业名单 → 点「开始匹配」 → 结果列表
"""

import json
import re
import sys
from pathlib import Path

from fair_report_js import REPORT_JS

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
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

def patch_fair_js(js):
    """按队长的要求改采集流程。只改本页打包出来的副本，不动交接包原件。"""
    edits = []

    # ① 不再问手机号，只留邮箱
    js = js.replace(
        "ask: '如果现场有企业想联系你，留个手机号或邮箱？（可跳过）',",
        "ask: '现场有企业想联系你，留个邮箱？（可跳过）',")
    js = js.replace(
        "placeholder: '手机号或邮箱，可跳过',",
        "placeholder: '邮箱，可跳过',")
    js = js.replace(
        """    const digits = t.replace(/[\\s-]/g, '');
    if (/^1[3-9]\\d{9}$/.test(digits)) return { value: { phone: digits, email: '' } };
    return { error: '看起来不是手机号或邮箱。可以直接点「跳过」。' };""",
        """    return { error: '看起来不是邮箱。可以直接点「跳过」。' };""")
    edits.append("删除手机号提问")

    # ①b 联系方式整步删除（邮件也不问）
    _before = js
    js = js.replace(
        "if (!state.answers.identity.phone && !state.answers.identity.email) flow.push(contactStep);",
        "")
    if js == _before:
        raise RuntimeError("联系方式步骤没删掉，检查 fair.js 的 buildFlow")

    # 连定义一起删，别留死代码
    c0 = js.find("const contactStep = {")
    if c0 < 0:
        c0 = js.find("contactStep = {")
    if c0 > 0:
        c1 = js.find("\n};", c0)
        if c1 > c0:
            js = js[:c0] + js[c1 + 4:]
    edits.append("删除联系方式步骤")

    # ② 删除「找秋招还是实习」整步
    stage_start = js.find("    fairStep('stage',")
    stage_end = js.find("    fairStep('direction',", stage_start)
    if stage_start > 0 and stage_end > stage_start:
        js = js[:stage_start] + js[stage_end:]
        edits.append("删除求职阶段步骤")

    # ②b 方向步骤：去掉「参考项 + alias」这套离线岗位族规则，改成纯文本「有什么实习」。
    #    学生写的软件工程 / 数据分析 / 运筹优化等原样记录，不再被硬映射成岗位族名。
    OLD_DIR = """    fairStep('direction', '想做什么方向或岗位？点参考项可以，自己写也可以。', {
      options: Q.major_directions.map(d => d.name),
      allowCustom: true,
      placeholder: '比如：供应链计划 / 采购 / 数据分析，也可以直接写岗位',
      hint: '选项只是参考，不锁定岗位族；自己写的会原样记录，仅作参考。',
      alias: {
        供应链: '运营与供应链管理', 运营: '运营与供应链管理', 物流: '运营与供应链管理',
        金融: '金融服务与金融工程', 投资: '金融服务与金融工程',
        数据: '大数据分析与决策', 分析: '大数据分析与决策', 商业分析: '大数据分析与决策',
        工程: '复杂工程与智能管理', 项目: '复杂工程与智能管理',
        智能: '智能系统管理', 系统: '智能系统管理',
      },
      after(value, a) { a.direction_interest = value; },
      reset(a) { a.direction_interest = ''; },
    }),"""
    NEW_DIR = """    fairStep('internship', '做过哪些实习？写岗位或方向就行，也可以留空。', {
      allowCustom: true,
      optional: true,
      placeholder: '比如：软件工程 / 数据分析 / 运筹优化，做过几个写几个',
      hint: '自由填写，原样记录，交给匹配模型判断你和对口岗位的相关度；不套任何岗位族。想跳过就输入「跳过」。',
      after(value, a) { a.direction_interest = value; },
      reset(a) { a.direction_interest = ''; },
    }),"""
    if OLD_DIR not in js:
        raise RuntimeError("方向步骤没匹配到，检查 fair.js 的 direction 步骤")
    js = js.replace(OLD_DIR, NEW_DIR, 1)
    edits.append("方向步骤改为纯文本「有什么实习」，去掉离线岗位族参考项与 alias")

    # ②c 删除「想在哪座城市工作？」（期望城市）整步；城市维度改按「期望城市不限，中性分 0.60」处理。
    rg_start = js.find("    fairStep('region',")
    rg_end = js.find("    fairStep('timeline',", rg_start)
    if rg_start > 0 and rg_end > rg_start:
        js = js[:rg_start] + js[rg_end:]
        edits.append("删除期望城市（想去哪个城市）步骤")

    # 插入「岗位推荐」步骤：按专业/实习给候选岗位，学生勾选 -> intended_jobs[]
    fs_anchor = "function fairSteps() {\n  return [\n"
    if fs_anchor in js:
        js = js.replace(fs_anchor, fs_anchor + "    jobRecommendStep(),\n", 1)
        edits.append("插入岗位推荐步骤")

    # ③ 删除「最晚什么时候落地」整步
    tl_start = js.find("    fairStep('timeline',")
    tl_end = js.find("  ];\n}\n\nfunction buildFlow", tl_start)
    if tl_start > 0 and tl_end > tl_start:
        js = js[:tl_start] + js[tl_end:]
        edits.append("删除时间窗步骤")

    # ③b 按用户允许「删减问题」：不再插入「是否考虑专业不对口的工作」这一步。
    # 岗位对口已由采集端的「推荐岗位 → 学生勾选」承担，重复提问没有信息增量。

    # ④ 偏好卡改为必答
    js = js.replace("ask: '要不要让匹配更懂你？选填，约 20 秒。',",
                    "ask: '最后一步，必答：让匹配更懂你。',")
    js = js.replace("""        <div class="cc-title">要不要让匹配更懂你？</div>
        <div class="cc-sub">选填，约 20 秒。偏好只影响排序权重，不是硬门槛；跳过不影响这份画像。</div>""",
                    """        <div class="cc-title">让匹配更懂你</div>
        <div class="cc-sub">必答，约 20 秒。至少选 1 项最看重的；只影响排序权重，不是硬门槛。</div>""")
    js = js.replace("""          <button type="button" class="btn btn-p" data-act="save">保存并继续</button>
          <button type="button" class="btn btn-g" data-act="skip">跳过</button>""",
                    """          <button type="button" class="btn btn-p" data-act="save">保存并继续</button>""")
    js = js.replace("""        root.querySelector('[data-act="save"]').onclick = () => {
          const res = {""",
                    """        root.querySelector('[data-act="save"]').onclick = () => {
          if (!selected.length) {
            const tip = root.querySelector('.cc-sub');
            if (tip) tip.textContent = '未选「看重什么」，将按默认 7 项权重匹配（仍可正常出报告）。';
          }
          const res = {""")
    js = js.replace("""        root.querySelector('[data-act="skip"]').onclick = () => {
          state.awaiting = null;
          el.classList.add('resolved');
          root.querySelectorAll('button,input').forEach(x => { try { x.disabled = true; } catch (e) {} });
          resolve({ skipped: true });
        };
""", "")
    edits.append("偏好卡改为必答")

    # ③c v10：在偏好卡（含家乡城市）之后，追问三类排序偏好。
    #    城市 7 类 / 企业类型三档 / 行业排序，结果写入
    #    state.answers.city_rank / company_type_rank / industry_rank，
    #    经 SHIM _buildDocument 落进 doc.answers，报告端 getProfile 直接读取参与重算。
    #    v11：排序追问必须与用户在「最看重什么」里选的关注点对应——没选城市条件
    #    就不显示城市 7 类（新一线等）排序，没选企业类型就不显示三档排序，以此类推。
    _after_pref = "  flow.push(preferencesCardStep());\n"
    if _after_pref not in js:
        raise RuntimeError("偏好卡步骤没找到，检查 fair.js 的 buildFlow")
    js = js.replace(_after_pref,
        "  flow.push(preferencesCardStep());\n"
        "  /* v11：只追问用户选中的关注点；guard 在运行时读取偏好卡结果 */\n"
        "  const _prefPicked = k => (((state.answers.values || {})[k] || 0) > 0);\n"
        "  const _rankIfPicked = (s, k) => Object.assign(s, { guard: () => _prefPicked(k) });\n"
        "  flow.push(_rankIfPicked(cityRankStep(), 'city'));\n"
        "  flow.push(_rankIfPicked(companyTypeRankStep(), 'company_type'));\n"
        "  flow.push(_rankIfPicked(industryRankStep(), 'industry'));\n", 1)
    edits.append("插入城市/企业类型/行业三类排序追问（按关注点联动）")

    # ③d v11：流程引擎支持 guard，跳过的步骤连提问气泡都不出现。
    _run_anchor = "    const step = state.flow[i];\n"
    if _run_anchor not in js:
        raise RuntimeError("流程引擎 step 定位没找到，检查 fair.js 的 runFlow")
    js = js.replace(_run_anchor,
        _run_anchor + "    if (step.guard && !step.guard()) continue;  // v11：按所选关注点动态跳过\n", 1)
    edits.append("排序追问按关注点动态跳过（未选的维度不显示）")

    # ③e v11.1：?detail=企业名 / ?report=1 直接进报告页，不再从头跑采集问卷
    _init_anchor = "async function init() {\n"
    if _init_anchor not in js:
        raise RuntimeError("init() 定位没找到，检查 fair.js 的 init")
    js = js.replace(_init_anchor, _init_anchor + (
        "  /* v11.1 报告模式：新标签打开 ?detail= 时，本标签没有采集记录，\n"
        "     原逻辑会把用户当新访客从头问一遍 —— 这里直接渲染报告。 */\n"
        "  const _modeQ = new URLSearchParams(location.search);\n"
        "  if (_modeQ.get('detail') || _modeQ.get('report')) {\n"
        "    document.body.classList.add('report-only');\n"
        "    const _st = document.getElementById('status-t');\n"
        "    if (_st) _st.textContent = '双选会匹配报告';\n"
        "    document.title = (_modeQ.get('detail') ? _modeQ.get('detail') + ' · ' : '') + '双选会匹配报告';\n"
        "    let _mounted = false, _refreshed = false, _tries = 0;\n"
        "    const _go = function(){\n"
        "      try {\n"
        "        if (!_mounted && window.__mountMatch) {\n"
        "          window.__mountMatch({});\n"
        "          _mounted = true;\n"
        "        }\n"
        "        if (_mounted && !_refreshed && window.__triRefresh) {\n"
        "          window.__triRefresh();\n"
        "          _refreshed = true;\n"
        "        }\n"
        "      } catch (e) {}\n"
        "    };\n"
        "    _go();\n"
        "    const _tk = setInterval(function(){\n"
        "      _tries++; _go();\n"
        "      if ((_mounted && _refreshed) || _tries > 60) clearInterval(_tk);\n"
        "    }, 100);\n"
        "    return;\n"
        "  }\n"), 1)
    edits.append("?detail= / ?report=1 直接渲染报告，不再重跑采集问卷")

    return js, edits


html = (HANDOFF / "fair.html").read_text(encoding="utf-8")
style = (HANDOFF / "style.css").read_text(encoding="utf-8")
fair_css = (HANDOFF / "fair.css").read_text(encoding="utf-8")
fair_js = (HANDOFF / "fair.js").read_text(encoding="utf-8")
fair_js, PATCH_LOG = patch_fair_js(fair_js)
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
    ["industry", "行业对口"],
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

SHIM = r"""
<script>
/* ── 离线垫片：/api/fair/* 本地计算 ─────────────────────────────── */
const QUESTIONNAIRE = __QUESTIONNAIRE__;
/* v6：同源服务模式（http/https）启用真实写库与在线模型；file:// 双击仍完全离线 */
const FAIR_ONLINE = /^https?:$/.test(location.protocol);
const TRI_KEYS = ["city","home","company_type","industry","scale","industry_status","growth"];
const TRI_DEFAULT = { city: 18, home: 14, company_type: 16, industry: 14, scale: 14, industry_status: 12, growth: 12 };

const DEMO_RESUME = {
  ok: true, filename: "示例简历.docx", chars: 1860, sha256: "demo",
  method: "rules", model: "",
  fields: {
    name:   { value: "李小明", source: "regex", evidence: "姓名：李小明" },
    school: { value: "南京大学", source: "regex", evidence: "南京大学" },
    major:  { value: "管理科学与工程", source: "regex", evidence: "管理科学与工程" },
    degree: { value: "硕士", source: "regex", evidence: "硕士研究生" },
    grade:  { value: "研一", source: "regex", evidence: "研一" },
    graduation: { value: "2028 年 6 月", source: "regex", evidence: "预计 2028 年 6 月毕业" }
  },
  skills: [
    { name: "SQL 取数", evidence: "用 SQL 做库存周转分析" },
    { name: "Python / R 数据处理", evidence: "用 Python 清洗订单数据" }
  ],
  experiences: [
    { text: "某电商平台供应链实习：用 SQL 做库存周转分析，把补货周期从 14 天压到 9 天" },
    { text: "校级科研项目：用 Python 处理十万级订单数据，产出需求预测模型" }
  ],
  certificates: ["英语六级"],
  warnings: ["离线演示用的固定简历，不是你上传的文件"],
  llm: {}
};

function _resp(obj) {
  return Promise.resolve({ ok: true, status: 200,
    json: async () => obj, text: async () => JSON.stringify(obj) });
}

function _fairId() {
  const s = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let out = "";
  for (let i = 0; i < 8; i++) out += s[Math.floor(Math.random() * s.length)];
  return "FAIR-" + out;
}

function _buildDocument(body) {
  const a = body.answers || {};
  const fair = a.fair || {};
  const r = body.resume || {};
  const values = (a.values && typeof a.values === "object") ? a.values : {};
  const dims = QUESTIONNAIRE.value_dimensions || [];
  const sum = dims.reduce((n, [k]) => n + (Number(values[k]) || 0), 0);
  /* 采集端 -> 匹配端：学生填的五项分数直接归一成排序权重，不再硬编码。
     离线未填（全 0）时用默认五项 100；同源服务写库后由 fair_ingest 重建 payload。 */
  const tri_weights = {};
  const triRaw = {};
  TRI_KEYS.forEach(k => { triRaw[k] = Math.max(0, Number(values[k]) || 0); });
  const triSum = TRI_KEYS.reduce((n, k) => n + triRaw[k], 0);
  if (triSum > 0) TRI_KEYS.forEach(k => {
    tri_weights[k] = +(triRaw[k] / triSum * 100).toFixed(1);
  }); else Object.assign(tri_weights, TRI_DEFAULT);

  /* 产品范围：只做研三 / 大四，求职阶段一律按校招。不问学生。 */
  const COHORT_STAGE = "校招";
  const COHORT_DETAIL = "秋招校招";

  const required = [a.direction_interest];
  const answered = required.filter(Boolean).length;
  let score = Math.round((answered / required.length) * 70);
  if (r.uploaded) score += 20;
  if (sum > 0) score += 10;
  score = Math.min(100, score);

  const traits = [];
  if (a.intended_jobs && a.intended_jobs.length) traits.push("意向岗位：" + a.intended_jobs.join("、"));
  if (a.direction_interest) traits.push("实习：" + a.direction_interest);
  if (a.region) traits.push("期望城市：" + a.region);
  if (fair.stage_detail) traits.push("批次：" + fair.stage_detail);
  else traits.push("批次：" + COHORT_DETAIL + "（研三 / 大四，按校招）");
  if (sum > 0) {
    const top = dims.map(([k, l]) => [l, Number(values[k]) || 0])
                    .filter(x => x[1] > 0).sort((x, y) => y[1] - x[1]);
    traits.push("偏好权重（排序用）：" + top.map(x => x[0] + x[1]).join("、"));
  } else {
    traits.push("未填偏好权重，按默认五项 100 处理");
  }
  if (fair.target_company_types && fair.target_company_types.length)
    traits.push("企业类型偏好（软）：" + fair.target_company_types.join("、"));
  if (fair.home_city) traits.push("家乡城市：" + fair.home_city);

  const missing = [];
  if (!a.direction_interest) missing.push({ why: "没填实习经历" });
  /* 期望城市步骤已删除：城市维度按「期望城市不限，中性分 0.60」处理，不再提示缺失 */

  return {
    document_type: "fair_profile", document_version: "1.1.0",
    schema_version: QUESTIONNAIRE.schema_version,
    generated_at: new Date().toISOString(), fair_id: _fairId(),
    collector: { version: "0.2.0", engine: "offline-demo" },
    privacy: { contact_masked: false,
               storage: FAIR_ONLINE ? "sqlite:fair_profiles（同源服务待写库）"
                                    : "浏览器 localStorage + 可导出 JSON 入库" },
    consent: body.consent || {},
    identity: (r.fields && Object.fromEntries(
      Object.entries(r.fields).map(([k, v]) => [k, v.value || v]))) || {},
    fair: {
      event_date: fair.event_date || "",
      target_stage: COHORT_STAGE,
      stage_detail: COHORT_DETAIL,
      flagship_experience: fair.flagship_experience || "",
      target_company_types: fair.target_company_types || [],
      home_city: fair.home_city || ""
    },
    profile: { skills: (r.skills || []).map(s => ({ name: s.name })), values },
    answers: {
      intended_jobs: a.intended_jobs || [],
      tri_weights,
      direction_interest: a.direction_interest || "",
      region: a.region || "", timeline: a.timeline || "",
      major_fit: a.major_fit || "不限专业，都看看",
      values, skills: r.skills || [],
      city_rank: a.city_rank || {},
      company_type_rank: a.company_type_rank || {},
      industry_rank: a.industry_rank || {}
    },
    resume: {
      uploaded: !!r.uploaded,
      extraction: r.uploaded ? { method: r.method || "rules",
        experiences: r.experiences || [], skills: r.skills || [],
        certificates: r.certificates || [] } : {}
    },
    quality: {
      score,
      level: score >= 85 ? "可直接进入匹配" : score >= 60 ? "建议补充几项" : "信息偏少",
      missing,
      warnings: [FAIR_ONLINE
        ? "同源服务模式：画像会写入 careersafari.db，匹配端报告按最新画像重建"
        : "离线模式：画像存在浏览器本地，点下载 JSON 后运行 fair_ingest.py 即可入库"]
    },
    summary: {
      headline: [a.region, a.direction_interest, fair.stage_detail].filter(Boolean).join(" · ") || "未填完整",
      traits
    },
    notes: [FAIR_ONLINE
      ? "同源服务模式：/api/fair/* 由 fair_server.py 提供，画像真实落库"
      : "离线演示版：/api/fair/* 由本地垫片模拟，与服务端字段一致"],
    downstream: { match_ready: score >= 60 }
  };
}

const _realFetch = window.fetch;
window.fetch = function (url, opts) {
  const u = String(url);
  if (u.indexOf("/api/fair/questionnaire") >= 0) return _resp(QUESTIONNAIRE);
  if (u.indexOf("/api/fair/resume") >= 0) {
    /* v11：在线模式透传给 fair_server 的真实简历解析；离线双击明确告知不支持。
       不再把任何上传都替换成 DEMO_RESUME 那份「管科 + 供应链实习」固定示例。 */
    if (FAIR_ONLINE) return _realFetch.call(window, url, opts);
    return _resp({ ok: false,
      error: '离线双击模式无法解析简历；请用 http://127.0.0.1:8102/fair.html 打开本页，或选「没有简历，直接聊」' });
  }
  if (u.indexOf("/api/fair/profile") >= 0) {
    let body = {};
    try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) {}
    const doc = _buildDocument(body);
    const keepLocal = () => {
      try { localStorage.setItem("fair_profile", JSON.stringify(doc)); } catch (e) {}
    };
    keepLocal();
    setTimeout(() => { if (window.__mountMatch) window.__mountMatch(doc); }, 420);
    const offline = () => _resp({ ok: true, saved: false,
      storage: "浏览器 localStorage + 导出 JSON", document: doc });
    if (!FAIR_ONLINE) return offline();
    /* v6 真写库：同源服务把 doc 交给 fair_server -> fair_ingest -> fair_profiles，
       成功后把 storage 改回 sqlite 并回写 localStorage，报告层随后拉最新 payload。 */
    return _realFetch.call(window, "/api/fair/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: doc })
    }).then(r => r.json()).then(j => {
      if (!j || !j.saved) return offline();
      doc.privacy.storage = "sqlite:fair_profiles";
      doc.saved_to_db = { fair_id: j.fair_id, payload_rebuilt: !!j.payload_rebuilt };
      keepLocal();
      if (window.__triRefresh) window.__triRefresh();
      return _resp(Object.assign({ document: doc }, j));
    }).catch(() => offline());
  }
  return _realFetch.apply(this, arguments);
};
</script>
"""
SHIM = SHIM.replace("__QUESTIONNAIRE__", json.dumps(questionnaire, ensure_ascii=False))

# 采集端「岗位推荐」：按专业 + 实习给几个具体岗位名，学生勾选后写进 intended_jobs[]。
# 输出不是岗位族、不是企业事实；联网时由 /api/fair/recommend-jobs 调模型，离线时规则兜底。
RECOMMEND_JS = r"""<script>
function _jobRecommendRules(major, exps, skills, exclude) {
  /* 所有专业统一校招口径：只写校招公告里应届可投的具体岗位名，不写岗位族 */
  const MAJOR = {
    "管理科学与工程": ["管培生（运营/综合方向）","供应链管理类管培生","项目管理类管培生",
      "数据分析岗","商业分析岗","经营分析岗","供应链计划专员","需求计划专员","采购管培生",
      "数据智能培训生","BI/数据分析培训生","咨询分析师","风控/合规专员","ERP实施顾问"],
    "运营与供应链管理": ["供应链管培生","供应链计划专员","需求计划专员","采购管培生",
      "物流运营专员","库存计划专员","生产计划专员","运营管理培训生","供应商管理专员",
      "仓储管理专员","物控专员","供应链数据分析岗"],
    "大数据分析与决策": ["数据分析岗","数据分析培训生","BI分析师","商业分析岗",
      "数据开发工程师（校招）","数据运营专员","策略分析岗","数据产品经理（校招）",
      "算法工程师（校招）","经营分析岗","数据治理专员","用户研究分析师"],
    "金融服务与金融工程": ["行业研究助理","量化研究岗","风险管理培训生","投行承做岗（校招）",
      "金融数据分析岗","管培生（金融方向）","金融市场岗","合规专员","财富顾问培训生",
      "信用分析岗","交易员助理","金融科技培训生"],
    "复杂工程与智能管理": ["项目管理培训生","工程咨询助理","数据分析岗","流程改善专员",
      "系统方案工程师（校招）","项目经理助理","造价/合约专员","工业工程师","质量工程师",
      "智能制造培训生","BIM工程师（校招）","供应链计划专员"],
    "智能系统管理": ["产品经理（校招）","数据分析岗","算法产品经理（校招）","系统运营专员",
      "决策支持分析岗","产品运营培训生","信息管理专员","ERP实施顾问","数据产品经理（校招）",
      "用户体验研究员（校招）","BI分析师","咨询分析师"],
    "工业工程": ["工业工程师","生产计划专员","工艺改善工程师","质量工程师","运营分析岗",
      "供应链管培生","精益改善专员","制造管培生","设备工程师（校招）","IE工程师",
      "物控专员","项目管理培训生"],
    "计算机": ["后端开发工程师（校招）","算法工程师（校招）","数据分析岗",
      "测试开发工程师（校招）","前端开发工程师（校招）","客户端开发工程师（校招）",
      "运维开发工程师（校招）","安全工程师（校招）","数据开发工程师（校招）",
      "AI工程师（校招）","软件开发工程师（校招）","技术支持工程师（校招）"],
    "软件工程": ["软件开发工程师（校招）","后端开发工程师（校招）","算法工程师（校招）",
      "测试开发工程师（校招）","前端开发工程师（校招）","移动端开发工程师（校招）",
      "DevOps工程师（校招）","数据开发工程师（校招）","质量工程师（校招）",
      "产品工程师（校招）","嵌入式软件工程师（校招）","技术支持工程师（校招）"],
    "金融": ["行业研究助理","量化研究岗","财富顾问培训生","风险管理培训生",
      "投行承做岗（校招）","管培生（金融方向）","客户经理培训生","金融数据分析岗",
      "信贷管理专员","合规专员","运营培训生（银行方向）","金融市场岗"]
  };
  const RULES = [
    [["供应链","库存","补货","采购","物流","仓储","周转","供应商"],
     ["供应链计划专员","需求计划专员","采购管培生","物流运营专员"],"实习里做过供应链/库存相关的实际工作"],
    [["SQL","Python","数据","分析","预测","报表","指标","建模","取数"],
     ["数据分析岗","商业分析岗","BI分析师","数据运营专员"],"实习里用到取数、分析或预测"],
    [["产品","需求文档","原型","用户","PRD","竞品"],
     ["产品经理（校招）","产品运营培训生","商业分析岗"],"实习里接触过产品或需求工作"],
    [["项目","协调","排期","交付","流程","推进"],
     ["项目管理培训生","流程改善专员","项目经理助理"],"实习里有项目协调或流程推进经历"],
    [["销售","客户","商务","BD","渠道","市场"],
     ["客户经理培训生","市场分析岗","商务专员（校招）"],"实习里接触过客户或市场"],
    [["财务","会计","审计","预算","成本"],
     ["财务分析岗","成本分析专员","内审/风控专员"],"实习里接触过财务或成本"]
  ];
  const text = (exps || []).concat(skills || []).join(" ").toLowerCase();
  let base = MAJOR[major] || [];
  if (!base.length) {
    for (const k in MAJOR) { if (major && (major.indexOf(k) >= 0 || k.indexOf(major) >= 0)) { base = MAJOR[k]; break; } }
  }
  if (!base.length) base = ["管培生（综合方向）","数据分析岗","商业分析岗","项目管理培训生"];
  const cands = [], seen = new Set();
  RULES.forEach(function(pair) {
    if (pair[0].some(function(k) { return text.indexOf(k.toLowerCase()) >= 0; }))
      pair[1].forEach(function(j) { cands.push({ title: j, why: pair[2] }); });
  });
  base.forEach(function(j) { cands.push({ title: j, why: "与专业「" + (major || "未填") + "」的培养方向对口" }); });
  const out = [];
  cands.forEach(function(c) { if (!seen.has(c.title)) { seen.add(c.title); out.push(c); } });
  /* 「换一批」：优先给没展示过的，不够再把旧的补上，保证永远有候选 */
  const excl = new Set(exclude || []);
  const fresh = out.filter(function(c) { return !excl.has(c.title); });
  const pool = fresh.length >= 6 ? fresh
    : fresh.concat(out.filter(function(c) { return excl.has(c.title); }));
  return { ok: true, mode: "rules", candidates: pool.slice(0, 6),
           notice: "离线（双击）模式：未调用模型，下面是本地规则推荐；想让模型按你的经历生成，请用 http://127.0.0.1:8102/fair.html 打开本页。" };
}

function jobRecommendStep() {
  return {
    id: 'job-recommend', kind: 'card',
    ask: '根据你说的专业和实习，我先给几个今年校招能投的岗位，勾几个最想去的；不满意可以换一批。',
    async render() {
      const a = state.answers;
      const major = (a.identity && a.identity.major) || a.major || '';
      const r0 = state.resume || {};
      const exps = (r0.experiences || (r0.extraction && r0.extraction.experiences) || [])
        .map(function(x) { return (x && x.text) || x || ''; }).filter(Boolean);
      const skills = (r0.skills || (r0.extraction && r0.extraction.skills) || [])
        .map(function(x) { return (x && x.name) || x || ''; }).filter(Boolean);
      const basis = exps.length ? exps.join('；') : (major ? major + ' 的核心课程' : '尚未提供专业/实习');
      let cands = [], tag = '', notice = '';
      const shown = [];
      async function load(exclude) {
        let r;
        try {
          r = await (await fetch('/api/fair/recommend-jobs', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ major: major, experiences: exps, skills: skills, exclude: exclude })
          })).json();
        } catch (e) { r = null; }
        if (!r || !r.ok || !(r.candidates || []).length) {
          r = _jobRecommendRules(major, exps, skills, exclude);
        }
        cands = r.candidates || [];
        tag = (r.mode === 'llm') ? '（模型生成 · 校招口径）'
            : (FAIR_ONLINE ? '（规则兜底 · 校招口径）' : '（离线规则 · 双击模式）');
        notice = r.notice || '点一个岗位看推荐依据。';
      }
      await load([]);
      const html = '<div class="chat-card">' +
        '<div class="cc-title">推荐岗位 <span id="job-tag">' + esc(tag) + '</span></div>' +
        (FAIR_ONLINE ? '' :
          '<div class="cc-sub" style="color:#b45309">你现在是双击打开（离线模式），推荐不调模型。想让模型按你的专业和实习生成，请用 <b>http://127.0.0.1:8102/fair.html</b> 打开本页。</div>') +
        '<div class="cc-sub">依据：' + esc(basis) + '。都是校招可投的具体岗位名；勾了才算数，不会写进任何企业资料。</div>' +
        '<div class="cc-chips" id="job-cands"></div>' +
        '<div class="cc-sub muted" id="job-why"></div>' +
        '<input id="job-custom" type="text" maxlength="120" placeholder="还想加别的岗位？直接写，用逗号分开">' +
        '<div class="cc-actions">' +
          '<button type="button" class="btn btn-g" data-act="refresh">不满意，换一批</button>' +
          '<button type="button" class="btn btn-p" data-act="ok">选好了</button>' +
          '<button type="button" class="btn btn-g" data-act="skip">跳过，我自己写</button>' +
        '</div></div>';
      return makeCard(html, function(root, done) {
        /* 用岗位名而不是下标记录选择：换一批后已勾选的不会丢 */
        const selTitles = new Set();
        const chipsBox = root.querySelector('#job-cands');
        const whyBox = root.querySelector('#job-why');
        function paint() {
          chipsBox.innerHTML = cands.map(function(c, i) {
            return '<button type="button" class="chip" data-i="' + i + '" title="' + esc(c.why || '') + '">' + esc(c.title) + '</button>';
          }).join('') || '<span class="muted">这一批没有更多岗位了，可以直接手写。</span>';
          root.querySelector('#job-tag').textContent = tag;
          whyBox.textContent = notice;
          chipsBox.querySelectorAll('[data-i]').forEach(function(b) {
            const c = cands[+b.dataset.i];
            if (c && selTitles.has(c.title)) { b.style.background = 'var(--accent, #2563eb)'; b.style.color = '#fff'; }
            b.onclick = function() {
              if (!c) return;
              if (selTitles.has(c.title)) { selTitles.delete(c.title); b.style.background = ''; b.style.color = ''; }
              else { selTitles.add(c.title); b.style.background = 'var(--accent, #2563eb)'; b.style.color = '#fff'; }
              whyBox.textContent = c.title + '：' + (c.why || '');
            };
          });
        }
        paint();
        root.querySelector('[data-act="refresh"]').onclick = async function() {
          cands.forEach(function(c) { if (c && c.title && shown.indexOf(c.title) < 0) shown.push(c.title); });
          whyBox.textContent = '正在换一批…';
          await load(shown.slice(-40));
          paint();
        };
        root.querySelector('[data-act="skip"]').onclick = function() { done({ skipped: true, value: [] }); };
        root.querySelector('[data-act="ok"]').onclick = function() {
          const custom = (root.querySelector('#job-custom').value || '')
            .split(/[,，、]/).map(function(s) { return s.trim(); }).filter(Boolean);
          done({ value: Array.from(selTitles).concat(custom) });
        };
      });
    },
    summary(res) { return (res && res.value && res.value.length) ? '意向岗位：' + res.value.join('、') : '未选岗位'; },
    apply(res) { if (!res || res.skipped) return; state.answers.intended_jobs = res.value; },
    reset() { state.answers.intended_jobs = []; },
  };
}

const _prevFetch = window.fetch;
window.fetch = function(url, opts) {
  const u = String(url);
  if (u.indexOf('/api/fair/recommend-jobs') >= 0) {
    let body = {};
    try { body = JSON.parse((opts && opts.body) || '{}'); } catch (e) {}
    const fallback = () => _resp(_jobRecommendRules(
      body.major || '', body.experiences || [], body.skills || []));
    /* v6：以前这一层无条件拦截，导致同源服务下也永远走规则、模型用不上。
       现在在线时先打真实端点（模型生成），失败或空结果才回退规则。 */
    if (FAIR_ONLINE) {
      return _prevFetch.apply(this, arguments)
        .then(res => res.json())
        .then(j => (j && j.candidates && j.candidates.length) ? _resp(j) : fallback())
        .catch(() => fallback());
    }
    return fallback();
  }
  return _prevFetch.apply(this, arguments);
};

/* v10：三类排序追问（城市 7 类 / 企业类型三档 / 行业），写入 state.answers.*_rank，
   报告端 getProfile 读取后参与实时重算。点标签按看重程度排序，最多点的排最前。 */
function rankingStep(kind, title, desc, items, applyKey) {
  return {
    id: 'rank-' + kind,
    kind: 'card',
    ask: title,
    async render() {
      let order = [];
      const html = '<div class="chat-card">'
        + '<div class="cc-title">' + esc(title) + '</div>'
        + '<div class="cc-sub">' + esc(desc) + '</div>'
        + '<div class="cc-chips" id="rank-chips">'
        + items.map(function(it){ return '<button type="button" class="chip" data-it="' + esc(it) + '">' + esc(it) + '</button>'; }).join('')
        + '</div>'
        + '<div class="cc-sub" id="rank-order" style="margin-top:8px">已选顺序：（点标签按看重程度排序，可再点取消）</div>'
        + '<div class="cc-actions">'
        + '<button type="button" class="btn btn-p" data-act="ok">确定</button>'
        + '<button type="button" class="btn btn-g" data-act="reset">重置</button>'
        + '<button type="button" class="btn btn-g" data-act="skip">用默认顺序</button>'
        + '</div></div>';
      return makeCard(html, function(root, done) {
        const chips = root.querySelector('#rank-chips');
        const orderEl = root.querySelector('#rank-order');
        const paint = function() {
          chips.querySelectorAll('.chip').forEach(function(b){
            const it = b.dataset.it;
            if (order.indexOf(it) >= 0) { b.style.background = 'var(--accent, #2563eb)'; b.style.color = '#fff'; }
            else { b.style.background = ''; b.style.color = ''; }
          });
          orderEl.innerHTML = '已选顺序：' + (order.length
            ? order.map(function(it, i){ return (i+1) + '.' + esc(it); }).join(' ＞ ')
            : '（点标签按看重程度排序，可再点取消）');
        };
        chips.querySelectorAll('.chip').forEach(function(b){
          b.onclick = function() {
            const it = b.dataset.it;
            const idx = order.indexOf(it);
            if (idx >= 0) order.splice(idx, 1); else order.push(it);
            paint();
          };
        });
        root.querySelector('[data-act="ok"]').onclick = function() {
          const d = {};
          order.forEach(function(it, i){ d[it] = i + 1; });
          let tail = order.length + 1;
          items.forEach(function(it){ if (!(it in d)) d[it] = tail++; });
          done({ value: d });
        };
        root.querySelector('[data-act="reset"]').onclick = function() { order = []; paint(); };
        root.querySelector('[data-act="skip"]').onclick = function() {
          const d = {}; items.forEach(function(it, i){ d[it] = i + 1; });
          done({ value: d });
        };
        paint();
      });
    },
    summary(res) {
      if (!res || !res.value) return '未排序（用默认）';
      const keys = Object.keys(res.value).sort(function(a,b){ return res.value[a]-res.value[b]; });
      return '排序：' + keys.map(function(k){ return k + '#' + res.value[k]; }).join('、');
    },
    apply(res) { if (!res || res.skipped) return; state.answers[applyKey] = res.value; },
    reset() { state.answers[applyKey] = {}; },
  };
}
function cityRankStep() {
  return rankingStep('city',
    '给这几类城市排个序（最看重的排最前）',
    '城市条件按 7 类算分：一线 / 新一线 / 其他省会 / 经济发达 / 交通发达 / 生态友好 / 其他。按你找工作时看重程度点一下，最多点的排最前；不点用默认顺序。一家企业命中多类时，取你最看重的那类算分。',
    ['一线','新一线','其他省会','经济发达','交通发达','生态友好','其他'],
    'city_rank');
}
function companyTypeRankStep() {
  return rankingStep('ct',
    '给三类企业排个序（最想去的排最前）',
    '企业类型分三档：国央企 / 民企 / 外企。按你的偏好点一下排序；不点用默认顺序。',
    ['国央企','民企','外企'],
    'company_type_rank');
}
function industryRankStep() {
  return rankingStep('ind',
    '给这些行业排个序（最想进的排最前）',
    '行业只做排序偏好（对口是硬约束，不对口的不进推荐）。按你感兴趣程度点一下排序；不点用默认顺序。',
    ['IT','金融','制造','建筑','医药','能源','教育','消费','未知'],
    'industry_rank');
}
</script>
"""

MATCH = r"""
<style>
#matchbox{margin:26px 0 60px}
#matchbox .mb-card{background:#fff;border:1px solid #e6e8ec;border-radius:14px;
 padding:18px 18px 16px;box-shadow:0 2px 10px rgba(30,50,110,.05)}
#matchbox h3{margin:0 0 4px;font-size:17px;font-weight:650;color:#16181d}
#matchbox .mb-sub{color:#6b7280;font-size:13px;margin:0 0 14px}
#matchbox textarea{width:100%;min-height:88px;border:1px solid #dfe3ea;border-radius:10px;
 padding:10px 12px;font:13px/1.6 inherit;resize:vertical;box-sizing:border-box}
#matchbox .mb-inputs{display:flex;gap:8px;align-items:center;margin-bottom:10px}
#matchbox .mb-plus{flex:0 0 auto;width:44px;height:44px;border-radius:11px;border:1px dashed #c3cbd8;
 background:#f7f9fc;color:#2f5bd3;font-size:24px;font-weight:300;line-height:42px;text-align:center;
 cursor:pointer;user-select:none}
#matchbox .mb-plus:hover{background:#eef3fe;border-color:#9db2e0}
#matchbox #murl{flex:1;height:44px;border:1px solid #dfe3ea;border-radius:11px;padding:0 12px;
 font:13px/1.4 inherit;box-sizing:border-box}
#matchbox .chip{display:inline-flex;align-items:center;gap:6px;background:#eef3fe;color:#2f5bd3;
 border-radius:999px;font-size:12px;padding:5px 10px;margin:0 6px 8px 0}
#matchbox .chip b{font-weight:600}
#matchbox .chip i{font-style:normal;cursor:pointer;opacity:.6}
#matchbox .chip i:hover{opacity:1}
#matchbox .mb-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
#matchbox button{cursor:pointer;border:0;border-radius:10px;padding:10px 16px;
 font:14px/1.2 inherit;font-weight:600}
#matchbox .mb-ghost{background:#f1f3f7;color:#4b5563}
#matchbox .mb-go{background:#2f5bd3;color:#fff;flex:1;min-width:160px}
#matchbox .mb-go:hover{background:#2549ad}
#matchbox .mb-fast{background:#e8f3ec;color:#1f7a4d}
#matchbox .mb-fast:hover{background:#d9ebe1}
#matchbox .mb-view{background:#fff;color:#2f5bd3;border:1px solid #b9c8e9}
#matchbox .mb-view:hover{background:#f3f7ff}
#matchbox .mb-view:disabled{background:#f5f6f8;color:#9aa1ad;border-color:#e1e4e9;cursor:not-allowed}
#mcontentsdialog{width:min(860px,calc(100vw - 32px));max-height:min(86vh,900px);padding:0;
 border:0;border-radius:16px;box-shadow:0 24px 80px rgba(15,23,42,.25);color:#16181d}
#mcontentsdialog::backdrop{background:rgba(15,23,42,.42);backdrop-filter:blur(2px)}
#mcontentsdialog .mc-head{position:sticky;top:0;z-index:2;display:flex;align-items:flex-start;
 gap:14px;padding:18px 20px 14px;background:#fff;border-bottom:1px solid #e6e8ec}
#mcontentsdialog .mc-head h3{margin:0;font-size:18px}
#mcontentsdialog .mc-head p{margin:4px 0 0;color:#6b7280;font-size:12.5px}
#mcontentsdialog .mc-close{margin-left:auto;width:34px;height:34px;border:0;border-radius:9px;
 background:#f1f3f7;color:#4b5563;font-size:20px;line-height:1;cursor:pointer}
#mcontentsdialog .mc-body{padding:16px 20px 24px;overflow:auto;max-height:calc(86vh - 74px)}
#mcontentsdialog .mc-sum{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
#mcontentsdialog .mc-stat{background:#f5f7fb;border:1px solid #e5e8ee;border-radius:9px;
 padding:7px 10px;font-size:12.5px;color:#4b5563}
#mcontentsdialog .mc-stat b{color:#16181d}
#mcontentsdialog .mc-section{margin-top:18px}
#mcontentsdialog .mc-section h4{margin:0 0 8px;font-size:13px;color:#4b5563}
#mcontentsdialog .mc-list{display:grid;gap:8px}
#mcontentsdialog .mc-item{border:1px solid #e6e8ec;border-radius:10px;padding:10px 12px;
 background:#fff;font-size:13px;line-height:1.55}
#mcontentsdialog .mc-item-head{display:flex;gap:8px;align-items:baseline;justify-content:space-between}
#mcontentsdialog .mc-item-head b{font-weight:650;color:#16181d}
#mcontentsdialog .mc-badge{flex:none;border-radius:999px;padding:2px 8px;font-size:11px;
 background:#eef3fe;color:#2f5bd3}
#mcontentsdialog .mc-badge.ok{background:#e8f3ec;color:#1f7a4d}
#mcontentsdialog .mc-badge.warn{background:#fff6df;color:#92600b}
#mcontentsdialog .mc-meta{color:#6b7280;font-size:12px;margin-top:3px}
#mcontentsdialog .mc-jobs{color:#374151;font-size:12px;margin-top:5px}
#mcontentsdialog .mc-empty{color:#8a919d;font-size:12.5px;padding:8px 0}
#mres{margin-top:20px}
#mres .tier{margin-bottom:18px}
#mres .tier-h{display:flex;align-items:baseline;gap:8px;margin:0 0 10px;
 font-size:13px;font-weight:650;letter-spacing:.3px}
#mres .tier-h em{font-style:normal;font-weight:400;color:#9aa1ad;font-size:12px}
#mres .t1 .tier-h{color:#1f7a4d}
#mres .t2 .tier-h{color:#2f5bd3}
#mres .t3 .tier-h{color:#96a0ae}
#mres .t4 .tier-h{color:#8b94a3}
#mres .row{background:#fff;border:1px solid #e6e8ec;border-radius:11px;
 padding:12px 14px;margin-bottom:8px}
#mres .rowhead{display:flex;align-items:flex-start;gap:14px}
#mres .rowhead .nm{flex:1;min-width:0}
#mres .rowhead .nm b{display:block;font-size:14.5px;font-weight:620;color:#16181d}
#mres .rowhead .nm .meta{display:block;font-size:12px;color:#6b7280;margin-top:4px;line-height:1.65}
#mres .rowhead .nm .line{display:block;font-size:12px;color:#8b94a3;margin-top:2px;line-height:1.6}
#mres .rowhead .nm .hits{display:block;font-size:12px;color:#1f7a45;margin-top:4px;line-height:1.65}
#mres .rowhead .nm .misses{display:block;font-size:11.5px;color:#9aa1ad;margin-top:2px;line-height:1.6}
#mres .rowhead .sc{flex:none;font-size:21px;font-weight:700;color:#16181d;
 font-variant-numeric:tabular-nums;text-align:right;line-height:1.15}
#mres .rowhead .sc em{display:block;font-size:10px;font-style:normal;color:#9aa1ad;
 font-weight:400;margin-top:2px}
#mres .calc{margin-top:9px;padding:9px 11px;background:#f7f9fc;border:1px solid #e9edf4;
 border-radius:8px;font-size:11.8px;color:#5b6472;line-height:1.75}
#mres .calc b{color:#16181d}
#mres .calc .calc-sub{margin-top:3px;color:#7b8492}
#mres .calc details{margin-top:6px}
#mres .calc summary{cursor:pointer;color:#2f5bd3;font-size:11.5px;font-weight:600}
#mres .jobbox{margin-top:8px;border-top:1px dashed #e6e8ec;padding-top:8px;font-size:12px}
#mres .jobbox summary{cursor:pointer;color:#2f5bd3;font-weight:600;line-height:1.7}
#mres .jobbox .job{margin-top:8px;padding:8px 10px;background:#fbfcfe;border:1px solid #edf0f5;
 border-radius:8px}
#mres .jobbox .job-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
#mres .jobbox .job-h b{font-size:12.5px;color:#16181d}
#mres .jobbox .job-h span{font-size:11px;color:#8b94a3}
#mres .jobbox .job-req{margin-top:3px;font-size:11.5px;color:#6b7280;line-height:1.7}
#mres .jobbox .job-empty{color:#9aa1ad;line-height:1.7}
#mres .jobbox .jobfill{margin-left:8px;border:1px solid #cfd5e0;background:#fff;
 color:#2f5bd3;border-radius:6px;padding:2px 9px;font-size:11.5px;cursor:pointer}
#mres .jobbox .jobfill:hover{border-color:#2f5bd3}
#mres .jobbox .jobfill:disabled{opacity:.6;cursor:default}
#mres .note{font-size:12px;color:#9aa1ad;margin-top:14px;line-height:1.75}
/* v11.1 报告模式（?detail=企业名 / ?report=1）：只显示报告，不跑采集问卷 */
body.report-only .chat-head,
body.report-only #composer,
body.report-only .footer{display:none}
body.report-only .chat-wrap{max-width:1160px}
body.report-only #chat{padding-top:6px}
body.report-only #matchbox{margin:0 0 40px}
body.report-only #matchbox > .mb-card > h3,
body.report-only #matchbox .mb-sub,
body.report-only #matchbox .mb-inputs,
body.report-only #matchbox #mfilelist,
body.report-only #matchbox #mnames,
body.report-only #matchbox .mb-row,
body.report-only #matchbox #mhint,
body.report-only #matchbox #mthink,
body.report-only #matchbox #mstat{display:none}
</style>

<script>
/* ── 与 page 一致的企业数据 ─────────────────────────────────────── */
const COMPANIES = __COMPANIES__;
const BUILTIN_NAMES = __NAMES__;
let POOL = COMPANIES.slice();

const DIM_KEYS = ["salary","stability","growth","autonomy","impact","balance"];
const DIM_LABEL = { salary:"薪资", stability:"稳定", growth:"成长",
                    autonomy:"自主", impact:"影响", balance:"平衡" };
const NATURE_LEVEL = { "央企":1.0, "事业单位":1.0, "国企":0.9, "合资":0.6, "外企":0.5, "民企":0.35 };
const DIM_RULES = {
  growth:   [["培训","导师","带教","晋升","轮岗","成长","培养"], []],
  autonomy: [["自主","独立负责","独立完成","牵头"], ["协助","配合","执行上级"]],
  impact:   [["影响","服务客户","价值","成果应用"], []],
  balance:  [["双休","不加班","弹性","八小时","8小时"], ["倒班","轮班","长期出差","驻场"]]
};
const clamp01 = x => Math.max(0, Math.min(1, x));

function weightsOf(prof){
  const raw = (prof && prof.values) || {};
  const vals = {}; let total = 0;
  DIM_KEYS.forEach(k => { vals[k] = Math.max(0, Number(raw[k]) || 0); total += vals[k]; });
  if (total <= 0){ const even = Math.floor(100 / DIM_KEYS.length);
    DIM_KEYS.forEach(k => vals[k] = even); return { vals, uncollected: true }; }
  return { vals, uncollected: false };
}

function dimScores(c){
  const jobs = c.jobs || [];
  const text = jobs.map(j => j.requirements || "").join(" ");
  const salaryText = jobs.map(j => j.salary || "").join(" ");
  const out = { stability: NATURE_LEVEL[c.nature] ?? 0.5 };
  const notes = {
    stability: c.nature && NATURE_LEVEL[c.nature] != null
      ? "企业性质：" + c.nature + " → " + NATURE_LEVEL[c.nature].toFixed(2)
      : "企业性质未知，按中性分 0.50"
  };

  if (!salaryText || salaryText === "未知") {
    out.salary = 0.5;
    notes.salary = "岗位薪资未知，按中性分 0.50";
  } else if (/\d/.test(salaryText)) {
    out.salary = 1.0;
    notes.salary = "岗位薪资写明数字 → 1.00";
  } else {
    out.salary = 0.6;
    notes.salary = "岗位薪资只写「" + salaryText.slice(0, 12) + "」→ 0.60";
  }

  Object.keys(DIM_RULES).forEach(dim => {
    const [pos, neg] = DIM_RULES[dim];
    if (!text) {
      out[dim] = 0.5;
      notes[dim] = "没有岗位描述，按中性分 0.50";
      return;
    }
    const posHits = pos.filter(k => text.includes(k));
    const negHits = neg.filter(k => text.includes(k));
    let s = 0.5;
    let why = "岗位描述未命中关键词，按中性分 0.50";
    if (posHits.length) { s = 1.0; why = "命中「" + posHits.join("、") + "」→ 1.00"; }
    if (negHits.length) { s = 0.1; why = "命中反向词「" + negHits.join("、") + "」→ 0.10"; }
    out[dim] = s;
    notes[dim] = why;
  });
  return { scores: out, notes };
}

function baseOf(prof, c){
  const regions = Array.isArray(prof.region) ? prof.region
                : String(prof.region || "").split(/[、/，,\s]+/).filter(Boolean);
  const city = String(c.city || "未知");
  let cityPart = 0.6;
  let cityNote = "未填期望城市，按中性分 0.60";
  if (city === "未知") {
    cityNote = "企业城市未知，按中性分 0.60";
  } else if (regions.includes("不限")) {
    cityNote = "期望城市不限，按中性分 0.60";
  } else if (regions.length) {
    const hit = regions.find(r => r && (city.includes(r) || r.includes(city)));
    cityPart = hit ? 1.0 : 0;
    cityNote = hit ? "在期望城市「" + hit + "」→ 1.00" : "不在期望城市（企业城市：" + city + "）→ 0.00";
  }

  const words = String(prof.direction_interest || "").split(/[、/，,\s]+/).filter(w => w.length >= 2);
  const jobs = c.jobs || [];
  const blob = jobs.map(j => (j.title || "") + (j.requirements || "")).join(" ");
  let dirPart;
  let dirNote;
  let dirHits = [];
  if (!words.length) {
    dirPart = 0.5;
    dirNote = "未填期望方向，按中性分 0.50";
  } else if (!jobs.length) {
    dirPart = 0.3;
    dirNote = "没有岗位资料，方向只能按 0.30";
  } else {
    dirHits = words.filter(w => blob.includes(w));
    dirPart = clamp01(dirHits.length / words.length * 1.5);
    dirNote = dirHits.length
      ? "岗位命中方向词「" + dirHits.join("、") + "」→ " + dirPart.toFixed(2)
      : "岗位描述没有命中方向词「" + words.join("、") + "」→ 0.00";
  }

  const jobWithUrgency = jobs.find(j => ["高","中","低"].includes(j.urgency));
  const u = jobWithUrgency ? jobWithUrgency.urgency : "";
  const urgPart = { "高":1.0, "中":0.6, "低":0.2 }[u] ?? 0.4;
  const urgNote = u
    ? "岗位标注紧急度「" + u + "」→ " + urgPart.toFixed(2)
    : "岗位未标注紧急度，按 0.40";

  return {
    base: 40*cityPart + 35*dirPart + 25*urgPart,
    cityPart, dirPart, urgPart, cityNote, dirNote, urgNote, dirHits
  };
}

function profileFromDoc(doc){
  const a = (doc && doc.answers) || {};
  const f = (doc && doc.fair) || {};
  const id = (doc && doc.identity) || {};
  return {
    region: a.region || "", home_city: f.home_city || "",
    direction_interest: a.direction_interest || "",
    major_fit: a.major_fit || "不限专业，都看看",
    values: a.values || {}, target_company_types: f.target_company_types || [],
    stage_detail: f.stage_detail || "", major: id.major || ""
  };
}

const shortNote = s => String(s || "").replace(/\s*→\s*[\d.]+\s*$/, "").trim();

function match(companies, prof){
  const weightInfo = weightsOf(prof);
  const vals = weightInfo.vals;
  const types = Array.isArray(prof.target_company_types) ? prof.target_company_types : [];
  const home = String(prof.home_city || "未知");

  const scored = companies.map(c => {
    const baseInfo = baseOf(prof, c);
    const dimInfo = dimScores(c);
    const dims = dimInfo.scores;
    const dimNotes = dimInfo.notes;
    const preferencePoints = DIM_KEYS.reduce((n, k) => n + vals[k] * dims[k], 0);
    const factor = 1 + preferencePoints / 100;

    const reasons = [];
    if (baseInfo.cityPart >= 1) reasons.push("在期望城市");
    if (baseInfo.dirPart >= 1) reasons.push("方向对口");
    let bonus = 0;
    const bonusReasons = [];
    if (types.includes(c.nature)) {
      bonus += 5;
      bonusReasons.push("命中企业类型偏好 +5");
      reasons.push("命中企业类型偏好");
    }
    if (home !== "未知" && c.city === home) {
      bonus += 8;
      bonusReasons.push("就在家乡 +8");
      reasons.push("就在家乡");
    }
    const jobs = c.jobs || [];
    const hot = jobs.find(j => j.urgency === "高");
    if (hot) reasons.push(hot.title + " 急招");
    const top = jobs[0];

    // 命中 / 没中：把这家企业到底吃到了哪些分写清楚
    const hits = [];
    const misses = [];

    if (baseInfo.cityPart >= 1) {
      hits.push("城市（在期望城市「" + (c.city || "未知") + "」）");
    } else if (baseInfo.cityPart === 0) {
      misses.push("城市（不在期望城市，企业城市：" + (c.city || "未知") + "）");
    }

    const dirHits = baseInfo.dirHits || [];
    if (baseInfo.dirPart >= 1) {
      hits.push("方向（命中「" + dirHits.join("、") + "」）");
    } else if (baseInfo.dirPart > 0 && jobs.length) {
      hits.push("方向（部分命中 " + f2(baseInfo.dirPart) + "）");
    } else if (baseInfo.dirPart === 0) {
      misses.push("方向（未命中「" + String(prof.direction_interest || "") + "」）");
    }

    if (hot) {
      hits.push("急招（" + (hot.title || "岗位") + "）");
    } else if (baseInfo.urgPart >= 0.6) {
      hits.push("紧急度（中）");
    } else if (baseInfo.urgPart === 0.2) {
      misses.push("紧急度（低）");
    }

    DIM_KEYS.forEach(k => {
      const s = dims[k];
      if (s >= 0.6) {
        hits.push(DIM_LABEL[k] + "（" + shortNote(dimNotes[k]) + "）");
      } else if (s <= 0.1) {
        misses.push(DIM_LABEL[k] + "（" + shortNote(dimNotes[k]) + "）");
      }
    });

    if (types.includes(c.nature)) hits.push("企业类型（" + c.nature + " +5）");
    if (home !== "未知" && c.city === home) hits.push("家乡（" + home + " +8）");
    if (!jobs.length) misses.push("岗位（未查到，方向和紧急度只能按中性分）");

    const hitLine = hits.join(" · ") || "没有明显命中项，主要按中性分计算";
    const missLine = misses.join(" · ");
    const score = Math.round((baseInfo.base * factor + bonus) * 10) / 10;

    return {
      id: c.id, name: c.name, nature: c.nature, city: c.city,
      industry: c.industry || "未知", jobSource: c.jobSource || "未知",
      jobs: jobs,
      score: score,
      line: reasons.join(" · ") || (top ? top.title : "按综合条件"),
      hits: hits, misses: misses, hitLine: hitLine, missLine: missLine,
      gap: jobs.length ? "" : "未查到岗位，仅按企业与地域排序",
      calc: {
        base: baseInfo.base,
        cityPart: baseInfo.cityPart, dirPart: baseInfo.dirPart, urgPart: baseInfo.urgPart,
        cityNote: baseInfo.cityNote, dirNote: baseInfo.dirNote, urgNote: baseInfo.urgNote,
        dims: dims, dimNotes: dimNotes, weights: vals,
        preferencePoints: preferencePoints, factor: factor,
        bonus: bonus, bonusReasons: bonusReasons,
        uncollected: weightInfo.uncollected
      }
    };
  }).sort((a, b) => b.score - a.score);

  const withJobs = scored.filter(r => r.jobs && r.jobs.length);
  const noJobs = scored.filter(r => !(r.jobs && r.jobs.length));

  // 学生选「只看和专业对口的」→ 方向不成立的不进推荐榜，直接压到放弃
  const strict = String(prof.major_fit || "").indexOf("只看") === 0;
  const gated = strict ? withJobs.filter(r => r.calc.dirPart < 0.5) : [];
  const pool = strict ? withJobs.filter(r => r.calc.dirPart >= 0.5) : withJobs;

  pool.forEach((r, i) => {
    const n = pool.length;
    r.tier = i < Math.max(1, Math.round(n * 0.3)) ? "优先去"
           : i < Math.max(2, Math.round(n * 0.7)) ? "可以看" : "建议放弃";
  });
  gated.forEach(r => {
    r.tier = "建议放弃";
    r.line = "专业方向不对口 · " + r.line;
    r.misses.push("方向（你选了「只看专业对口的」，这家不进推荐榜）");
  });
  noJobs.forEach(r => { r.tier = "资料不全"; });

  return pool.concat(gated).concat(noJobs);
}

const esc2 = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

const TIER_META = [
  { key: "优先去",   cls: "t1", note: "先走这几家" },
  { key: "可以看",   cls: "t2", note: "有余力再去" },
  { key: "建议放弃", cls: "t3", note: "别把时间花在这" },
  { key: "资料不全", cls: "t4", note: "未查到岗位，先补数据" }
];

const f1 = n => Number(n || 0).toFixed(1);
const f2 = n => Number(n || 0).toFixed(2);

function companyInfoLine(r){
  const jobs = r.jobs || [];
  return [
    "城市：" + (r.city || "未知"),
    "性质：" + (r.nature || "未知"),
    "行业：" + (r.industry || "未知"),
    jobs.length ? "岗位：" + jobs.length + " 个" : "岗位：未查到",
    "来源：" + (r.jobSource || "未知")
  ].join(" · ");
}

function renderCalc(r){
  const c = r.calc;
  const baseLine = "城市 40×" + f2(c.cityPart) + "=" + f1(40 * c.cityPart)
    + " + 方向 35×" + f2(c.dirPart) + "=" + f1(35 * c.dirPart)
    + " + 紧急度 25×" + f2(c.urgPart) + "=" + f1(25 * c.urgPart)
    + " → 基础分 " + f1(c.base);
  const prefParts = DIM_KEYS.map(k =>
    DIM_LABEL[k] + " " + c.weights[k] + "×" + f2(c.dims[k]) + "=" + f1(c.weights[k] * c.dims[k])
  );
  const prefLine = "偏好：" + prefParts.join(" + ")
    + " = " + f1(c.preferencePoints)
    + " → 系数 1 + " + f1(c.preferencePoints) + "/100 = " + f2(c.factor);
  const bonusLine = c.bonus
    ? "加分：" + c.bonusReasons.join("；") + " → +" + c.bonus
    : "加分：无";

  const dimLines = DIM_KEYS.map(k =>
    DIM_LABEL[k] + "：" + c.dimNotes[k] + "（权重 " + c.weights[k] + "）"
  ).join("；");

  return '<div class="calc">'
    + '<div><b>分数怎么算：</b>' + esc2(baseLine)
    + "；× 偏好系数 " + f2(c.factor)
    + (c.bonus ? " + 加分 " + c.bonus : "")
    + " = <b>" + f1(r.score) + "</b></div>"
    + '<div class="calc-sub">' + esc2(prefLine) + "。" + esc2(bonusLine) + "。</div>"
    + '<div class="calc-sub">' + esc2("城市：" + c.cityNote + "；方向：" + c.dirNote + "；紧急度：" + c.urgNote) + "。</div>"
    + (c.uncollected ? '<div class="calc-sub">没有填写偏好，六项按等权（各 16）计算。</div>' : "")
    + '<details><summary>每一项匹配度怎么来的</summary>'
    + '<div class="calc-sub">' + esc2(dimLines) + "。</div>"
    + '</details>'
    + '</div>';
}

function renderJobs(r){
  const jobs = r.jobs || [];
  if (!jobs.length) {
    return '<div class="jobbox job-empty">未查到岗位：以上分数只用企业城市、性质、方向和偏好计算，没有岗位信息加成。'
      + '<button class="jobfill" data-name="' + esc2(r.name) + '">补岗位</button></div>';
  }
  const body = jobs.map(j => {
    const tags = [];
    if (j.urgency && j.urgency !== "未知") tags.push("紧急度 " + j.urgency);
    if (j.headcount !== "未知" && j.headcount != null) tags.push("招聘 " + j.headcount + " 人");
    if (j.salary && j.salary !== "未知") tags.push(j.salary);
    if (j.group && j.group !== "未知") tags.push(j.group);
    return '<div class="job"><div class="job-h"><b>' + esc2(j.title || "未知")
      + '</b><span>' + esc2(tags.join(" · ")) + "</span></div>"
      + (j.requirements && j.requirements !== "未知"
          ? '<div class="job-req">' + esc2(j.requirements) + "</div>" : "")
      + "</div>";
  }).join("");
  return '<details class="jobbox"><summary>岗位与要求（' + jobs.length + " 个）</summary>"
    + body + "</details>";
}

window.__mountMatch = function(doc){
  if (document.getElementById("matchbox")) return;      // 只挂一次

  const wrap = document.createElement("div");
  wrap.id = "matchbox";
  wrap.innerHTML =
    '<div class="mb-card">'
    + '<h3>接下来：导入参会企业名单</h3>'
    + '<p class="mb-sub">上传名单文件、贴一个网址，或直接粘贴。都可以。</p>'
    + '<div class="mb-inputs">'
    +   '<label class="mb-plus" for="mfiles" title="上传名单文件">+</label>'
    +   '<input id="mfiles" type="file" accept=".txt,.csv,.md,.text,.xlsx,.xls" multiple hidden>'
    +   '<input id="murl" type="url" placeholder="或者贴一个网址（招聘公告 / 名单页）">'
    + '</div>'
    + '<div id="mfilelist"></div>'
    + '<textarea id="mnames" placeholder="也可以直接粘贴，一行一家"></textarea>'
    + '<div class="mb-row">'
    +   '<button type="button" class="mb-ghost" id="mfill">用内置名单（' + BUILTIN_NAMES.length + ' 家）</button>'
    +   '<button type="button" class="mb-ghost" id="mlookup">搜索</button>'
    +   '<button type="button" class="mb-view" id="mcontents" disabled>查看搜索内容</button>'
    +   '<button type="button" class="mb-go" id="mgo">开始匹配</button>'
    + '</div>'
    + '<div class="note" id="mhint" style="margin-top:8px">'
    +   '搜索：对名单里每一家企业，先从本地数据库取已有档案，再联网查找校招公告补全岗位与画像。'
    +   '下方会实时显示进度。没抓到岗位的企业也保留，按企业资料参与匹配。'
    + '</div>'
    + '<div id="mthink"></div>'
    + '<div class="note" id="mstat"></div>'
    + '</div>'
    + '<dialog id="mcontentsdialog" aria-labelledby="mcontents-title">'
    +   '<div class="mc-head"><div><h3 id="mcontents-title">本次搜索内容</h3>'
    +     '<p>这里显示本次实际参与搜索的全部企业，不截断结果。</p></div>'
    +     '<button type="button" class="mc-close" id="mcontents-close" aria-label="关闭">×</button></div>'
    +   '<div class="mc-body" id="mcontents-body"></div>'
    + '</dialog>'
    + '<div id="mres"></div>';

  const chat = document.getElementById("chat");
  (chat || document.body).appendChild(wrap);

  const stat = wrap.querySelector("#mstat");
  const res = wrap.querySelector("#mres");
  const box = wrap.querySelector("#mnames");
  const fileList = wrap.querySelector("#mfilelist");
  const contentsBtn = wrap.querySelector("#mcontents");
  const contentsDialog = wrap.querySelector("#mcontentsdialog");
  const contentsBody = wrap.querySelector("#mcontents-body");
  let lastSearchReport = null;

  // 手动补岗位：资料不全的企业可以贴招聘页链接或岗位文本
  res.addEventListener("click", async ev => {
    const btn = ev.target.closest(".jobfill");
    if (!btn) return;
    const name = btn.dataset.name || "";
    if (!name) return;
    const raw = prompt("粘贴「" + name + "」的招聘页链接，或直接粘贴岗位列表：");
    if (!raw || !raw.trim()) return;
    const isUrl = /^https?:\/\//i.test(raw.trim());
    btn.disabled = true;
    btn.textContent = "补录中…";
    try {
      const r = await fetch("/api/jobs/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(isUrl ? { name: name, url: raw.trim() }
                                   : { name: name, text: raw })
      });
      const d = await r.json();
      if (!d.ok) {
        alert(d.error || "补录失败");
        btn.disabled = false;
        btn.textContent = "补岗位";
        return;
      }
      const idx = POOL.findIndex(c => c.name === name);
      if (idx >= 0) POOL[idx] = d.company; else POOL.push(d.company);
      try { localStorage.setItem("companies", JSON.stringify(POOL)); } catch (e) {}
      wrap.querySelector("#mgo").click();
    } catch (e) {
      alert("补录失败：本地服务可能没起来");
      btn.disabled = false;
      btn.textContent = "补岗位";
    }
  });

  function resultMap(report){
    return new Map([...(report.companies || []), ...(report.missed || [])]
      .map(c => [c.name, c]));
  }

  function renderSearchContents(){
    const report = lastSearchReport;
    if (!report) {
      contentsBody.innerHTML = '<div class="mc-empty">还没有搜索记录。先点「搜索」。</div>';
      return;
    }
    const resultByName = resultMap(report);
    const completed = report.completed || 0;
    const jobsCount = (report.companies || []).length;
    const missedCount = (report.missed || []).length;
    const excluded = report.excludedNames || [];
    const items = (report.candidates || []).map((name, index) => {
      const c = resultByName.get(name);
      let badge = "待搜索", badgeClass = "";
      let meta = "", jobs = "";
      if (c) {
        const jobList = c.jobs || [];
        if (jobList.length) {
          badge = "已补全"; badgeClass = "ok";
          jobs = "岗位：" + jobList.map(j => j.title).filter(Boolean).join("、");
        } else {
          badge = "未查到岗位"; badgeClass = "warn";
          jobs = "未抓到可用招聘页，匹配时保持未知。";
        }
        meta = [c.city && c.city !== "未知" ? "城市：" + c.city : "",
                c.industry && c.industry !== "未知" ? "行业：" + c.industry : "",
                c.nature && c.nature !== "未知" ? "性质：" + c.nature : ""]
          .filter(Boolean).join(" · ");
        if (!meta) meta = "企业画像字段均为未知";
      } else if (report.failed && completed <= index) {
        badge = "搜索中断"; badgeClass = "warn";
        meta = report.error || "本地服务未返回结果";
      } else if (completed > index) {
        badge = "等待返回"; badgeClass = "warn";
      }
      return '<div class="mc-item">'
        + '<div class="mc-item-head"><b>' + (index + 1) + '. ' + esc2(name) + '</b>'
        + '<span class="mc-badge ' + badgeClass + '">' + badge + '</span></div>'
        + (meta ? '<div class="mc-meta">' + esc2(meta) + '</div>' : "")
        + (jobs ? '<div class="mc-jobs">' + esc2(jobs) + '</div>' : "")
        + '</div>';
    }).join("");
    const candidateSection = items
      ? '<div class="mc-section"><h4>搜索候选（' + report.candidates.length + ' 家）</h4>'
        + '<div class="mc-list">' + items + '</div></div>'
      : '<div class="mc-section"><h4>搜索候选</h4><div class="mc-empty">没有候选企业。</div></div>';
    const excludedSection = excluded.length
      ? '<div class="mc-section"><h4>初筛已排除（' + excluded.length + ' 家）</h4>'
        + '<div class="mc-list">' + excluded.map(n =>
            '<div class="mc-item"><div class="mc-item-head"><b>' + esc2(n) + '</b>'
            + '<span class="mc-badge warn">初筛排除</span></div></div>').join("") + '</div></div>'
      : "";
    const modeText = "完整搜索";
    contentsBody.innerHTML =
      '<div class="mc-sum">'
      + '<span class="mc-stat">模式 <b>' + modeText + '</b></span>'
      + '<span class="mc-stat">导入 <b>' + report.inputCount + '</b> 家</span>'
      + '<span class="mc-stat">候选 <b>' + report.candidates.length + '</b> 家</span>'
      + '<span class="mc-stat">已处理 <b>' + completed + '</b> 家</span>'
      + '<span class="mc-stat">补全 <b>' + jobsCount + '</b> 家</span>'
      + '<span class="mc-stat">未查到岗位 <b>' + missedCount + '</b> 家</span>'
      + (excluded.length ? '<span class="mc-stat">初筛排除 <b>' + excluded.length + '</b> 家</span>' : "")
      + '</div>'
      + candidateSection + excludedSection
      + (report.finished ? "" : '<div class="mc-empty">搜索仍在进行，重新点击入口可查看最新进度。</div>');
  }

  function updateSearchReport(report){
    lastSearchReport = report;
    contentsBtn.disabled = false;
    contentsBtn.textContent = "查看搜索内容（" + report.candidates.length + "）";
    if (contentsDialog.open) renderSearchContents();
  }

  contentsBtn.addEventListener("click", () => {
    renderSearchContents();
    if (typeof contentsDialog.showModal === "function") contentsDialog.showModal();
    else contentsDialog.setAttribute("open", "");
  });
  wrap.querySelector("#mcontents-close").addEventListener("click", () => contentsDialog.close());
  contentsDialog.addEventListener("click", ev => {
    if (ev.target === contentsDialog) contentsDialog.close();
  });

  function pickNames(text){
    return String(text || "").split(/[\n\r,，、;；\t]+/)
      .map(s => s.replace(/^\s*\d+[\s.、)）]*/, "").replace(/["']/g, "").trim())
      .filter(s => s.length >= 4 && s.length <= 40 && /[\u4e00-\u9fa5A-Za-z]/.test(s))
      .filter(s => !/^(序号|企业名称|单位名称|公司名称|展位号|岗位|备注)$/.test(s))
      .filter((s, i, arr) => arr.indexOf(s) === i);
  }

  function addToBox(list){
    const have = new Set(pickNames(box.value));
    const add = list.filter(n => !have.has(n));
    box.value = (box.value.trim() ? box.value.trim() + "\n" : "") + add.join("\n");
    return add.length;
  }

  wrap.querySelector("#mfiles").addEventListener("change", ev => {
    const files = Array.from(ev.target.files || []);
    if (!files.length) return;

    files.forEach(f => {
      const isText = /\.(txt|csv|md|text)$/i.test(f.name);
      const isXlsx = /\.(xlsx|xlsm)$/i.test(f.name);
      const isOldXls = /\.xls$/i.test(f.name);

      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = '<b>' + esc2(f.name) + '</b>';
      fileList.appendChild(chip);

      const fail = msg => {
        chip.style.background = "#fdecec";
        chip.style.color = "#b4453f";
        chip.innerHTML = '<b>' + esc2(f.name) + '</b>（' + esc2(msg) + '）';
      };

      if (isOldXls) { fail("请另存为 xlsx 或 csv"); return; }

      if (isXlsx) {
        chip.innerHTML = '<b>' + esc2(f.name) + '</b> 解析中…';
        fetch("/api/names", { method: "POST", headers: { "X-Filename": f.name }, body: f })
          .then(r => r.json())
          .then(d => {
            if (!d.ok) { fail(d.error || "解析失败"); return; }
            const n = addToBox(d.names || []);
            chip.innerHTML = '<b>' + esc2(f.name) + '</b> +' + n;
            stat.textContent = "已从 Excel 读到 " + pickNames(box.value).length + " 家。";
          })
          .catch(() => fail("本地服务没起来"));
        return;
      }

      if (!isText) { fail("只读 txt / csv / md / xlsx"); return; }

      const reader = new FileReader();
      reader.onload = () => {
        const n = addToBox(pickNames(reader.result));
        chip.innerHTML = '<b>' + esc2(f.name) + '</b> +' + n;
        stat.textContent = "已从文件读到 " + pickNames(box.value).length + " 家。";
      };
      reader.readAsText(f, "utf-8");
    });
    ev.target.value = "";
  });

  wrap.querySelector("#mfill").addEventListener("click", () => {
    box.value = BUILTIN_NAMES.join("\n");
    stat.textContent = "已放入内置名单 " + BUILTIN_NAMES.length + " 家。";
  });


  wrap.querySelector("#mlookup").addEventListener("click", async () => {
    const btn = wrap.querySelector("#mlookup");
    const raw = box.value.trim();
    const all = raw ? pickNames(raw) : BUILTIN_NAMES.slice();
    if (!all.length) { stat.textContent = "先导入或粘贴企业名单。"; return; }

    const CHUNK = 8;
    let done = 0, gotTotal = 0, missTotal = 0, addedTotal = 0;
    const started = Date.now();
    const report = {
      mode: "full", inputCount: all.length, candidates: all.slice(),
      excludedNames: [], companies: [], missed: [],
      completed: 0, finished: false, failed: false, error: ""
    };
    updateSearchReport(report);

    btn.disabled = true;
    btn.textContent = "搜索中…";
    stat.innerHTML = "正在从本地数据库读取企业名单…";

    for (let i = 0; i < all.length; i += CHUNK) {
      const batch = all.slice(i, i + CHUNK);
      const eta = done ? Math.round((Date.now() - started) / done * (all.length - done) / 1000) : "?";
      stat.innerHTML = "正在查找企业档案与校招公告（<b>" + done + " / " + all.length + "</b>）…"
        + " 已补到 " + gotTotal + " 家 · 约剩 " + eta + " 秒";
      try {
        const r = await fetch("/api/lookup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ names: batch })
        });
        const data = await r.json();
        const got = data.companies || [];
        const missed = data.missed || [];
        gotTotal += got.length;
        missTotal += missed.length;
        report.companies.push(...got);
        report.missed.push(...missed);
        report.completed = Math.min(all.length, i + batch.length);
        updateSearchReport(report);

        // 没抓到岗位的企业也放进匹配池，避免 43 家初筛最后只剩有岗位的 10 家。
        const incoming = [...got, ...missed];
        const byName = new Map();
        incoming.forEach(c => {
          const prev = byName.get(c.name);
          if (!prev || ((c.jobs || []).length > (prev.jobs || []).length)) byName.set(c.name, c);
        });
        POOL = POOL.map(c => byName.get(c.name) || c);
        const known = new Set(POOL.map(c => c.name));
        const fresh = [...byName.values()].filter(c => !known.has(c.name));
        POOL = POOL.concat(fresh);
        addedTotal += fresh.length;
      } catch (e) {
        report.failed = true;
        const firstFail = (i === 0 && gotTotal === 0);
        report.error = firstFail ? "连不上本地服务 127.0.0.1:8102（第一批就失败）" : "中途失败，本地服务可能断了";
        report.finished = true;
        updateSearchReport(report);
        if (firstFail) {
          stat.innerHTML = "⚠️ 连不上本地服务 <b>127.0.0.1:8102</b>（联网搜索必须连它）。<br>"
            + "请先在 ent_tools 目录运行 <code>python -X utf8 fair_server.py</code>，"
            + "再用 <b>http://127.0.0.1:8102/fair.html</b> 打开本页重试。<br>"
            + "（走「填完画像直接出报告」的对话流程可离线看结果，那份用的是内置数据。）";
          btn.disabled = false; btn.textContent = "重试";
        } else {
          stat.innerHTML = "中途失败：已补到 " + gotTotal + " 家。点「继续搜索」接着补。";
          btn.disabled = false; btn.textContent = "继续搜索";
        }
        return;
      }
      done = Math.min(all.length, i + CHUNK);
    }
    stat.innerHTML = "正在把结果写回本地缓存…";
    report.finished = true;
    updateSearchReport(report);

    const secs = Math.round((Date.now() - started) / 1000);
    try { localStorage.setItem("companies", JSON.stringify(POOL)); } catch (e) {}
    stat.innerHTML = "搜索完成：名单 <b>" + all.length + "</b> 家 · 补到岗位 <b>"
      + gotTotal + "</b> 家 · 没抓到 <b>" + missTotal + "</b> 家（仍保留，按企业资料参与匹配）· 耗时 "
      + secs + " 秒<br>现在匹配池里共 <b>" + POOL.length + "</b> 家企业。";
    btn.textContent = "重新搜索";
    btn.disabled = false;
  });

  wrap.querySelector("#mgo").addEventListener("click", () => {
    const urlInput = wrap.querySelector("#murl");
    const url = (urlInput.value || "").trim();
    const raw = box.value.trim();
    const names = raw ? pickNames(raw) : BUILTIN_NAMES.slice();

    const prof = profileFromDoc(doc);

    if (!POOL.length) {
      stat.innerHTML = "还没有企业数据。先点上面的「联网补全企业画像」，"
        + "抓到的企业会自动加进来。";
      return;
    }

    const rows = match(POOL, prof);

    const covered = new Set(POOL.map(c => c.name));
    const pending = names.filter(n => !covered.has(n)).length;

    res.innerHTML = TIER_META.map(t => {
      const list = rows.filter(r => r.tier === t.key);
      if (!list.length) return "";
      return '<div class="tier ' + t.cls + '">'
        + '<div class="tier-h">' + t.key + ' ' + list.length + ' 家 <em>' + t.note + '</em></div>'
        + list.map(r =>
            '<div class="row">'
            + '<div class="rowhead">'
            +   '<div class="nm"><b>' + esc2(r.name) + '</b>'
            +     '<span class="meta">' + esc2(companyInfoLine(r)) + '</span>'
            +     '<span class="hits">命中：' + esc2(r.hitLine) + '</span>'
            +     (r.missLine ? '<span class="misses">没中：' + esc2(r.missLine) + '</span>' : '')
            +   '</div>'
            +   '<div class="sc">' + r.score.toFixed(1) + '<em>分</em></div>'
            + '</div>'
            + renderCalc(r)
            + renderJobs(r)
            + '</div>').join("")
        + '</div>';
    }).join("");

    const w = weightsOf(prof);
    const wline = DIM_KEYS.filter(k => w.vals[k] > 0)
      .map(k => DIM_LABEL[k] + w.vals[k])
      .join(" · ");

    let extra = "";
    if (url) {
      extra = "<br>网址 <b>" + esc2(url) + "</b>：离线演示不联网，"
            + "正式版这里会去抓公告页并抽取岗位；当前用名单里的企业匹配。";
    }

    stat.innerHTML = "名单 " + names.length + " 家 · "
      + "本次能匹配 " + POOL.length + " 家（有企业画像的）"
      + (pending ? " · 另有 <b>" + pending + "</b> 家尚未采集企业画像" : "")
      + "<br>加权：" + esc2(wline || "未填偏好，按等权")
      + "<br>分数 = 基础分（城市 40 + 方向 35 + 紧急度 25）× 偏好系数（1 + 六项偏好加权/100）+ 加分（企业类型 5 / 家乡 8），最后四舍五入到 1 位小数。"
      + "<br>每家下面会写「命中 / 没中」：城市、方向、紧急度、六项偏好、企业类型、家乡。"
      + "<br>分档：有岗位的企业按分数从高到低，前 30% 为「优先去」，30%–70% 为「可以看」，其余「建议放弃」。"
      + "<br>未查到岗位的企业单独放在「资料不全」，不占上面三档的名额。"
      + extra;

    wrap.querySelector("#mgo").textContent = "重新匹配";
    res.scrollIntoView({ behavior: "smooth", block: "start" });
  });
};
</script>
"""

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
print("生成", out)
print("  改动：" + "、".join(PATCH_LOG))
print("  大小 %.1f KB；内置企业 %d 家；内置名单 %d 家" %
      (out.stat().st_size / 1024, COMPANIES_JSON.count('"id"'), len(NAMES)))
