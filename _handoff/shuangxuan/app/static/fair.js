/* CareerSafari · 双选会画像采集端（对话式）
   一问一答：每题都能点选项或直接打字；能力/价值观/未知项用对话里的卡片收口。
   画像组装仍在 fair.py，前端只负责采集，保证前后端只有一套逻辑。 */

let Q = null;
const state = {
  answers: null,
  resume: null,
  resumeLLM: false,
  flow: [],
  runId: 0,
  stepIdx: -1,
  history: [],
  awaiting: null,      // {step, botEls, resolve}：当前等待回答的步骤
  awaitingEls: [],     // 当前步骤已渲染的元素（卡片也要能随「上一步」移除）
  multiSel: new Set(),
  generated: false,
  lastDoc: null,
  lastSaved: false,
  save: true,
};

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  try { return await r.json(); } catch (e) { return { ok: false, error: `请求失败（HTTP ${r.status}）` }; }
}

/* ---------------- 数据 ---------------- */

function blankAnswers() {
  return {
    target_stage: '', attained_degree: '', major: '', grade: '',
    direction_interest: '', region: '', timeline: '', dealbreaker: '', intern_goal: '',
    skills: [],
    riasec: { R: 0, I: 0, A: 0, S: 0, E: 0, C: 0 },
    mbti: '', mbti_caveat_ack: false,
    values: { salary: 0, stability: 0, growth: 0, autonomy: 0, impact: 0, balance: 0 },
    unknowns: [],
    identity: { name: '', school: '', major: '', degree: '', grade: '',
                graduation: '', phone: '', email: '' },
    fair: { event_name: '', event_date: '', event_city: '', target_stage: '', stage_detail: '',
            target_industries: [], target_company_types: [], on_site_goals: [],
            availability: '', resume_status: '', self_intro: '', home_city: '' },
  };
}

function syncAnswers() {
  const a = state.answers, id = a.identity;
  a.major = id.major || a.major;
  a.grade = id.grade || a.grade;
  a.attained_degree = id.degree || a.attained_degree;
  if (a.fair.stage_detail && !a.target_stage) a.target_stage = a.fair.target_stage;
}

function todayISO() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/* ---------------- 消息 ---------------- */

function scrollBottom() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}

function appendMsg(kind, html, cls) {
  const el = document.createElement('div');
  el.className = 'msg ' + kind + (cls ? ' ' + cls : '');
  el.innerHTML = `<div class="avatar">${kind === 'bot' ? 'C' : '我'}</div><div class="bubble">${html}</div>`;
  $('chat').appendChild(el);
  scrollBottom();
  return el;
}

async function say(html, opts) {
  const o = opts || {};
  const temp = document.createElement('div');
  temp.className = 'msg bot';
  temp.innerHTML = '<div class="avatar">C</div><div class="bubble typing"><i></i><i></i><i></i></div>';
  $('chat').appendChild(temp);
  scrollBottom();
  await sleep(o.fast ? 120 : 240);
  temp.remove();
  return appendMsg('bot', html, o.wide ? 'wide' : '');
}

function note(kind, html) {
  return `<div class="note ${kind}"><span class="ic">${{ ok: '✓', warn: '⚠', bad: '✕' }[kind] || 'i'}</span><div>${html}</div></div>`;
}

/* ---------------- 选项与输入 ---------------- */

function renderQuick(step) {
  const box = $('quick');
  box.innerHTML = '';
  const add = (label, onClick, cls) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip ' + (cls || '');
    b.textContent = label;
    b.onclick = onClick;
    box.appendChild(b);
    return b;
  };
  const valueOf = o => (typeof o === 'string' ? o : o.value);
  const labelOf = o => (typeof o === 'string' ? o : o.label);

  if (step.kind === 'single') {
    step.options.forEach(o => add(labelOf(o), () => answer({ value: valueOf(o), label: labelOf(o) })));
    if (step.optional) add('跳过', () => answer({ value: '', label: '跳过', skipped: true }), 'chip-skip');
  } else if (step.kind === 'multi') {
    step.options.forEach(o => {
      const b = add(labelOf(o), () => {
        if (state.multiSel.has(valueOf(o))) state.multiSel.delete(valueOf(o));
        else state.multiSel.add(valueOf(o));
        b.classList.toggle('on', state.multiSel.has(valueOf(o)));
      });
      if (state.multiSel.has(valueOf(o))) b.classList.add('on');
    });
    add('选好了', () => {
      const picked = step.options.filter(o => state.multiSel.has(valueOf(o)));
      answer({
        value: picked.map(valueOf),
        label: picked.length ? picked.map(labelOf).join('、') : '都不选',
        empty: picked.length === 0,
      });
    }, 'chip-done');
    if (step.optional) add('跳过', () => answer({ value: [], label: '跳过', skipped: true }), 'chip-skip');
  } else if (step.kind === 'text' && step.optional) {
    add('跳过', () => answer({ value: '', label: '跳过', skipped: true }), 'chip-skip');
  }
  $('hint').textContent = step.hint || '点下面的选项，或者直接打字。';
}

function enableInput(step) {
  const input = $('msg');
  input.disabled = false;
  input.value = '';
  input.placeholder = step.placeholder || '直接打字也可以，Enter 发送…';
  $('send').disabled = false;
  setTimeout(() => { try { input.focus({ preventScroll: true }); } catch (e) {} }, 60);
}

function disableComposer() {
  $('msg').disabled = true;
  $('msg').value = '';
  $('send').disabled = true;
  $('quick').innerHTML = '';
  $('hint').textContent = '';
  $('msg').placeholder = '';
}

