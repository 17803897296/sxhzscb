
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
  if (a.hard_filter) traits.push("硬约束：" + a.hard_filter + "（不满足的企业直接剔除）");
  traits.push("教培等无专业要求企业：" + (a.accept_no_major === true ? "可考虑" : "不考虑（已剔除）"));

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
    /* 身份字段：简历解析字段优先；没传简历时用对话里填的 identity
       （之前只取 r.fields，「直接聊」模式下对话填的姓名/学校/专业全丢，
       画像 major 被兜底成管科默认值 —— 2026-11-15 修复）。 */
    identity: (() => {
      const src = (r.fields && Object.fromEntries(
        Object.entries(r.fields).map(([k, v]) => [k, v.value || v]))) || {};
      const aid = a.identity || {};
      const out = {};
      ["name", "school", "major", "degree", "grade", "graduation", "phone", "email"]
        .forEach(k => { out[k] = src[k] || aid[k] || ""; });
      return out;
    })(),
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
      industry_rank: a.industry_rank || {},
      /* v12 硬约束：一句自由填空（如「只要南京」「只看互联网」），
         匹配端由大模型解析成结构化条件后硬过滤企业池，不满足者直接剔除。
         空串 = 不设限。 */
      hard_filter: a.hard_filter || "",
      /* v12 教培类接受度：false（默认）= 教培/无专业要求企业剔除；true 才纳入。 */
      accept_no_major: a.accept_no_major === true
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
      error: '离线双击模式无法解析简历；请用 统一入口首页（/） 打开本页，或选「没有简历，直接聊」' });
  }
  if (u.indexOf("/api/fair/profile") >= 0) {
    let body = {};
    try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) {}
    const doc = _buildDocument(body);
    const keepLocal = () => {
      try { localStorage.setItem("fair_profile", JSON.stringify(doc)); } catch (e) {}
    };
    keepLocal();
    setTimeout(() => {
      /* v12 分端（2026-11-17 用户拍板）：在线模式下名单导入在企业端、
         匹配排行在匹配端（/match.html），学生端只管画像——聊完给跳转卡片。
         离线双击模式没有匹配端页，保留原完整块（名单+匹配内联完成）。 */
      if (!FAIR_ONLINE) {
        if (window.__mountMatch) window.__mountMatch(doc);
        return;
      }
      const mb = document.createElement("div");
      mb.id = "matchbox";
      mb.innerHTML = '<div class="mb-card"><h3>画像已生成</h3>'
        + '<p class="mb-sub">企业名单与岗位在企业端导入；匹配结果排行在匹配端看。</p>'
        + '<div class="mb-row"><a class="mb-go" style="text-decoration:none;'
        + 'text-align:center;display:block" href="/match.html">去匹配端看排行 →</a></div></div>';
      const chat = document.getElementById("chat");
      (chat || document.body).appendChild(mb);
    }, 420);
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
