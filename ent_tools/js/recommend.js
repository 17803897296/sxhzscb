<script>
function _jobRecommendRules(major, exps, skills, exclude) {
  /* 专业为空不给推荐（与服务端同口径）。规则表按专业名查，空专业只能兜底成
     「管培生 / 数据分析 / 业务运营」这类管理通用岗——看着像给了建议，其实是
     替学生答题。 */
  if (!String(major || "").trim()) {
    return { ok: false, need_major: true, mode: "blocked", candidates: [],
             notice: "还没拿到你的专业，先回上一步把专业填上，我再给岗位。" };
  }
  /* 所有专业统一校招口径：只写校招公告里应届可投的具体岗位名，不写岗位族 */
  const MAJOR = {
    "管理科学与工程": ["管培生（运营/综合方向）","供应链管理类管培生","项目管理类管培生",
      "数据分析岗","商业分析岗","经营分析岗","供应链计划专员","需求计划专员","采购管培生",
      "数据智能培训生","BI/数据分析培训生","咨询分析师","风控/合规专员","ERP实施顾问"],
    "运营与供应链管理": ["供应链管培生","供应链计划专员","需求计划专员","采购管培生",
      "物流运营专员","库存计划专员","生产计划专员","运营管理培训生","供应商管理专员",
      "仓储管理专员","物控专员","供应链数据分析岗"],
    "大数据分析与决策": ["数据分析岗","数据分析培训生","BI分析师","商业分析岗",
      "数据开发工程师","数据运营专员","策略分析岗","数据产品经理",
      "算法工程师","经营分析岗","数据治理专员","用户研究分析师"],
    "金融服务与金融工程": ["行业研究助理","量化研究岗","风险管理培训生","投行承做岗",
      "金融数据分析岗","管培生（金融方向）","金融市场岗","合规专员","财富顾问培训生",
      "信用分析岗","交易员助理","金融科技培训生"],
    "复杂工程与智能管理": ["项目管理培训生","工程咨询助理","数据分析岗","流程改善专员",
      "系统方案工程师","项目经理助理","造价/合约专员","工业工程师","质量工程师",
      "智能制造培训生","BIM工程师","供应链计划专员"],
    "智能系统管理": ["产品经理","数据分析岗","算法产品经理","系统运营专员",
      "决策支持分析岗","产品运营培训生","信息管理专员","ERP实施顾问","数据产品经理",
      "用户体验研究员","BI分析师","咨询分析师"],
    "工业工程": ["工业工程师","生产计划专员","工艺改善工程师","质量工程师","运营分析岗",
      "供应链管培生","精益改善专员","制造管培生","设备工程师","IE工程师",
      "物控专员","项目管理培训生"],
    "计算机": ["后端开发工程师","算法工程师","数据分析岗",
      "测试开发工程师","前端开发工程师","客户端开发工程师",
      "运维开发工程师","安全工程师","数据开发工程师",
      "AI工程师","软件开发工程师","技术支持工程师"],
    "软件工程": ["软件开发工程师","后端开发工程师","算法工程师",
      "测试开发工程师","前端开发工程师","移动端开发工程师",
      "DevOps工程师","数据开发工程师","质量工程师",
      "产品工程师","嵌入式软件工程师","技术支持工程师"],
    "金融": ["行业研究助理","量化研究岗","财富顾问培训生","风险管理培训生",
      "投行承做岗","管培生（金融方向）","客户经理培训生","金融数据分析岗",
      "信贷管理专员","合规专员","运营培训生（银行方向）","金融市场岗"],
    /* 理科 / 化工类（2026-11-15 补，与服务端 MAJOR_BASE 同步）：此前没有理科词表，
       化学等专业会兜底成「管培生/数据分析岗/业务运营岗」这类管理通用岗。 */
    "化学": ["研发工程师","合成研究员","化验/质检岗","分析测试岗",
      "工艺工程师","实验室技术员","EHS安全工程师","质量工程师"],
    "化学工程与工艺": ["工艺工程师","生产技术员","化验/质检岗",
      "EHS安全工程师","设备工程师","质量工程师"],
    "材料": ["材料研发工程师","失效分析工程师","工艺工程师",
      "质量工程师","实验室技术员","化验/质检岗"],
    "生物": ["生物研发工程师","发酵/细胞培养技术员","实验员","质检QA",
      "注册专员","生产技术员"],
    "药学": ["药物分析研究员","药品注册专员","QA/QC岗","生产技术员",
      "医学信息沟通专员"],
    "数学": ["数据分析岗","统计分析师","算法工程师","精算助理",
      "量化研究岗","数据开发工程师"],
    "环境工程": ["EHS工程师","环保工程师","水处理工程师","环境检测员",
      "化验/质检岗"]
  };
  const RULES = [
    [["供应链","库存","补货","采购","物流","仓储","周转","供应商"],
     ["供应链计划专员","需求计划专员","采购管培生","物流运营专员"],"实习里做过供应链/库存相关的实际工作"],
    [["SQL","Python","数据","分析","预测","报表","指标","建模","取数"],
     ["数据分析岗","商业分析岗","BI分析师","数据运营专员"],"实习里用到取数、分析或预测"],
    [["产品","需求文档","原型","用户","PRD","竞品"],
     ["产品经理","产品运营培训生","商业分析岗"],"实习里接触过产品或需求工作"],
    [["项目","协调","排期","交付","流程","推进"],
     ["项目管理培训生","流程改善专员","项目经理助理"],"实习里有项目协调或流程推进经历"],
    [["销售","客户","商务","BD","渠道","市场"],
     ["客户经理培训生","市场分析岗","商务专员"],"实习里接触过客户或市场"],
    [["财务","会计","审计","预算","成本"],
     ["财务分析岗","成本分析专员","内审/风控专员"],"实习里接触过财务或成本"]
  ];
  /* 学科大类兜底表：MAJOR 里查不到专业时按大类给岗。
     旧版查不到就写死「管培生（综合方向）/数据分析岗/商业分析岗/项目管理培训生」——
     那是管理学院 5 个方向的入口，食品 / 机械 / 土木 / 纺织的学生离线打开时
     全被当成管培生。这里与服务端 CATEGORY_JOBS_MAP 对齐（大类名同）。 */
  const CAT = [
    [["食品","粮食","乳品"], ["食品研发工程师","品控专员","食品检验员","生产工艺员",
      "质量管理专员","化验/质检岗","研发助理","感官评价专员","技术销售工程师","供应链专员"]],
    [["农学","作物","园艺","植保","动物","畜牧","水产","兽医"], ["农艺师","育种技术员",
      "植保技术员","养殖技术员","饲料配方师","农产品品控岗","化验/质检岗","技术销售工程师"]],
    [["化学","化工","材料","冶金","高分子"], ["研发工程师","工艺工程师",
      "质量工程师","化验/质检岗","分析测试岗","实验室技术员","EHS安全工程师"]],
    [["数学","物理","统计"], ["数据分析岗","算法工程师","统计分析师","精算助理",
      "量化研究岗","数据开发工程师"]],
    [["计算机","软件","人工智能","数据科学","大数据","信息安全","物联网"],
     ["软件开发工程师","前端开发工程师","测试开发工程师",
      "数据开发工程师","算法工程师","网络安全工程师","运维工程师"]],
    [["机械","车辆","汽车","航空","航天","船舶","能源","动力","机器人","智能制造"],
     ["机械设计工程师","结构工程师","工艺工程师","设备工程师",
      "质量工程师","仿真/CAE工程师","测试工程师","技术支持工程师"]],
    [["电气","自动化","控制","通信","电子","微电子","光电","仪器"],
     ["电气工程师","自动化工程师","硬件工程师","嵌入式软件工程师","测试工程师",
      "工艺工程师","设备工程师","技术支持工程师"]],
    [["土木","建筑","水利","测绘","给排水","工程造价"],
     ["结构工程师","土建工程师","施工技术员","造价/预算岗","BIM工程师","安全工程师"]],
    [["环境工程","环境科学","安全工程","矿业"], ["EHS工程师","环保工程师",
      "水处理工程师","环境检测员","安全工程师","化验/质检岗"]],
    [["纺织","服装","轻化","轻工","包装","印刷"], ["纺织工程师","面料开发工程师",
      "染整技术员","工艺工程师","质量工程师","检测工程师"]],
    [["生物","医学","临床","护理","药学","中医"], ["研发工程师","实验技术员",
      "检测工程师","质量检验员（QC）","注册专员","临床研究助理","医药代表"]],
    [["中文","外语","英语","新闻","传播","教育","历史","哲学","法学"], ["新媒体运营",
      "内容编辑","文案策划","品牌市场专员","人力资源专员","行政专员","翻译","编辑/记者"]]
  ];
  /* 识别不出大类时的中性入口表：任何专业都投得进，且不含管培生这类经管专属入口 */
  const NEUTRAL = ["生产技术员","质量管理专员","化验/质检岗","销售专员","采购专员",
    "市场专员","行政专员","人事专员","客户服务专员","项目助理"];
  const WIDE_MAJOR = ["管理","经济","金融","会计","营销","供应链","物流","计算机","软件",
    "数据","数学","统计","信息"];
  const DATA_NARROW = ["数据分析岗", "数据运营专员"];
  const text = (exps || []).concat(skills || []).join(" ").toLowerCase();
  let base = MAJOR[major] || [];
  if (!base.length) {
    for (const k in MAJOR) { if (major && (major.indexOf(k) >= 0 || k.indexOf(major) >= 0)) { base = MAJOR[k]; break; } }
  }
  if (!base.length) {
    for (const row of CAT) {
      if (row[0].some(function(k) { return (major || "").indexOf(k) >= 0; })) { base = row[1]; break; }
    }
  }
  if (!base.length) base = NEUTRAL;
  const cands = [], seen = new Set();
  /* 「数据」这条信号原来不分专业给 数据分析/商业分析/BI —— 食品专业写「SPSS 统计分析」
     也会看到 BI 分析师。非经管 / 非计算机专业收窄到通用数据岗。 */
  const wide = WIDE_MAJOR.some(function(k) { return (major || "").indexOf(k) >= 0; });
  RULES.forEach(function(pair) {
    if (pair[0].some(function(k) { return text.indexOf(k.toLowerCase()) >= 0; })) {
      const isData = (pair[2] || "").indexOf("取数") >= 0;
      const jobs = (isData && !wide) ? DATA_NARROW : pair[1];
      jobs.forEach(function(j) { cands.push({ title: j, why: pair[2] }); });
    }
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
           notice: "离线（双击）模式：未调用模型，下面是本地规则推荐；想让模型按你的经历生成，请用 统一入口首页（/） 打开本页。" };
}

function jobRecommendStep() {
  return {
    id: 'job-recommend', kind: 'card',
    ask: '根据你说的专业和实习 / 竞赛 / 论文，我先给几个今年校招能投的岗位，勾几个最想去的；不满意可以换一批。',
    async render() {
      const a = state.answers;
      const major = (a.identity && a.identity.major) || a.major || '';
      const r0 = state.resume || {};
      const rx = r0.extraction || {};
      /* v14：实习 / 竞赛 / 论文分开取，模型才能分别用上；兼容两种简历结构 */
      function bag(k) {
        return (r0[k] || rx[k] || []).map(function(x) {
          return (x && (x.text || x.name || x.title)) || x || '';
        }).filter(Boolean);
      }
      const internships = bag('experiences');
      const competitions = bag('competitions');
      const papers = bag('papers');
      const skills = bag('skills');
      // v13：问卷里刚填的「实习 / 竞赛 / 论文」也并进去，题不白问
      const typed = String(a.direction_interest || '').trim();
      if (typed && typed !== '跳过' && internships.indexOf(typed) < 0) internships.push(typed);
      const allExps = internships.concat(competitions, papers);
      const basis = allExps.length ? allExps.join('；') : (major ? major + ' 的核心课程' : '尚未提供经历');
      // 教培题排在推荐之前，这里能直接读到学生是否接受「不限专业」岗位
      const acceptNoMajor = (a.accept_no_major === true);
      let cands = [], tag = '', notice = '', tierLabel = '', curTier = 0;
      /* 2026-11-17：升层必须由学生点「放宽一档」触发。
         以前本层换完会自动升到跨专业层，点几次「换一批」就被灌进质量/生产/测试岗。 */
      let crossMajor = false, hasNext = false, blocked = false, wantRelax = false;
      const shown = [];               // 历次展示过的岗位名，跨层也不重复
      async function load() {
        let r;
        try {
          r = await (await fetch('/api/fair/recommend-jobs', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              major: major, internships: internships, competitions: competitions,
              papers: papers, skills: skills, shown: shown, tier: curTier,
              accept_no_major: acceptNoMajor, allow_relax: wantRelax
            })
          })).json();
        } catch (e) { r = null; }
        wantRelax = false;
        /* 专业为空：服务端直接拒绝。这里不能再退回规则池——规则池对空专业给的正是
           销售/行政/人事/生产技术员那批泛岗，等于把闸门又拆了。 */
        if (r && r.need_major) {
          blocked = true; crossMajor = false; hasNext = false;
          cands = []; tierLabel = ''; tag = '';
          notice = r.error || '还没拿到你的专业，先回上一步把专业填上。';
          return;
        }
        blocked = false;
        if (!r || !r.ok) {
          const rr = _jobRecommendRules(major, allExps, skills, shown);
          if (rr && rr.need_major) {          // 离线兜底也认「没专业就不给」这条
            blocked = true; crossMajor = false; hasNext = false;
            cands = []; tierLabel = ''; tag = ''; notice = rr.notice;
            return;
          }
          r = { ok: true, mode: 'rules', candidates: rr.candidates || [],
                tier: curTier, tier_label: '离线规则（双击模式）', notice: rr.notice };
        }
        if (typeof r.tier === 'number') curTier = r.tier;
        tierLabel = r.tier_label || tierLabel;
        crossMajor = !!r.cross_major;
        hasNext = !!r.has_next;
        cands = r.candidates || [];
        tag = (r.mode === 'llm-web') ? '（联网搜索 · 真实校招口径）'
            : (r.mode === 'llm') ? '（模型生成 · 校招口径）'
            : (FAIR_ONLINE ? '（规则兜底 · 校招口径）' : '（离线规则 · 双击模式）');
        notice = r.notice || '点一个岗位看推荐依据。';
      }
      await load();
      const html = '<div class="chat-card">' +
        '<div class="cc-title">推荐岗位 <span id="job-tag">' + esc(tag) + '</span></div>' +
        (FAIR_ONLINE ? '' :
          '<div class="cc-sub" style="color:#b45309">你现在是双击打开（离线模式），推荐不调模型。想让模型按你的专业和实习生成，请用 <b>统一入口首页（/）</b> 打开本页。</div>') +
        '<div class="cc-sub" id="job-tier" style="color:#2563eb;font-weight:600"></div>' +
        '<div class="cc-sub">依据：' + esc(basis) + '。都是校招可投的具体岗位名；勾了才算数，不会写进任何企业资料。</div>' +
        '<div class="cc-chips" id="job-cands"></div>' +
        '<div class="cc-sub muted" id="job-why"></div>' +
        '<input id="job-custom" type="text" maxlength="120" placeholder="还想加别的岗位？直接写，用逗号分开">' +
        '<div class="cc-actions">' +
          '<button type="button" class="btn btn-g" data-act="refresh">不满意，换一批</button>' +
          '<button type="button" class="btn btn-g" data-act="relax" style="display:none;color:#b45309;border-color:#f0c68a">放宽一档：看跨专业岗位</button>' +
          '<button type="button" class="btn btn-p" data-act="ok">选好了</button>' +
          '<button type="button" class="btn btn-g" data-act="skip">跳过，我自己写</button>' +
        '</div></div>';
      return makeCard(html, function(root, done) {
        /* 用岗位名而不是下标记录选择：换一批后已勾选的不会丢 */
        const selTitles = new Set();
        const chipsBox = root.querySelector('#job-cands');
        const whyBox = root.querySelector('#job-why');
        const relaxBtn = root.querySelector('[data-act="relax"]');
        const refreshBtn = root.querySelector('[data-act="refresh"]');
        function paint() {
          chipsBox.innerHTML = cands.map(function(c, i) {
            /* v15：推荐池与岗位库解耦，不再有 db_level / 校招标——候选就是
               通用校招岗位名，不带任何「库里有/没有」的来源信息。
               v17：跨专业层的岗位必须自带角标，否则学生分不清哪些是按专业给的。 */
            var _why = (c.why || '');
            var _badge = crossMajor
              ? ' <span style="font-size:11px;color:#b45309;border:1px solid #f0c68a;border-radius:4px;padding:0 3px;margin-left:4px">跨专业</span>'
              : '';
            return '<button type="button" class="chip" data-i="' + i + '" title="' + esc(_why) + '">' + esc(c.title) + _badge + '</button>';
          }).join('') || (blocked
            ? '<span class="muted">没有专业，就没有推荐。回上一步填专业。</span>'
            : '<span class="muted">这一层看完了；想看更宽的岗位，点下面「放宽一档」，或直接手写。</span>');
          root.querySelector('#job-tag').textContent = tag;
          var tierEl = root.querySelector('#job-tier');
          if (tierEl) {
            tierEl.textContent = tierLabel
              ? ((crossMajor ? '⚠ 当前口径：' : '当前口径：') + tierLabel
                 + (crossMajor ? '（这些岗位不要求你的专业，和上面的对口岗位不是一回事）' : ''))
              : '';
            tierEl.style.color = crossMajor ? '#b45309' : '#2563eb';
          }
          whyBox.textContent = notice;
          if (relaxBtn) relaxBtn.style.display = (hasNext && !blocked) ? '' : 'none';
          if (refreshBtn) refreshBtn.disabled = blocked || !cands.length;
          chipsBox.querySelectorAll('[data-i]').forEach(function(b) {
            const c = cands[+b.dataset.i];
            if (c && selTitles.has(c.title)) { b.style.background = 'var(--accent, #2563eb)'; b.style.color = '#fff'; }
            b.onclick = function() {
              if (!c) return;
              if (selTitles.has(c.title)) { selTitles.delete(c.title); b.style.background = ''; b.style.color = ''; }
              else { selTitles.add(c.title); b.style.background = 'var(--accent, #2563eb)'; b.style.color = '#fff'; }
              whyBox.textContent = c.title + '：' + (c.why || '') + _srcNote(c)
                + (crossMajor ? '（这一层是跨专业岗位，投之前先确认它要不要求你的专业。）' : '');
            };
          });
        }
        paint();
        root.querySelector('[data-act="refresh"]').onclick = async function() {
          cands.forEach(function(c) { if (c && c.title && shown.indexOf(c.title) < 0) shown.push(c.title); });
          whyBox.textContent = '正在换一批（只在本层内换，不升层）…';
          await load();
          paint();
        };
        if (relaxBtn) relaxBtn.onclick = async function() {
          cands.forEach(function(c) { if (c && c.title && shown.indexOf(c.title) < 0) shown.push(c.title); });
          wantRelax = true;                  // 只有这一次请求允许升层
          whyBox.textContent = '正在放宽一档…';
          await load();
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
        /* need_major 是服务端的拒绝答复，必须原样交给界面——不能在这里掉进规则兜底，
           规则池对空专业给的正是「销售/行政/人事/生产技术员」那批泛岗。 */
        /* need_major 是服务端的拒绝答复；ok:true 但 candidates 为空是「本层看完了」，
           两者都必须原样交给界面——掉进规则兜底会把「本层看完，点放宽」变成
           又一屏离线泛岗，正好把新加的层闸门绕过去。 */
        .then(j => (j && (j.need_major || j.ok)) ? _resp(j) : fallback())
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