function answer(res) {
  const a = state.awaiting;
  if (!a) return;
  state.awaiting = null;
  state.awaitingEls = [];
  disableComposer();
  a.resolve(res);
}

function waitAnswer(step, botEls) {
  return new Promise(resolve => {
    state.multiSel = new Set();
    state.awaiting = { step, botEls, resolve };
    state.awaitingEls = botEls;
    renderQuick(step);
    enableInput(step);
  });
}

function normText(s) {
  return String(s || '').toLowerCase().replace(/[\s，,。.、；;：:（）()「」『』]/g, '');
}

function matchSingle(step, text) {
  const t = normText(text);
  if (!t) return null;
  const alias = step.alias || {};
  for (const key of Object.keys(alias)) {
    if (t.includes(normText(key))) {
      const want = alias[key];
      return step.options.find(o => (typeof o === 'string' ? o : o.value) === want
        || (typeof o === 'string' ? o : o.label) === want) || null;
    }
  }
  return step.options.find(o => {
    const label = normText(typeof o === 'string' ? o : o.label);
    return label === t || label.includes(t) || t.includes(label);
  }) || null;
}

function sendTyped() {
  const a = state.awaiting;
  if (!a) return;
  const text = $('msg').value.trim();
  if (!text) return;
  const step = a.step;

  if (text === '跳过' && step.optional) {
    $('msg').value = '';
    answer({ value: step.kind === 'multi' ? [] : '', label: '跳过', skipped: true });
    return;
  }

  if (step.kind === 'text') {
    const parsed = step.parse ? step.parse(text) : { value: text };
    if (parsed && parsed.error) { say(parsed.error, { fast: true }); return; }
    $('msg').value = '';
    answer({ value: parsed.value, label: text });
    return;
  }

  if (step.kind === 'single') {
    // 只接受「整句就是选项或别名」的规范值；带修饰的长句原样保留（仅参考）
    if (step.allowCustom) {
      const t = normText(text);
      const exact = step.options.find(o => normText(typeof o === 'string' ? o : o.label) === t);
      const aliasKey = Object.keys(step.alias || {}).find(k => normText(k) === t);
      let value = '';
      if (exact) value = typeof exact === 'string' ? exact : exact.value;
      else if (aliasKey) value = step.alias[aliasKey];
      if (value) {
        $('msg').value = '';
        answer({ value, label: value });
        return;
      }
      const custom = text.slice(0, 60);
      $('msg').value = '';
      answer({ value: custom, label: custom, custom: true });
      return;
    }
    const hit = matchSingle(step, text);
    if (!hit) {
      say(`没太听清。可以点下面的选项，或者换个说法（例如「${esc((typeof step.options[0] === 'string' ? step.options[0] : step.options[0].label))}」）。`, { fast: true });
      return;
    }
    $('msg').value = '';
    answer({ value: typeof hit === 'string' ? hit : hit.value, label: typeof hit === 'string' ? hit : hit.label });
    return;
  }

  if (step.kind === 'multi') {
    if (/^(好了|选好了|完成|就这些|不用了)$/.test(text)) {
      const picked = step.options.filter(o => state.multiSel.has(typeof o === 'string' ? o : o.value));
      $('msg').value = '';
      answer({
        value: picked.map(o => (typeof o === 'string' ? o : o.value)),
        label: picked.length ? picked.map(o => (typeof o === 'string' ? o : o.label)).join('、') : '都不选',
        empty: picked.length === 0,
      });
      return;
    }
    let matched = 0;
    text.split(/[，,、/;；\s]+/).filter(Boolean).forEach(tok => {
      const hit = matchSingle(step, tok);
      if (hit) { state.multiSel.add(typeof hit === 'string' ? hit : hit.value); matched++; }
    });
    if (!matched) { say('没匹配到选项。可以点下面的多选项，选完点「选好了」。', { fast: true }); return; }
    $('msg').value = '';
    renderQuick(step);
    $('hint').textContent = '已加入，继续选或点「选好了」。';
  }
}

/* ---------------- 步骤定义 ---------------- */

const contactStep = {
  id: 'id-contact',
  kind: 'text',
  ask: '如果现场有企业想联系你，留个手机号或邮箱？（可跳过）',
  optional: true,
  placeholder: '手机号或邮箱，可跳过',
  hint: '只写进这份画像，不会外发。',
  parse(text) {
    const t = text.trim();
    if (t.includes('@')) {
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(t)) return { error: '邮箱格式看起来不太对，再试一次？' };
      return { value: { email: t, phone: '' } };
    }
    const digits = t.replace(/[\s-]/g, '');
    if (/^1[3-9]\d{9}$/.test(digits)) return { value: { phone: digits, email: '' } };
    return { error: '看起来不是手机号或邮箱。可以直接点「跳过」。' };
  },
  apply(res) {
    if (res.skipped) return;
    state.answers.identity.phone = res.value.phone || '';
    state.answers.identity.email = res.value.email || '';
  },
  reset() { state.answers.identity.phone = ''; state.answers.identity.email = ''; },
};

