
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

/* 排序对象 = 当前名单。
   名单从哪来：用户自己录/上传的名单优先，没录就是内置名单（__NAMES__，即库里这批企业）。
   以前直接把 POOL 丢给 match()，而 POOL 是「内置企业 + 这次补到的」，
   于是录取名单以外的企业一直混在榜里——换一场双选会，学生看到的榜还掺着上一场的公司。
   现在按名单逐个取画像，取不到就给一条「尚未采集」占位，照样进「资料不全」层，
   不会被悄悄丢掉。 */
function rosterStub(name){
  return { id: "roster-" + name, name: name, city: "未知", province: "",
           nature: "未知", industry: "未知", jobs: [], jobSource: "未知",
           roster_stub: true };
}

function rosterPool(names){
  const byName = new Map();
  POOL.forEach(c => { if (c && c.name) byName.set(c.name, c); });
  const pool = [];
  names.forEach(n => {
    const c = byName.get(n);
    if (c) { pool.push(c); byName.delete(n); return; }
    pool.push(rosterStub(n));
  });
  return pool;
}

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

window.__mountMatch = function(doc, opts){
  if (document.getElementById("matchbox")) return;      // 只挂一次
  /* v12 分端：rosterOnly=true 只挂「导入参会企业名单」块（企业端用），
     不渲染开始匹配按钮和结果区——匹配排行归匹配端（/match.html）。
     缺省（学生端离线流程）保持原来的完整块。 */
  const rosterOnly = !!(opts && opts.rosterOnly);
  const wrap = document.createElement("div");
  wrap.id = "matchbox";
  wrap.innerHTML =
    '<div class="mb-card">'
    + '<h3>' + (rosterOnly ? "导入参会企业名单" : "接下来：导入参会企业名单") + '</h3>'
    + '<p class="mb-sub">' + (rosterOnly
        ? "上传名单文件、贴一个网址，或直接粘贴。搜索会补全企业档案与岗位。"
        : "上传名单文件、贴一个网址，或直接粘贴。都可以。") + '</p>'
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
    +   (rosterOnly ? '' : '<button type="button" class="mb-go" id="mgo">开始匹配</button>')
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
    + (rosterOnly ? '' : '<div id="mres"></div>');

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

  // 手动补岗位：资料不全的企业可以贴招聘页链接或岗位文本（结果区仅在匹配端渲染）
  if (res) res.addEventListener("click", async ev => {
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

  /* 企业名判定：与服务端 portal.looks_like_company 同口径。
     以前这里只要求「4~40 字、含中文」，于是 CSV 表头「所属行业」、岗位列
     「人事行政、财务」都被当成企业名塞进名单，检索时白跑一趟还显示「未找到」。 */
  const CO_RE = /公司|集团|银行|支行|分行|营业部|研究院|研究所|设计院|科学院|大学|学院|医院|中心|事务所|事业单位|出版社|报社|杂志社|合作社|(?:财政|税务|市场监督|生态环境|人力资源|统计|气象|地震|邮政|铁路|海关|药监|烟草)局|(?:机械|印刷|纺织|食品|化工|电子|建材|造纸|钢铁|冶炼|加工|制造)厂/;
  const HEAD_RE = /^(序号|编号|企业名称|公司名称|单位名称|企业|单位|公司名|所属行业|行业|单位性质|性质|展位号|展位|招聘岗位数|岗位数|岗位方向|岗位|备注|说明|地址|联系人|联系电话|电话|人数|专业|学历|要求)$/;

  function pickNames(text, strict){
    return String(text || "").split(/[\n\r,，、;；\t]+/)
      .map(s => s.replace(/^\s*\d+[\s.、)）]*/, "").replace(/["']/g, "").trim())
      .filter(s => s.length >= 4 && s.length <= 40 && /[\u4e00-\u9fa5A-Za-z]/.test(s))
      .filter(s => !HEAD_RE.test(s))
      .filter(s => !/序号|岗位|专业|人数|展位|备注|要求|所属行业|单位性质/.test(s))
      .filter(s => (strict ? CO_RE.test(s) : true))
      .filter((s, i, arr) => arr.indexOf(s) === i);
  }

  /* 先按严格口径（必须有 公司/集团/银行… 这类后缀）解析；一个都没有才退回
     宽松口径——粘贴的短名单（「华为」「小米」）还能用，CSV 表头/备注列又进不来。 */
  function pickNamesSmart(text){
    const strict = pickNames(text, true);
    return strict.length ? strict : pickNames(text, false);
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
        /* 文件名放查询串，不放请求头：头只能用 ISO-8859-1，中文文件名会让
           fetch 直接抛错（页面上显示「本地服务没起来」，请求根本没发出去）。 */
        fetch("/api/names?f=" + encodeURIComponent(f.name), { method: "POST", body: f })
          .then(r => r.json())
          .then(d => {
            if (!d.ok) { fail(d.error || "解析失败"); return; }
            const n = addToBox(d.names || []);
            if (!n) {                       // 解析成功但一个都没认出：别只说 +0
              fail((d.count ? "读到 " + d.count + " 条但都不像企业名" : "没认出企业名") +
                   "，检查是不是名称列不对，或直接粘贴名单");
              return;
            }
            chip.innerHTML = '<b>' + esc2(f.name) + '</b> +' + n;
            stat.textContent = "已从 Excel 读到 " + pickNamesSmart(box.value).length + " 家。";
          })
          .catch(() => fail("本地服务没起来"));
        return;
      }

      if (!isText) { fail("只读 txt / csv / md / xlsx"); return; }

      /* 编码：Excel「另存为 CSV」默认是 GBK，按 utf-8 硬读会整片变问号，
         名单直接归零。先按 utf-8 解，出现替换字符就退回 GBK 再解一次。 */
      f.arrayBuffer().then(buf => {
        let text = "";
        try {
          text = new TextDecoder("utf-8").decode(buf);
          if (text.indexOf("\uFFFD") >= 0) {
            try { text = new TextDecoder("gbk").decode(buf); } catch (e) {}
          }
        } catch (e) {
          fail("这个文件读不出来");
          return;
        }
        const n = addToBox(pickNamesSmart(text));
        if (!n) {
          fail("没认出企业名，检查是不是名称列不对，或直接粘贴名单");
          return;
        }
        chip.innerHTML = '<b>' + esc2(f.name) + '</b> +' + n;
        stat.textContent = "已从文件读到 " + pickNamesSmart(box.value).length + " 家。";
      }).catch(() => fail("这个文件读不出来"));
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
    const all = raw ? pickNamesSmart(raw) : BUILTIN_NAMES.slice();
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
        report.error = firstFail ? "连不上本地服务 本地服务（第一批就失败）" : "中途失败，本地服务可能断了";
        report.finished = true;
        updateSearchReport(report);
        if (firstFail) {
          stat.innerHTML = "⚠️ 连不上本地服务 <b>本地服务</b>（联网搜索必须连它）。<br>"
            + "请先在 ent_tools 目录运行 <code>python -X utf8 fair_server.py</code>，"
            + "再用 <b>统一入口首页（/）</b> 打开本页重试。<br>"
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
      + secs + " 秒<br>现在匹配池里共 <b>" + POOL.length + "</b> 家企业。"
      + (rosterOnly
          ? '<br>名单已就绪，<a href="/match.html"><b>去匹配端看排行 →</b></a>'
          : "");
    btn.textContent = "重新搜索";
    btn.disabled = false;
  });

  const mgoBtn = wrap.querySelector("#mgo");
  if (mgoBtn) mgoBtn.addEventListener("click", () => {
    const urlInput = wrap.querySelector("#murl");
    const url = (urlInput.value || "").trim();
    const raw = box.value.trim();
    const names = raw ? pickNamesSmart(raw) : BUILTIN_NAMES.slice();

    const prof = profileFromDoc(doc);

    if (!names.length) {
      stat.innerHTML = "名单是空的。先上传名单文件或粘贴企业名；"
        + "不上传名单就按内置的 <b>" + BUILTIN_NAMES.length + "</b> 家排。";
      return;
    }

    /* 只排名单里的企业：名单里没采集到画像的，给一条占位进「资料不全」。 */
    const pool = rosterPool(names);
    const rows = match(pool, prof);

    const withProfile = pool.filter(c => !c.roster_stub).length;
    const pending = pool.length - withProfile;
    const fromBuiltin = !raw;

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

    stat.innerHTML = "排序范围：<b>" + (fromBuiltin
        ? "内置名单 " + BUILTIN_NAMES.length + " 家（没上传名单）"
        : "你录入的名单 " + names.length + " 家") + "</b>"
      + " · 其中 <b>" + withProfile + "</b> 家有企业画像"
      + (pending ? " · 另有 <b>" + pending + "</b> 家尚未采集，列在「资料不全」" : "")
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