/* 没有简历时：一句自由填写，替代 6 个身份问题 */
const identityFreeTextStep = {
  id: 'id-free',
  kind: 'text',
  ask: '没有简历也没关系。学校 / 专业 / 年级，一句话说清就行（可跳过）。',
  optional: true,
  placeholder: '比如：南京大学 管理科学与工程 研一',
  hint: '只从这句话里拆学校、专业、年级；拆不出就留空，不猜。',
  parse(text) {
    const grade = (text.match(/(研[一二三]|大[一二三四五])/) || [])[0] || '';
    const rest = grade ? text.replace(grade, ' ').trim() : text.trim();
    const parts = rest.split(/[\/,，、;；|]+|\s+/).map(x => x.trim()).filter(Boolean);
    if (!parts.length && !grade) return { error: '没识别到内容，可以直接点「跳过」。' };
    return { value: { school: parts[0] || '', major: parts.slice(1).join(' ') || '', grade } };
  },
  apply(res) {
    if (res.skipped) return;
    const v = res.value || {};
    if (v.school) state.answers.identity.school = v.school;
    if (v.major) state.answers.identity.major = v.major;
    if (v.grade) state.answers.identity.grade = v.grade;
    syncAnswers();
  },
  reset() {
    state.answers.identity.school = '';
    state.answers.identity.major = '';
    state.answers.identity.grade = '';
    syncAnswers();
  },
};

function fairStep(id, ask, opts) {
  const o = opts || {};
  return {
    id: 'fair-' + id,
    kind: o.options ? 'single' : 'text',
    ask,
    options: o.options || [],
    optional: o.optional === true,
    allowCustom: o.allowCustom === true,
    alias: o.alias || {},
    placeholder: o.placeholder,
    hint: o.hint,
    apply(res) {
      if (res.skipped) return;
      if (o.after) o.after(res.value, state.answers);
    },
    reset() {
      if (o.reset) o.reset(state.answers);
    },
  };
}

/* ---------------- 卡片 ---------------- */

function makeCard(html, wire) {
  const el = appendMsg('bot', html, 'wide');
  const root = el.querySelector('.bubble');
  state.awaitingEls = [el];
  return new Promise(resolve => {
    const done = value => {
      el.classList.add('resolved');
      el.querySelectorAll('button,input,select,textarea').forEach(x => { try { x.disabled = true; } catch (e) {} });
      state.awaiting = null;
      resolve(value);
    };
    state.awaiting = { step: null, botEls: [el], resolve: () => {} };
    wire(root, done);
  });
}

async function startCard() {
  const html = `<div class="chat-card">
    <div class="cc-title">先说说，有没有带简历？</div>
    <div class="cc-sub">带上简历我会直接读出学校、专业、项目经历，只再问你 4 个问题；没有简历也能聊。</div>
    <div class="cc-actions">
      <button type="button" class="btn btn-p" data-act="upload">上传简历</button>
      <button type="button" class="btn btn-g" data-act="manual">没有简历，直接聊</button>
    </div>
    <input type="file" id="resume-file" accept=".docx,.pdf,.txt,.md,.markdown,.html,.htm" hidden>
    <label class="check slim"><input type="checkbox" id="use-llm">
      <span>允许把简历文字发送给模型做增强抽取（默认只用本地规则）</span></label>
    <div id="upload-msg" class="cc-sub"></div>
  </div>`;
  return makeCard(html, (root, done) => {
    const fileInput = root.querySelector('#resume-file');
    const useLLM = root.querySelector('#use-llm');
    root.querySelector('[data-act="upload"]').onclick = () => fileInput.click();
    root.querySelector('[data-act="manual"]').onclick = () => { state.resumeLLM = false; done('manual'); };
    fileInput.onchange = async () => {
      const file = fileInput.files[0];
      if (!file) return;
      const msg = root.querySelector('#upload-msg');
      if (file.size > Q.fair.max_resume_bytes) {
        msg.innerHTML = note('bad', `文件超过 ${Math.round(Q.fair.max_resume_bytes / 1024 / 1024)} MB 上限。`);
        return;
      }
      msg.innerHTML = '<span class="spin"></span> 正在本机解析简历…';
      const fd = new FormData();
      fd.append('file', file);
      let r;
      try {
        r = await (await fetch(`/api/fair/resume?use_llm=${useLLM.checked ? 'true' : 'false'}`,
          { method: 'POST', body: fd })).json();
      } catch (e) {
        msg.innerHTML = note('bad', '上传失败，请重试或直接聊。');
        return;
      }
      if (!r.ok) { msg.innerHTML = note('bad', esc(r.error || '解析失败')); return; }
      state.resumeLLM = useLLM.checked;
      state.resume = {
        uploaded: true, filename: r.filename, chars: r.chars, sha256: r.sha256,
        method: r.method, model: r.model, fields: r.fields, skills: r.skills || [],
        experiences: r.experiences || [], certificates: r.certificates || [],
        warnings: r.warnings || [], llm: r.llm || {},
      };
      prefillFromResume(r.fields || {});
      done('uploaded');
    };
  });
}

function prefillFromResume(fields) {
  const map = { name: 'name', school: 'school', major: 'major', degree: 'degree',
    grade: 'grade', graduation: 'graduation', phone: 'phone', email: 'email' };
  Object.keys(map).forEach(key => {
    const f = fields[key];
    if (f && f.value && !state.answers.identity[map[key]]) state.answers.identity[map[key]] = f.value;
  });
  syncAnswers();
}

async function resumeSummaryCard() {
  const r = state.resume;
  const labels = [['name', '姓名'], ['school', '学校'], ['major', '专业'], ['degree', '学历'],
    ['grade', '年级'], ['graduation', '毕业时间'], ['phone', '手机'], ['email', '邮箱']];
  const rows = labels.map(([key, label]) => {
    const f = (r.fields || {})[key] || {};
    const val = f.value ? esc(f.value) : '<span class="muted">未识别</span>';
    const tag = f.source === 'missing' ? '' : `<span class="tag info tiny">${f.source === 'resume_llm' ? '模型' : '规则'}</span>`;
    return `<div class="kv two"><span class="k">${label}</span><span class="v">${val}</span>${tag}</div>`;
  }).join('');
  const skills = (r.skills || []).map(s => `<span class="tag ok" title="${esc(s.evidence || '')}">${esc(s.name)}</span>`).join('');
  const exps = (r.experiences || []).map(x => `<li>${esc(x.text)}</li>`).join('');
  const warn = (r.warnings || []).length ? note('warn', r.warnings.map(esc).join('<br>')) : '';
  const html = `<div class="chat-card">
    <div class="cc-title">从简历里抽到这些</div>
    <div class="cc-sub">抽不到的留空，不会猜。接着我只会问还缺的项。</div>
    ${rows}
    ${skills ? `<div class="sec-t">能力关键词</div><div class="chips">${skills}</div>` : ''}
    ${exps ? `<div class="sec-t">经历</div><ul class="explist">${exps}</ul>` : ''}
    ${warn}
    <div class="cc-actions">
      <button type="button" class="btn btn-p" data-act="ok">都对，继续</button>
      <button type="button" class="btn btn-g" data-act="edit">我要改一下</button>
    </div>
  </div>`;
  return makeCard(html, (root, done) => {
    root.querySelector('[data-act="ok"]').onclick = () => done('ok');
    root.querySelector('[data-act="edit"]').onclick = () => done('edit');
  });
}

function applyResumeSkills() {
  if (!state.resume) return;
  const src = (state.resume.skills || []).filter(x => x && x.name).slice(0, 8);
  if (!src.length) return;
  const dir = state.answers.direction_interest;
  const code = (Q.major_directions.find(d => d.name === dir) || {}).code || 'MSE-BIGDATA';
  const specific = new Set(Q.job_specific_skills[code] || []);
  state.answers.skills = src.map(x => ({
    name: x.name,
    column: specific.has(x.name) ? 'job_specific' : 'transferable',
    self_rating: null,
    evidence_text: String(x.evidence || '').slice(0, 300),
    evidence_source: 'self_report',
    artifact_reference: '',
  }));
}

async function flagshipCard() {
  const exps = (state.resume && state.resume.experiences) || [];
  const items = exps.slice(0, 5).map((x, i) =>
    `<button type="button" class="pick" data-i="${i}"><b>${i + 1}.</b> ${esc(x.text)}</button>`).join('');
  const html = `<div class="chat-card">
    <div class="cc-title">哪段经历最想让我们看？</div>
    <div class="cc-sub">点一段就行，后段匹配会优先看它；不选就按顺序取第一段。</div>
    <div class="pick-list" id="pick-list">${items}</div>
    <div class="cc-actions">
      <button type="button" class="btn btn-p" data-act="pick" disabled>就这段</button>
      <button type="button" class="btn btn-g" data-act="skip">跳过</button>
    </div>
  </div>`;
  return makeCard(html, (root, done) => {
    let selected = -1;
    root.querySelectorAll('.pick').forEach(btn => {
      btn.onclick = () => {
        selected = +btn.dataset.i;
        root.querySelectorAll('.pick').forEach(b => b.classList.toggle('on', b === btn));
        root.querySelector('[data-act="pick"]').disabled = false;
      };
    });
    root.querySelector('[data-act="pick"]').onclick = () => done({ text: exps[selected] ? exps[selected].text : '' });
    root.querySelector('[data-act="skip"]').onclick = () => done({ text: '' });
  });
}

async function identityEditCard() {
  const id = state.answers.identity;
  const html = `<div class="chat-card">
    <div class="cc-title">改哪几项？</div>
    <div class="grid g2" style="margin-top:8px">
      <div class="f"><label>姓名</label><input data-k="name" type="text" maxlength="20" value="${esc(id.name)}"></div>
      <div class="f"><label>学校</label><input data-k="school" type="text" maxlength="60" value="${esc(id.school)}"></div>
      <div class="f"><label>专业</label><input data-k="major" type="text" maxlength="40" value="${esc(id.major)}"></div>
      <div class="f"><label>学历</label><select data-k="degree"><option value="">未填</option>${Q.fair.degrees.map(d => `<option ${id.degree === d ? 'selected' : ''}>${esc(d)}</option>`).join('')}</select></div>
      <div class="f"><label>年级</label><select data-k="grade"><option value="">未填</option>${['研一', '研二', '研三', '大四', '大三', '大二', '大一'].map(d => `<option ${id.grade === d ? 'selected' : ''}>${esc(d)}</option>`).join('')}</select></div>
      <div class="f"><label>毕业时间</label><input data-k="graduation" type="text" maxlength="30" value="${esc(id.graduation)}"></div>
      <div class="f"><label>手机</label><input data-k="phone" type="text" maxlength="20" value="${esc(id.phone)}"></div>
      <div class="f"><label>邮箱</label><input data-k="email" type="text" maxlength="60" value="${esc(id.email)}"></div>
    </div>
    <div class="cc-actions"><button type="button" class="btn btn-p" data-act="save">保存</button></div>
  </div>`;
  return makeCard(html, (root, done) => {
    root.querySelector('[data-act="save"]').onclick = () => {
      root.querySelectorAll('[data-k]').forEach(el => { state.answers.identity[el.dataset.k] = el.value.trim(); });
      syncAnswers();
      done('saved');
    };
  });
}

function equalWeights(dims) {
  const out = {};
  if (!dims.length) return out;
  const base = Math.floor(100 / dims.length);
  let rem = 100 - base * dims.length;
  dims.forEach((d, i) => { out[d] = base + (i < rem ? 1 : 0); });
  return out;
}

function normalizeWeights(weights) {
  const keys = Object.keys(weights).filter(k => weights[k] > 0);
  const full = { salary: 0, stability: 0, growth: 0, autonomy: 0, impact: 0, balance: 0 };
  const total = keys.reduce((a, k) => a + weights[k], 0);
  if (!total) return full;
  const scaled = {};
  keys.forEach(k => { scaled[k] = weights[k] * 100 / total; full[k] = Math.floor(scaled[k]); });
  let rem = 100 - keys.reduce((a, k) => a + full[k], 0);
  keys.slice().sort((a, b) => (scaled[b] - full[b]) - (scaled[a] - full[a]))
      .slice(0, rem).forEach(k => { full[k] += 1; });
  return full;
}

/* 可选：偏好权重 + 企业类型 + 家乡城市（离家近） */
function preferencesCardStep() {
  return {
    id: 'preferences',
    kind: 'card',
    ask: '要不要让匹配更懂你？选填，约 20 秒。',
    render() {
      const dims = (Q.value_dimensions || []).map(([key, label]) => ({ key, label }));
      const dimChips = dims.map(d => `<button type="button" class="chip" data-dim="${esc(d.key)}">${esc(d.label)}</button>`).join('');
      const html = `<div class="chat-card" id="pref-card">
        <div class="cc-title">要不要让匹配更懂你？</div>
        <div class="cc-sub">选填，约 20 秒。偏好只影响排序权重，不是硬门槛；跳过不影响这份画像。</div>
        <div class="sec-t">最看重什么（可多选，不限数量）</div>
        <div class="chips" id="pref-dims">${dimChips}</div>
        <div id="pref-weights" class="pref-weights"></div>
        <div class="f" style="margin-top:10px"><label>家乡 / 家庭所在城市（可选，用来算“离家近”）</label>
          <input id="pref-home" type="text" maxlength="20" placeholder="比如：苏州"></div>
        <div class="cc-actions">
          <button type="button" class="btn btn-p" data-act="save">保存并继续</button>
          <button type="button" class="btn btn-g" data-act="skip">跳过</button>
        </div>
      </div>`;
      const el = appendMsg('bot', html, 'wide');
      const root = el.querySelector('.bubble');
      state.awaiting = { step: null, botEls: [el], resolve: () => {} };
      state.awaitingEls = (state.awaitingEls || []).concat([el]);
      const selected = [];
      let weights = {};
      const weightBox = root.querySelector('#pref-weights');

      const renderWeights = () => {
        if (!selected.length) { weightBox.innerHTML = ''; return; }
        const total = Object.values(weights).reduce((a, b) => a + b, 0);
        weightBox.innerHTML = selected.map(k => {
          const d = dims.find(x => x.key === k);
          return `<div class="pref-row"><span class="pref-label">${esc(d ? d.label : k)}</span>
            <input type="range" min="0" max="100" step="5" value="${weights[k]}" data-w="${esc(k)}">
            <span class="pref-val" data-v="${esc(k)}">${weights[k]}</span></div>`;
        }).join('') + `<div class="pref-sum">合计 <b id="pref-total">${total}</b> / 100
          <span class="muted">（保存时按比例折算为 100）</span></div>`;
        weightBox.querySelectorAll('input[type=range]').forEach(sl => {
          sl.oninput = () => {
            weights[sl.dataset.w] = +sl.value;
            const val = weightBox.querySelector(`[data-v="${sl.dataset.w}"]`);
            if (val) val.textContent = sl.value;
            const t = weightBox.querySelector('#pref-total');
            if (t) t.textContent = Object.values(weights).reduce((a, b) => a + b, 0);
          };
        });
      };

      root.querySelectorAll('#pref-dims .chip').forEach(btn => {
        btn.onclick = () => {
          const k = btn.dataset.dim;
          const at = selected.indexOf(k);
          if (at >= 0) { selected.splice(at, 1); btn.classList.remove('on'); }
          else {
            // v11：取消「最多 3 项」上限，五个关注点可以全选。
            selected.push(k); btn.classList.add('on');
          }
          weights = equalWeights(selected);
          renderWeights();
        };
      });

      return new Promise(resolve => {
        root.querySelector('[data-act="save"]').onclick = () => {
          const res = {
            values: normalizeWeights(weights),
            home_city: (root.querySelector('#pref-home').value || '').trim().slice(0, 20),
          };
          state.awaiting = null;
          el.classList.add('resolved');
          root.querySelectorAll('button,input').forEach(x => { try { x.disabled = true; } catch (e) {} });
          resolve(res);
        };
        root.querySelector('[data-act="skip"]').onclick = () => {
          state.awaiting = null;
          el.classList.add('resolved');
          root.querySelectorAll('button,input').forEach(x => { try { x.disabled = true; } catch (e) {} });
          resolve({ skipped: true });
        };
      });
    },
    summary(res) {
      if (!res || res.skipped) return '跳过偏好';
      const labels = Object.fromEntries((Q.value_dimensions || []).map(([k, l]) => [k, l]));
      const picked = Object.entries(res.values || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
      return picked.length ? '偏好：' + picked.map(([k, v]) => `${labels[k] || k} ${v}`).join(' / ') : '跳过偏好';
    },
    apply(res) {
      if (!res || res.skipped) return;
      state.answers.values = res.values;
      state.answers.fair.target_company_types = [];
      state.answers.fair.home_city = res.home_city || '';
    },
    reset() {
      state.answers.values = { salary: 0, stability: 0, growth: 0, autonomy: 0, impact: 0, balance: 0 };
      state.answers.fair.target_company_types = [];
      state.answers.fair.home_city = '';
    },
  };
}

function consentCardStep() {
  return {
    id: 'consent',
    kind: 'card',
    ask: '信息够了。最后确认两件事，我就把画像整理出来。',
    async render() {
      const html = `<div class="chat-card">
        <div class="cc-title">生成前确认</div>
        <label class="check"><input type="checkbox" id="c-save" checked>
          <span>把这份画像保存到本机数据库（<span class="mono">fair_profiles</span>），供后段匹配取用；取消则只在你浏览器里生成。</span></label>
        <label class="check"><input type="checkbox" id="c-ack">
          <span>我知道：简历里提到的技能只按「自述」算，只有标了实践 + 作品引用才算有材料；本次不施测兴趣问卷。</span></label>
        <div class="cc-actions"><button type="button" class="btn btn-p" data-act="gen">生成画像 JSON</button></div>
      </div>`;
      const el = appendMsg('bot', html, 'wide');
      const root = el.querySelector('.bubble');
      state.awaiting = { step: null, botEls: [el], resolve: () => {} };
      state.awaitingEls = (state.awaitingEls || []).concat([el]);
      return new Promise(resolve => {
        root.querySelector('[data-act="gen"]').onclick = () => {
          if (!root.querySelector('#c-ack').checked) {
            const label = root.querySelector('#c-ack').closest('.check');
            label.classList.add('shake');
            setTimeout(() => label.classList.remove('shake'), 500);
            return;
          }
          const save = root.querySelector('#c-save').checked;
          state.awaiting = null;
          el.classList.add('resolved');
          root.querySelectorAll('button,input').forEach(x => { try { x.disabled = true; } catch (e) {} });
          resolve({ save });
        };
      });
    },
    summary() { return '确认生成'; },
    apply(res) { state.save = res.save !== false; },
    reset() {},
  };
}

/* ---------------- 流程 ---------------- */

function fairSteps() {
  return [
    fairStep('stage', '这次主要想找什么？', {
      options: ['暑期实习', '日常实习', '秋招校招', '校招补录'],
      alias: { 暑期: '暑期实习', 日常: '日常实习', 秋招: '秋招校招', 校招: '秋招校招', 补录: '校招补录', 实习: '暑期实习' },
      after(value, a) {
        a.fair.stage_detail = value;
        a.target_stage = (value === '暑期实习' || value === '日常实习') ? '实习' : '校招';
        a.fair.target_stage = a.target_stage;
      },
      reset(a) { a.fair.stage_detail = ''; a.target_stage = ''; a.fair.target_stage = ''; },
    }),
    fairStep('direction', '想做什么方向或岗位？点参考项可以，自己写也可以。', {
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
    }),
    fairStep('region', '想在哪座城市工作？', {
      options: Q.regions,
      alias: { 上海: '上海', 北京: '北京', 深圳: '深圳', 广州: '广州', 杭州: '杭州', 南京: '南京', 苏州: '苏州', 成都: '成都' },
      after(value, a) { a.region = value; },
      reset(a) { a.region = ''; },
    }),
    fairStep('timeline', '最晚什么时候要落地？', {
      options: Q.fair.timeline_options,
      alias: { 今年: '今年暑期实习前', 明年: '明年暑期实习前', 毕业: '毕业前一年', 没想好: '还没想好', 不确定: '我不确定（登记为未知）' },
      after(value, a) { a.timeline = value === '我不确定（登记为未知）' ? '__unknown__' : value; },
      reset(a) { a.timeline = ''; },
    }),
  ];
}

function buildFlow() {
  const flow = [];
  if (!state.resume) flow.push(identityFreeTextStep);
  if (!state.answers.identity.phone && !state.answers.identity.email) flow.push(contactStep);
  fairSteps().forEach(step => flow.push(step));
  flow.push(preferencesCardStep());
  flow.push(consentCardStep());
  return flow;
}

function updateProgress() {
  const total = state.flow.length;
  const done = Math.min(Math.max(state.stepIdx, 0), total);
  $('progress').textContent = total ? `已答 ${done} / ${total} 题` : '';
  $('btn-back').disabled = state.history.length === 0 || state.generated;
}

async function runFlow(from) {
  const runId = ++state.runId;
  for (let i = from; i < state.flow.length; i++) {
    if (runId !== state.runId) return;
    state.stepIdx = i;
    updateProgress();
    const step = state.flow[i];
    try {
      const botEl = await say(step.ask);
      if (runId !== state.runId) return;
      const botEls = [botEl];
      state.awaitingEls = botEls.slice();

      let res;
      if (step.kind === 'card') {
        res = await step.render();
        if (runId !== state.runId) return;
        state.awaitingEls.filter(el => el !== botEl).forEach(el => botEls.push(el));
      } else {
        res = await waitAnswer(step, botEls);
        if (runId !== state.runId) return;
      }

      const summary = step.summary ? step.summary(res) : (res.label || res.value || '');
      const userEl = appendMsg('user', esc(summary));
      step.apply(res);
      state.history.push({ idx: i, els: [...botEls, userEl], step });
      updateProgress();
      await sleep(100);
    } catch (e) {
      console.error(e);
      await say(note('bad', '这一步出了点问题，点「上一步」重试，或「重新开始」。'), { fast: true });
      return;
    }
  }
  if (runId !== state.runId) return;
  generateProfile();
}

/* ---------------- 上一步 / 重新开始 ---------------- */

function goBack() {
  if (state.generated || !state.history.length) return;
  const last = state.history.pop();
  const currentEls = state.awaitingEls.slice();
  state.runId++;
  state.awaiting = null;
  state.awaitingEls = [];
  disableComposer();
  $('composer').classList.remove('done');
  [...currentEls, ...last.els].forEach(el => el && el.remove());
  if (last.step.reset) last.step.reset();
  updateProgress();
  runFlow(last.idx);
}

/* ---------------- 生成与结果 ---------------- */

async function generateProfile() {
  syncAnswers();
  state.answers.fair.event_date = todayISO();
  applyResumeSkills();
  const typing = appendMsg('bot', '<div class="bubble typing"><i></i><i></i><i></i></div>');
  await sleep(320);
  typing.remove();
  $('progress').textContent = '正在整理…';
  let r;
  try {
    r = await api('/api/fair/profile', {
      method: 'POST',
      body: JSON.stringify({
        user_id: 'fair',
        answers: state.answers,
        resume: state.resume || {},
        consent: { store_locally: state.save, send_resume_to_model: !!(state.resume && state.resumeLLM) },
        save: state.save,
      }),
    });
  } catch (e) {
    await say(note('bad', '生成失败：网络或服务异常。'), { fast: true });
    return;
  }
  if (!r.ok) { await say(note('bad', esc(r.error || '生成失败')), { fast: true }); return; }
  state.generated = true;
  $('composer').classList.add('done');
  state.lastDoc = r.document;
  state.lastSaved = !!r.saved;
  $('progress').textContent = '已完成';
  $('btn-back').disabled = true;
  disableComposer();
  $('hint').textContent = '画像已生成，可以下载 JSON 交给后段匹配。';
  const el = appendMsg('bot', resultHTML(r.document, r.saved), 'wide');
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resultHTML(doc, saved) {
  const q = doc.quality || {};
  const traits = (doc.summary && doc.summary.traits) || [];
  const fair = doc.fair || {};
  const identity = doc.identity || {};
  const exps = ((doc.resume || {}).extraction || {}).experiences || [];
  const skills = (doc.profile && doc.profile.skills) || [];
  const flagship = fair.flagship_experience || '';
  const missing = (q.missing || []).map(m => `<li>${esc(m.why)}</li>`).join('');
  const warnings = (q.warnings || []).map(w => `<li>${esc(w)}</li>`).join('');
  const honesty = (((doc.profile_card || {}).honesty_summary) || []).map(esc).join('<br>');

  const vMap = (doc.answers && doc.answers.values) || {};
  const vSum = Object.values(vMap).reduce((a, b) => a + b, 0);
  const vLabels = Object.fromEntries((Q.value_dimensions || []).map(([k, l]) => [k, l]));
  const prefText = vSum > 0
    ? Object.entries(vMap).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${vLabels[k] || k} ${v}`).join(' · ')
    : '';
  const empText = ((fair.target_company_types) || []).join('、');
  const prefRows = (prefText ? `<div class="kv"><span class="k">偏好权重</span><span class="v">${esc(prefText)}（自述，合计 100）</span></div>` : '')
    + (empText ? `<div class="kv"><span class="k">企业类型</span><span class="v">${esc(empText)}（软偏好）</span></div>` : '')
    + (fair.home_city ? `<div class="kv"><span class="k">家乡城市</span><span class="v">${esc(fair.home_city)}（离家近参考）</span></div>` : '');
  const expList = exps.length
    ? exps.map(x => `<li class="${flagship && x.text === flagship ? 'flag' : ''}">${esc(x.text)}${flagship && x.text === flagship ? ' <span class="tag ok tiny">主经历</span>' : ''}</li>`).join('')
    : '<li class="muted">没有从简历里读到项目/实习经历。</li>';
  const skillChips = skills.length
    ? skills.map(x => `<span class="tag ${x.evidence_state === 'artifact_provided' ? 'ok' : 'warn'}" title="${esc(x.evidence_text || '')}">${esc(x.name)}</span>`).join('')
    : '<span class="muted">无</span>';

  return `<div class="result-card">
    <div class="rc-head">
      <div class="eyebrow">fair_profile · 双选会最小画像</div>
      <h2>${esc((doc.summary && doc.summary.headline) || '画像')}</h2>
      <p class="muted small"><span class="mono">${esc(doc.fair_id)}</span> · ${esc(doc.generated_at)}
        · v${esc(doc.document_version)} · ${saved ? '<span class="tag ok">已存入本机</span>' : '<span class="tag unk">仅浏览器内</span>'}</p>
      <div class="prog"><div class="bar"><i style="width:${q.score || 0}%"></i></div>
        <span><b>${q.score || 0}</b>/100 · ${esc(q.level || '')}</span></div>
    </div>
    <div class="rc-body">
      <div class="sec-t">这份画像的特点</div>
      <ul class="traitlist">${traits.map(t => `<li>${esc(t)}</li>`).join('')}</ul>

      <div class="sec-t">身份</div>
      <div class="kv"><span class="k">姓名</span><span class="v">${esc(identity.name || '—')}</span></div>
      <div class="kv"><span class="k">学校 / 专业</span><span class="v">${esc([identity.school, identity.major].filter(Boolean).join(' · ') || '—')}</span></div>
      <div class="kv"><span class="k">学历 / 年级</span><span class="v">${esc([identity.degree, identity.grade].filter(Boolean).join(' · ') || '—')}</span></div>
      <div class="kv"><span class="k">联系方式</span><span class="v">${esc([identity.phone, identity.email].filter(Boolean).join(' · ') || '—')}</span></div>

      <div class="sec-t">求职意向（4 个必答）</div>
      <div class="kv"><span class="k">阶段</span><span class="v">${esc([fair.stage_detail, fair.target_stage].filter(Boolean).join(' · ') || '—')}</span></div>
      <div class="kv"><span class="k">方向</span><span class="v">${esc(doc.answers.direction_interest || '—')}</span></div>
      <div class="kv"><span class="k">城市</span><span class="v">${esc(doc.answers.region || '—')}</span></div>
      <div class="kv"><span class="k">时间窗</span><span class="v">${esc(doc.answers.timeline || '—')}</span></div>
      <div class="kv"><span class="k">场次</span><span class="v">${esc(fair.event_date || '—')}</span></div>
      ${prefRows}

      <div class="sec-t">能力关键词（简历/自述，未核实）</div>
      <div class="chips">${skillChips}</div>

      <div class="sec-t">项目 / 实习经历</div>
      <ul class="explist">${expList}</ul>

      <div class="sec-t">兴趣 / 价值观</div>
      <div class="note"><span class="ic">i</span><div>${vSum > 0
        ? 'RIASEC 未施测，兴趣记为「未测量」；价值观按偏好权重收集（合计 100，自述，仅用于排序）。'
        : '本次未施测 RIASEC，兴趣记为「未测量」；价值观本次不收集，记为「未收集」。都不是低分。'}</div></div>

      ${missing ? `<div class="sec-t">还缺什么（影响后段匹配）</div><ul class="misslist">${missing}</ul>` : ''}
      ${warnings ? `<div class="sec-t">需要注意</div><div class="note warn"><span class="ic">⚠</span><div><ul class="misslist">${warnings}</ul></div></div>` : ''}
      <div class="sec-t">诚实性摘要</div>
      <div class="note ok"><span class="ic">✓</span><div>${honesty || '信息来自本人陈述与简历抽取；未做任何能力核实。'}</div></div>

      <div class="json-actions">
        <button type="button" class="btn btn-p" onclick="downloadJson()">下载 JSON</button>
        <button type="button" class="btn btn-g" onclick="copyJson()">复制 JSON</button>
        <button type="button" class="btn btn-g" onclick="location.reload()">再采一份</button>
        <span class="muted small" id="copy-msg"></span>
      </div>
      <details style="margin-top:14px"><summary>机器可读 · 完整 JSON（doc v${esc(doc.document_version)}）</summary>
        <pre class="out" id="json-out">${esc(JSON.stringify(doc, null, 2))}</pre></details>
    </div>
  </div>`;
}

function downloadJson() {
  if (!state.lastDoc) return;
  const blob = new Blob([JSON.stringify(state.lastDoc, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${state.lastDoc.fair_id || 'fair-profile'}.fair-profile.json`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function copyJson() {
  if (!state.lastDoc) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.lastDoc, null, 2));
    const m = $('copy-msg'); if (m) m.textContent = '已复制到剪贴板';
  } catch (e) {
    const m = $('copy-msg'); if (m) m.textContent = '复制失败，请从下面的 JSON 手动选择';
  }
}

/* ---------------- 启动 ---------------- */

async function init() {
  try {
    Q = await api('/api/fair/questionnaire');
  } catch (e) {
    $('status-t').textContent = '后端未连接';
    return;
  }
  if (!Q || !Q.ok) {
    $('status-t').textContent = '问卷加载失败';
    $('status').querySelector('.dot').className = 'dot bad';
    return;
  }
  $('status-t').textContent = Q.fair.llm_configured ? '模型可用（可增强抽取）' : '仅本地规则抽取';
  $('status').querySelector('.dot').className = 'dot ' + (Q.fair.llm_configured ? 'ok' : 'warn');
  $('ver').textContent = 'fair_doc ' + Q.fair.doc_version + ' · schema ' + Q.schema_version;

  $('send').onclick = sendTyped;
  $('msg').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sendTyped(); } });
  $('btn-back').onclick = goBack;
  $('btn-restart').onclick = () => location.reload();

  state.answers = blankAnswers();
  disableComposer();

  await say('你好，我是 CareerSafari。带上简历的话，我只再问你 4 个问题，就能生成一份双选会画像。');
  const choice = await startCard();
  if (choice === 'uploaded') {
    await say(`简历解析完了（${state.resume.method === 'rules+llm' ? '规则 + 模型' : '本地规则'}）。`, { fast: true });
    const action = await resumeSummaryCard();
    if (action === 'edit') await identityEditCard();
    const exps = state.resume.experiences || [];
    if (exps.length > 1) {
      const picked = await flagshipCard();
      if (picked && picked.text) state.answers.fair.flagship_experience = picked.text;
    } else if (exps.length === 1) {
      state.answers.fair.flagship_experience = exps[0].text;
    }
  } else {
    await say('没问题，那我们只聊 4 个问题；身份信息可以一句话带过，也可以跳过。');
  }
  state.flow = buildFlow();
  updateProgress();
  runFlow(0);
}

init();
