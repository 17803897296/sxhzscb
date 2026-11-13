# -*- coding: utf-8 -*-
"""
v4 报告渲染层：采集端实时驱动匹配端。

相对 v3 的变化：
  - 报告不再是生成时算好的静态快照。学生改专业 / 勾选岗位 / 家乡 / 五项权重后，
    页面读取 fair_profile 并实时重算排序分、算分依据、现场提问优先级、队列位次与顺延目标。
  - 加入企业端事实校验徽标：模型结论与真实 JD / 行业是否一致，低可信的显式标出。
  - 加入筛选（全部 / 高可信 / 推断待核实）与折叠。

形式约束不变：不新增 CSS、不改 HTML 骨架、不改聊天流程，只往 #mres 填内容，
复用 .tier / .row / .rowhead / .nm / .sc / .calc / .jobbox / .note。
"""

REPORT_JS = r"""<script>
(function(){
const D = __TRI_DATA__;
let BASE = D.rows, W0 = D.weights, PM = D.province_map || {};
const ORDER = {hit:0, inferred:1, miss:2};
const WMAP = {岗位:'industry', 行业:'industry', 职责:'scale', 阶段:'scale',
              薪资:'scale', 人数:'scale', 城市:'city', 性质:'company_type',
              作息:'city', 证据:'scale'};
let injected=false, suppress=false, timer=null, lastSig='', filter='all';
let wOverride=null, visited=0, remainMin=210, currentRows=[], visitedSet={};
let feedback={}, sortMode='queue';          // v7：现场核实回流 + 展位动线排序
/* v11：完整报告是另一个页面（新标签 fair.html?detail=企业名）；列表页只显示两行摘要 */
const DETAIL_NAME = new URLSearchParams(location.search).get('detail') || '';
const DIM_LABEL={city:'城市条件',home:'离家近',company_type:'企业类型',
                 industry:'行业对口',scale:'企业规模档',
                 industry_status:'行业地位',growth:'成长性'};

__RULES_JS__
function rankValue(rank,n){ if(n<=1) return 1.0; return +(1.0-(rank-1)/(n-1)*0.6).toFixed(3); }
function cityCategories(city){
  if(!city||city==='未知'||city==='') return [];
  const R=RULES; let cats=[];
  if(R.city_first.indexOf(city)>=0) cats.push('一线');
  else if(R.city_new1.indexOf(city)>=0) cats.push('新一线');
  else if(R.city_cap.indexOf(city)>=0) cats.push('其他省会');
  else cats.push('其他');
  if(R.city_econ.indexOf(city)>=0) cats.push('经济发达');
  if(R.city_transit.indexOf(city)>=0) cats.push('交通发达');
  if(R.city_eco.indexOf(city)>=0) cats.push('生态友好');
  return cats;
}
/* 取企业全部工作城市；没有工作城市清单时退回主城市单值 */
function workCities(r){
  const w=(r&&r.v42&&r.v42.work_cities)||null;
  if(w&&w.length) return w;
  return r&&r.city?[r.city]:[];
}
function companyTypeBucket(nature){
  const b=RULES.ct_buckets;
  for(const k in b){ if(b[k].indexOf(nature)>=0) return k; }
  return null;
}
function classifyScale(nature,name,industry){
  name=name||''; const n=nature||'';
  const listed=/股份|集团|上市|银行/.test(name);
  if(n==='央企') return (listed||name.indexOf('集团')>=0)?'龙头特大':'大型';
  if(n==='国企') return (listed||name.indexOf('集团')>=0)?'大型':'中型';
  if(n==='事业单位') return (/大学|医院|学院|研究院/.test(name))?'大型':'中型';
  if(n==='混合所有制') return '中型';
  if(listed||name.indexOf('集团')>=0) return (/集团|股份|科技股份|电气|银行|证券|能源|数据/.test(name))?'大型':'中型';
  return '中小型';
}

/* ---------- v6：同源服务下从服务端拉最新 payload ----------
   采集端写库 -> fair_ingest 重建 fair_payload.json -> 报告层换掉 BASE/W0，
   实现「学生提交画像 -> 匹配端报告换一份」的跨会话闭环。
   file:// 双击时不发请求，完全离线。 */
function refreshFromServer(){
  if(!/^https?:$/.test(location.protocol)) return;
  fetch('/api/fair/payload').then(function(r){return r.json();}).then(function(p){
    if(!p || !p.rows || !p.rows.length) return;
    D.rows=p.rows; D.weights=p.weights||D.weights; D.profile=p.profile||D.profile;
    D.stats=p.stats||D.stats; D.sources=p.sources||D.sources;
    if(p.active_fair_id) D.active_fair_id=p.active_fair_id;
    if(p.province_map) PM=p.province_map;
    BASE=D.rows; W0=D.weights;
    lastSig=''; render();
  }).catch(function(){});
}
window.__triRefresh=refreshFromServer;

/* ---------- v7：现场「招 / 不招」核实结果 ----------
   离线写 localStorage（重开页面仍在），同源服务同时写 fair_feedback 表并重建 payload。
   结果会反向修正企业端证据等级，形成 采集端→企业端→匹配端→现场→企业端 的闭环。 */
function loadFeedback(){
  try{ feedback=JSON.parse(localStorage.getItem('fair_feedback')||'{}')||{}; }catch(err){feedback={};}
  if(!/^https?:$/.test(location.protocol)) return;
  fetch('/api/fair/feedback').then(function(r){return r.json();}).then(function(j){
    if(j&&j.items&&Object.keys(j.items).length){ feedback=j.items; render(); }
  }).catch(function(){});
}
function saveFeedback(name,result){
  if(!name) return;
  feedback[name]={result:result,note:'',updated_at:new Date().toISOString().slice(0,19)};
  try{ localStorage.setItem('fair_feedback',JSON.stringify(feedback)); }catch(err){}
  visitedSet[name]=1; visited++; remainMin=Math.max(0,remainMin-1);
  if(/^https?:$/.test(location.protocol)){
    fetch('/api/fair/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name,result:result})}).catch(function(){});
  }
  render();
}

/* 意向岗位 vs 企业真实岗位名：优先用大模型语义映射判定「是否符合」，
   模型没覆盖到时退回字面/关键词匹配。返回 [{intent, job, via, reason}]。
   jobTitles = 企业校招公告里的真实岗位名（不是模型预生成的 matched_jobs），
   这样报告里显示的命中岗位才和采集端勾选的岗位对得上。 */
function matchIntent(titles,intended,sem,jobTitles){
  const KW=['供应链','数据','分析','计划','研究','量化','算法','开发','产品',
            '运营','采购','物流','财务','销售','工程','项目','风控','交易',
            '顾问','教师','研发','质量','管培','投资'];
  const pool=((jobTitles&&jobTitles.length)?jobTitles:titles)||[];
  const out=[];
  (intended||[]).forEach(function(x){
    const m=(sem||{})[x];
    let hitJob=null, via='', reason='';
    if(m&&m.via==='llm'){
      const exact=pool.filter(function(t){
        return (m.titles||[]).some(function(s){ return s&&t&&(s===t||t.indexOf(s)>=0||s.indexOf(t)>=0); });
      });
      if(exact.length){ hitJob=exact[0]; via='llm'; reason=m.reason||'大模型判定为同类校招岗位'; }
      else {
        // 只用「3 字以上」的专属组合词兜底，避免「运营 / 管理 / 业务」这类泛词误伤。
        const kw=pool.filter(function(t){
          return (m.terms||[]).some(function(k){ return k&&k.length>=3&&t&&t.indexOf(k)>=0; });
        });
        if(kw.length){ hitJob=kw[0]; via='llm-term'; reason=m.reason||'同属一类校招岗位'; }
      }
    }
    if(!hitJob){
      const t=pool.filter(function(t){
        if(t.indexOf(x)>=0||x.indexOf(t)>=0) return true;
        const a=KW.filter(function(k){return x.indexOf(k)>=0;});
        const b=KW.filter(function(k){return t.indexOf(k)>=0;});
        return a.some(function(k){return b.indexOf(k)>=0;});
      })[0];
      if(t){ hitJob=t; via='literal'; reason='字面 / 关键词相近'; }
    }
    if(hitJob) out.push({intent:x, job:hitJob, via:via, reason:reason});
  });
  return out;
}

function e(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function short(s,n){ s=String(s||''); return s.length>n ? s.slice(0,n)+'…' : s; }
function stateLabel(s){ return s==='hit'?'明确命中':s==='inferred'?'推断待核实':'不对口'; }
function groupTitle(s){
  if(s==='hit') return ['t1','优先去 · 有真实岗位证据'];
  if(s==='inferred') return ['t2','按行业推断 · 现场问一句确认'];
  return ['t3','暂不优先'];
}

/* ---------- 采集端：读学生当前画像 ---------- */
function getProfile(){
  let p=null;
  try { p=JSON.parse(localStorage.getItem('fair_profile')||'null'); } catch(err){}
  const a=(p&&p.answers)||{}, f=(p&&p.fair)||{}, id=(p&&p.identity)||{};
  let w=a.tri_weights;
  if(!w||!Object.keys(w).length) w=W0;
  if(wOverride) w=wOverride;
  return {
    major:id.major||D.profile.major||'管理科学与工程',
    intended_jobs:(a.intended_jobs&&a.intended_jobs.length)?a.intended_jobs:(D.profile.intended_jobs||[]),
    home_city:f.home_city||a.home_city||D.profile.home_city||'南京',
    region:a.region||D.profile.region||'南京',
    weights:w,
    city_rank:a.city_rank||RULES.city_rank_def,
    company_type_rank:a.company_type_rank||RULES.ct_rank_def,
    industry_rank:a.industry_rank||RULES.ind_rank_def,
    sem:D.intent_semantics||{},
    fair_id:(p&&p.fair_id)||D.active_fair_id||'（尚未生成画像，用默认画像演示）',
    is_live:!!(p||D.active_fair_id),
    is_session:!!p,
    is_server:(!p&&!!D.active_fair_id),
    saved_to_db:!!(p&&p.saved_to_db),
    db_fair_id:(p&&p.saved_to_db&&p.saved_to_db.fair_id)||(D.active_fair_id||'')
  };
}

/* ---------- 匹配端：按当前画像实时重算 ---------- */
function calcOne(r,P){
  const w=P.weights, vals={};
  /* 城市：遍历全部工作城市，命中多类/多城取用户排名最靠前（最高分） */
  const wcs=workCities(r);
  const cr=P.city_rank||RULES.city_rank_def;
  let best=null;
  wcs.forEach(function(city){
    cityCategories(city).forEach(function(cat){
      if(cr[cat]!=null && (best===null||cr[cat]<best.rank)) best={cat:cat,rank:cr[cat],city:city};
    });
  });
  if(best!==null){
    const v=rankValue(best.rank, RULES.city_cats.length);
    vals.city={v:v, b:'工作城市 '+best.city+' 命中「'+best.cat+'」(你的第'+best.rank+'优先)'};
  }
  /* 离家近：遍历全部工作城市，取离家最近（得分最高）的那个工作地 */
  const home=P.home_city, wcsH=workCities(r);
  if(home){
    let bestH=null;
    wcsH.forEach(function(cc){
      const hp=RULES.PM[cc], hhp=RULES.PM[home];
      let v;
      if(cc===home) v=1.0;
      else if(hp&&hhp&&hp===hhp) v=0.8;
      else if(hhp&&hp&&RULES.adj[hhp]&&RULES.adj[hhp].indexOf(hp)>=0) v=0.6;
      else v=0.3;
      if(bestH===null||v>bestH.v) bestH={v:v, city:cc};
    });
    if(bestH!==null){
      vals.home={v:bestH.v, b:'离家最近的工作地 '+bestH.city+' / 家乡 '+home};
    }
  }
  /* 企业类型：国央企 / 民企 / 外企 三档排名 */
  const b=companyTypeBucket(r.nature);
  const ctr=P.company_type_rank||RULES.ct_rank_def;
  if(b&&ctr[b]!=null){
    const v=rankValue(ctr[b], Object.keys(RULES.ct_buckets).length);
    vals.company_type={v:v, b:'性质 '+r.nature+' → '+b+'(你的第'+ctr[b]+'优先)'};
  }
  /* 行业：对口是硬约束，这里只做排序偏好；不在列表内给中性地板分 */
  const ind=r.industry, ir=P.industry_rank||RULES.ind_rank_def;
  const nind=Object.keys(RULES.ind_rank_def).length;
  if(ind&&ir[ind]!=null){
    const v=rankValue(ir[ind], nind);
    vals.industry={v:v, b:'行业 '+ind+'(你的第'+ir[ind]+'优先)'};
  } else {
    vals.industry={v:0.4, b:'行业 '+(ind||'未知')+'（不在你的偏好排序内）'};
  }
  /* 规模：客观分档，不问用户 */
  const st=r.scale_tier||classifyScale(r.nature, r.name, r.industry);
  if(st&&RULES.scale_tiers[st]!=null){
    vals.scale={v:RULES.scale_tiers[st], b:'规模档 '+st+'（客观判定）'};
  }
  /* 行业地位：读 v4.2 rank_tier（500强/行业龙头等）。无公开榜单给中性地板，不惩罚 */
  const rt=(r.v42&&r.v42.rank_tier)||'';
  if(RULES.industry_status_tiers[rt]!=null){
    vals.industry_status={v:RULES.industry_status_tiers[rt],
      b:'行业地位 '+rt+'（第三方榜单/500强）'};
  } else {
    vals.industry_status={v:RULES.industry_status_floor, b:'无公开榜单（中性，不扣分）'};
  }
  /* 成长性：读 v4.2 growth_tier。无信号给中性地板 */
  const gt=(r.v42&&r.v42.growth_tier)||'none';
  const gv=(RULES.growth_tiers[gt]!=null)?RULES.growth_tiers[gt]:RULES.growth_floor;
  vals.growth={v:gv, b:'成长性 '+gt};

  let pool=0; Object.keys(vals).forEach(function(k){ pool+=(Number(w[k])||0); });
  pool=pool||1;
  const total=Object.keys(w).reduce(function(n,k){return n+(Number(w[k])||0);},0)||100;
  const parts=Object.keys(vals).map(function(k){
    const ww=Number(w[k])||0;
    return {id:k, name:DIM_LABEL[k]||k, weight:ww, value:+vals[k].v.toFixed(2),
            contribution:+(ww/pool*vals[k].v*100).toFixed(1), basis:vals[k].b};
  });
  const score=+parts.reduce(function(n,p){return n+p.contribution;},0).toFixed(1);

  /* 现场提问按当前权重 × 缺口可信度重排 */
  const qs=[];
  if(r.state==='inferred'&&r.infer_notice)
    qs.push({q:'【显著提示】'+r.infer_notice, priority:999, infer:true});
  (r.gaps||[]).forEach(function(g){
    const wk=WMAP[g.kind]||'industry';
    qs.push({q:g.question, priority:+(((Number(w[wk])||10))*(1-g.confidence)).toFixed(1),
             infer:false, why:g.why});
  });
  qs.sort(function(a,b){return b.priority-a.priority;});

  /* 学生勾选的意向岗位 ↔ 企业真实岗位名：大模型语义判定（未覆盖时退回字面匹配） */
  const matches=matchIntent(r.matched_jobs,P.intended_jobs,P.sem,r.job_titles);
  return {score:score, effective_weight:+(pool/total*100).toFixed(1),
          parts:parts, questions:qs.slice(0,2),
          intent_hit:matches.map(function(m){return m.intent;}),
          intent_matches:matches};
}

function recompute(P){
  const rows=BASE.map(function(r){
    const c=calcOne(r,P);
    const x=Object.assign({}, r, c);
    const fb=feedback[r.name];            // v7：现场核实优先于模型推断
    if(fb){
      x.feedback=fb;
      if(fb.result==='yes'){
        if(x.state==='inferred') x.state='hit';
        if(x.trust!=='low') x.trust='high';
        x.check_notes=(x.check_notes||[]).concat(['现场已确认招此岗位，推断升级为明确命中']);
        x.infer_notice='';
        x.questions=(x.questions||[]).filter(function(q){return !q.infer;});
      }
    }
    return x;
  });
  rows.sort(function(a,b){
    const an=(feedback[a.name]||{}).result==='no'?1:0;
    const bn=(feedback[b.name]||{}).result==='no'?1:0;
    return (an-bn) || (ORDER[a.state]-ORDER[b.state]) || (b.priority-a.priority)
        || (b.score-a.score) || a.name.localeCompare(b.name);
  });
  rows.forEach(function(r,i){
    r.rank=i+1;
    r.next=rows[i+1]?rows[i+1].name:'';
    r.rank_reason='队列第 '+(i+1)+' 位：'
      +({hit:'有真实 JD 命中',inferred:'按行业推断待核实',miss:'无对口岗位'})[r.state]
      +' / 优先级 '+r.priority+'；排序分 '+r.score+'（仅 '+r.effective_weight+'% 权重生效）';
    const fb=feedback[r.name];
    if(fb&&fb.result==='yes') r.rank_reason+='；现场已确认招，升级为明确命中';
    if(fb&&fb.result==='no') r.rank_reason+='；现场确认不招，已顺延（保留在队尾供核对）';
  });
  return rows;
}

/* ---------- 报告内容（复用原 CSS 类） ---------- */
function trustBadge(r){
  if(r.trust==='low') return '<span style="color:#c0392b;font-weight:650">⚠ 低可信</span>';
  if(r.trust==='medium') return '<span style="color:#b7791f">◐ 待核实</span>';
  return '<span style="color:#0f9960">✓ 与企业端一致</span>';
}
function fbBadge(r){
  const fb=feedback[r.name];
  if(!fb) return '';
  return fb.result==='yes'
    ? '<span style="color:#0f9960;font-weight:650">✓ 现场确认招（已升级）</span>'
    : '<span style="color:#c0392b;font-weight:650">✗ 现场确认不招（已顺延）</span>';
}
function rowHTML(r,idx,P){
  const ev=r.evidence, parts=r.parts||[];
  const gapRows=(r.gaps||[]).map(function(g){
    return '<div class="job"><div class="job-h"><b>'+e(g.why)+'</b>'
      +'<span>可信度 '+g.confidence+'</span></div>'
      +'<div class="job-req">建议问：'+e(g.question)+'</div></div>';
  }).join('')||'<div class="job-empty">无缺口</div>';
  const partRows=parts.map(function(p){
    return '<tr><td>'+e(p.name)+'</td><td>'+p.weight+' 分</td><td>'+p.value+'</td>'
      +'<td>+'+p.contribution+'</td><td>'+e(short(p.basis,42))+'</td></tr>';
  }).join('')||'<tr><td colspan="5">该企业没有可计算维度</td></tr>';
  const qRows=(r.questions||[]).map(function(q,i){
    return '<div class="job"><div class="job-h"><b>第 '+(i+1)+' 问'+(q.infer?' · 显著提示':'')+'</b>'
      +'<span>优先级 '+q.priority+'</span></div><div class="job-req">'+e(q.q)+'</div>'
      +(q.why?'<div class="job-req">起因：'+e(q.why)+'</div>':'')+'</div>';
  }).join('')||'<div class="job-empty">无需现场核实</div>';
  const notes=(r.check_notes||[]).map(function(n){return '<div class="job-req">· '+e(n)+'</div>';}).join('');
  const notice=r.infer_notice
    ? '<div style="margin:8px 0;padding:8px 10px;background:#fdecea;border-left:3px solid #c0392b;'
      +'color:#c0392b;font-size:13px;font-weight:650;border-radius:0 5px 5px 0">⚠ '
      +e(r.infer_notice)+'</div>' : '';
  const intentLine=(r.intent_matches&&r.intent_matches.length)
    ? '<span class="hits">岗位语义命中：'+e(r.intent_matches.map(function(m){
        return m.job+' ↔ 你选的「'+m.intent+'」'
          +(m.via==='literal'?'（字面近似）':'（大模型判定：'+m.reason+'）');
      }).join('；'))+'</span>'
    : ((r.intent_hit&&r.intent_hit.length)
        ? '<span class="hits">你勾选的岗位命中：'+e(r.intent_hit.join('、'))+'</span>' : '');

  /* v4.2 企业画像：行业地位(多榜) / 成长 / 细分领域 / 4 列岗位来源。
     复用现有 .job-req 样式，只往折叠区追加内容，不改页面骨架与布局。 */
  const v=r.v42||{};
  const vhits=(v.rank_hits||[]).map(function(h){
    return '<div class="job-req">· '+e(h.list||'')+(h.year?'（'+e(h.year)+'）':'')
      +' 第 '+e(h.rank==null?'—':h.rank)+' 名 · '
      +e(h.rank_dim==='industry'?'行业榜':'综合规模榜')
      +(h.rank_tier?' / '+e(h.rank_tier):'')
      +(h.group_scope==='group'?' · 集团口径（非本主体）':'')
      +(h.source_url?' · <a href="'+e(h.source_url)+'" target="_blank" rel="noopener">来源</a>':'')
      +'</div>';
  }).join('');
  const vjs=v.job_sources||{};
  const srcLine='校招官网 '+((vjs.official||[]).length)
    +' / 招聘软件 '+((vjs.platform||[]).length)
    +' / 联网兜底 '+((vjs.websearch||[]).length)
    +' / 并集 '+((vjs.total||[]).length);
  const v42Block=Object.keys(v).length
    ? '<div class="job-req"><b>企业画像（v4.2 · 仅展示，不参与打分）</b></div>'
      +'<div class="job-req">行业地位：'+e(v.industry_rank_label||'未查')
        +(v.rank_dim?' · '+e(v.rank_dim==='industry'?'行业榜主榜':'综合规模榜主榜'):'')
        +(v.rank_tier?' / '+e(v.rank_tier):'')+'</div>'
      +(vhits||'<div class="job-req">· 无榜单条目（不代表不是龙头）</div>')
      +'<div class="job-req">成长：'+e(v.growth_tier||'—')
        +' · 细分领域：'+e(short(v.segment||'—',70))
        +(v.segment_leader?' · 细分领先 '+e(v.segment_leader)
          +(v.segment_leader==='self_claim'?'（企业自述，不作认定）':''):'')
        +'</div>'
      +'<div class="job-req">岗位来源（三步流程保留）：'+srcLine
        +' · 覆盖 '+e(v.job_coverage||'—')+'</div>'
    : '';

  return '<div class="row" id="tri-'+idx+'"'+(visitedSet[r.name]?' style="opacity:.45"':'')+'>'
   + '<div class="rowhead"><div class="nm"><b>'+e(r.name)+'</b> '
   +   trustBadge(r) + ' ' + fbBadge(r)
   +   '<span class="meta">展位 '+e(r.booth||'—')+' · '+e(r.industry)+' · '+e(r.nature)
   +     ' · '+e(short(r.city,14))+' · '+r.job_count+' 条岗位记录 · '+stateLabel(r.state)
   +     ' / 优先级 '+r.priority+'</span>'
   +   '<span class="line">'+e(r.rank_reason)+'</span>'
   +   (r.tour_pos?'<span class="line">会场动线第 '+r.tour_pos+' 站 · 推荐序第 '+r.rank
        +' 位（匹配分 '+r.score+' 照常显示）</span>':'')
   +   '<span class="line">来源 '+e(short(ev.source,54))+' · '+e(ev.date)+' · 证据等级 '+e(ev.jobSource)+'</span>'
   +   (r.hits.length?'<span class="hits">命中岗位：'+e(r.hits.join('、'))+'</span>':'')
   +   intentLine
   +   (r.misses.length?'<span class="misses">未对口：'+e(short(r.misses.join('、'),70))+'</span>':'')
   +   (r.reason?'<span class="line">对口理由：'+e(short(r.reason,90))+'</span>':'')
   + '</div><div class="sc">'+r.score+'<em>生效权重 '+r.effective_weight+'%</em></div></div>'
   + notice
   + '<div class="calc"><b>算分依据（五项 100 · 按你当前画像实时重算）</b>'
   +   '<div class="calc-sub">实际生效 '+r.effective_weight+'% 权重；未采到的维度不计分、也绝不给最低档。'
   +     '分值只用于排序，不代表企业好坏，不与生效权重不同的企业直接可比。</div>'
   +   '<table style="width:100%;border-collapse:collapse;font-size:11.5px;margin-top:6px">'
   +   '<tr style="color:#7b8492"><td>维度</td><td>你的权重</td><td>档位值</td><td>贡献</td><td>依据</td></tr>'
   +   partRows+'</table></div>'
   + '<div class="jobbox"><details><summary>信息缺口 · 现场提问 · 事实校验 · 证据溯源（'+r.gaps.length+' 项缺口）</summary>'
   +   '<div class="job-req"><b>建议现场问（按当前权重 × 缺口可信度实时取前 2）</b></div>'+qRows
   +   '<div class="job-req"><b>信息缺口清单</b></div>'+gapRows
   +   '<div class="job-req"><b>企业端事实校验 '+trustBadge(r)+'</b></div>'+notes
   +   ((r.guard&&r.guard.note)?'<div class="job-req" style="color:#c0392b"><b>幻觉拦截</b>：'
        +e(r.guard.note)+'</div>':'')
   +   '<div class="job-req"><b>证据溯源</b>：'+e(ev.source||'无来源 URL')+' · '+e(ev.date)
   +     ' · 岗位证据 '+e(short(ev.jobEvidence,60))+'</div>'
   +   v42Block
   +   (r.nearby&&r.nearby.length?'<div class="job-req"><b>顺路动线</b>：展位相邻 '
   +     +e(r.nearby.join('、'))+'，路过可一起问</div>':'')
   +   '<div class="btns" style="margin-top:8px">'
   +     '<button type="button" class="btn btn-g" data-fb="no" data-name="'+e(r.name)+'">问一句：不招（顺延 + 回流）</button>'
   +     '<button type="button" class="btn btn-p" data-fb="yes" data-name="'+e(r.name)+'">问一句：招（升级 + 回流）</button>'
   +     (r.next?'<button type="button" class="btn btn-g" data-name="'+e(r.name)+'" data-next="'+e(r.next)+'">顺延至 '+e(short(r.next,14))+'</button>':'')
   +     '<button type="button" class="btn btn-p" data-name="'+e(r.name)+'" data-done="'+idx+'">聊完了，指针前移</button>'
   +   '</div></details></div></div>';
}

/* v11 列表页：每家企业最多两行说明——亮点 + 命中用户关注的标准；
   完整细节不删，放到另一个页面（?detail=企业名）看。 */
function rowBrief(r, idx, P){
  const v=r.v42||{}, sm=r.intent_matches||[];
  const top=(r.parts||[]).slice().sort(function(a,b){return b.contribution-a.contribution;})
    .slice(0,2).map(function(p){return p.name+' '+p.value;});
  const bright=[];
  if(v.industry_rank_label==='榜单在列') bright.push('榜单在列'+(v.rank_tier?' · '+v.rank_tier:''));
  if(v.growth_tier==='rising_star') bright.push('行业新星');
  const line1='展位 '+e(r.booth||'—')+' · '+e(r.industry)+' · '+e(r.nature)
    +' · '+e(short(r.city,10))
    +(bright.length?' · <b>'+e(bright.join(' / '))+'</b>':'')
    +' · '+e(stateLabel(r.state));
  const hit=sm.slice(0,2).map(function(m){return m.job+' ↔ '+m.intent;});
  const focus=top.concat(hit.length?['岗位命中：'+hit.join('；')]:[]);
  const line2=focus.length?e(focus.join(' · ')):'按当前画像计算，暂无特别突出的命中项';
  return '<div class="row" id="tri-'+idx+'">'
   + '<div class="rowhead"><div class="nm"><b>'+e(r.name)+'</b> '+trustBadge(r)+'</div>'
   +   '<div class="sc">'+r.score+'<em>生效权重 '+r.effective_weight+'%</em></div></div>'
   + '<div class="line">'+line1+'</div>'
   + '<div class="line">'+line2+'</div>'
   + '<div class="btns" style="margin-top:6px">'
   +   '<button type="button" class="btn btn-p" data-detail="'+e(r.name)+'">查看完整报告 →</button>'
   + '</div></div>';
}

function render(){
  const box=document.getElementById('mres');
  if(!box) return;
  const P=getProfile();
  const rows=recompute(P).filter(function(r){
    if(filter==='high') return r.trust==='high';
    if(filter==='infer') return r.state==='inferred';
    if(filter==='low') return r.trust==='low';
    return true;
  });
  if(sortMode==='booth'){
    rows.sort(function(a,b){
      return String(a.booth||'zzz').localeCompare(String(b.booth||'zzz'),'zh',{numeric:true});
    });
    // 导逛模式下补站位，避免卡片仍写「队列第 N 位」与实际位置不符。
    rows.forEach(function(r,i){ r.tour_pos=i+1; });
  }
  const fbKeys=Object.keys(feedback);
  const fbYes=fbKeys.filter(function(k){return feedback[k].result==='yes';}).length;
  const fbNo=fbKeys.length-fbYes;
  const live=P.is_session?'<span style="color:#0f9960">● 已接入本次采集画像 '+e(P.fair_id)+'</span>'
                      :(P.is_server?'<span style="color:#0f9960">● 正在使用服务端最近一次采集的画像 '+e(P.fair_id)+'（本标签页未走采集流程，结果已按该画像算好）</span>'
                      :'<span style="color:#b7791f">○ 尚未生成画像，正在用默认画像演示</span>');
  const _infoHtml='<div class="note" style="background:#eff4ff;border:1px solid #d6e4ff;border-radius:8px;'
    +'padding:9px 12px;margin:0 0 14px;font-size:12px;line-height:1.8">'
    +'<b>三端协调报告 · v7 现场回流 / 展位动线</b>　'+live+'<br>'
    +'写库：'+(P.saved_to_db
        ? '✓ 本次画像已写入 careersafari.db（'+e(P.db_fair_id)+'），匹配端已按它重建'
        : (P.db_fair_id ? '库内生效画像 '+e(P.db_fair_id)+'；本次会话尚未提交新画像'
                        : '离线模式：点下载 JSON 后运行 fair_ingest.py --file 即可入库'))+'<br>'
    +'采集端：'+e(P.major)+' / 勾选岗位 '+e((P.intended_jobs||[]).join('、')||'—')
      +' / 家乡 '+e(P.home_city)+' / 期望城市 '+e(P.region)
      +' / 权重 '+Object.keys(P.weights).map(function(k){return k+':'+P.weights[k];}).join('、')+'<br>'
    +'企业端：'+e(D.sources['企业端'])+'<br>匹配端：'+e(D.sources['匹配端'])
      +'；结论已与企业端 JD / 行业做事实校验<br>'
    +'共 '+D.stats.companies+' 家 / '+D.stats.jobs+' 条岗位；明确命中 '+D.stats.states.hit
      +' · 推断 '+D.stats.states.inferred+' · 不对口 '+D.stats.states.miss
      +'；校验 高 '+(D.stats.trust&&D.stats.trust.high||0)+' / 中 '
      +(D.stats.trust&&D.stats.trust.medium||0)+' / 低 '+(D.stats.trust&&D.stats.trust.low||0)
      +'；已拦截可疑岗位名 '+(D.stats.masked_job_names||0)+' 个、证据等级纠错 '
      +(D.stats.state_fixes||0)+' 家（企业照常排序，不删功能）<br>'
    +'现场回流：'+e(D.sources['现场回流']||'fair_feedback 表')
      +'；本次已核实 '+fbKeys.length+' 家（招 '+fbYes+' / 不招 '+fbNo+'），'
      +'不招的排到队尾但不删除<br>'
    +'<b>改了画像或权重，这份报告会自动重算</b>（分数、算分依据、现场提问、队列位次与顺延目标）。<br>'
    +'<b>现场进度</b>：已逛 '+visited+' 家 · 剩余 '+remainMin+' 分钟 · 队列还有 '
      +Math.max(0,rows.length-visited)+' 家待看。点卡片里的按钮会同步推进。'
    +'</div>'
    +'<div class="note" style="font-size:12px;margin:0 0 10px">'
    +'<b>五项权重微调</b>（改完立即重算，不必回采集端重新生成画像）：'
    + Object.keys(P.weights).map(function(k){
        return '<span style="display:inline-block;margin:0 10px 0 0">'
          +e(DIM_LABEL[k]||k)+' <b>'+P.weights[k]+'</b> '
          +'<button type="button" class="btn btn-g" data-wk="'+k+'" data-wd="-5">−5</button> '
          +'<button type="button" class="btn btn-g" data-wk="'+k+'" data-wd="5">+5</button></span>';
      }).join('')
    + ' <button type="button" class="btn btn-g" data-wreset="1">恢复画像权重</button></div>'
    +'<div class="btns" style="margin:0 0 12px">'
    + [['all','全部'],['high','只看高可信'],['infer','只看推断待核实'],['low','只看校验异常']]
        .map(function(x){return '<button type="button" class="btn '+(filter===x[0]?'btn-p':'btn-g')
          +'" data-filter="'+x[0]+'">'+x[1]+'</button>';}).join('')
    + '<button type="button" class="btn btn-g" data-export="1">导出当前队列 JSON</button>'
    + '</div>';

  /* v11：列表页默认只给「按排名 / 按展位排行」两个显示选项。
     原有统计说明、权重微调、筛选、导出一条不删，收进折叠区；
     点企业卡片进另一个页面看完整报告。 */
  let html='';
  if(DETAIL_NAME){
    const t=rows.findIndex(function(r){return r.name===DETAIL_NAME;});
    if(t<0){
      html='<div class="note">没找到企业：'+e(DETAIL_NAME)
        +'。<a href="'+e(location.pathname)+'?report=1">返回排行列表</a></div>';
    }else{
      const cur=rows[t], prevT=rows[t-1], nextT=rows[t+1];
      /* 这条页是「单家企业完整报告」钻取页（由列表「查看完整报告 →」在新标签打开）。
         之前只显示 1 家 +「推荐序第 N 位」，容易被误认为排名塌了。
         这里明确标注「共 M 家 / 当前第 N 位」、加醒目返回、并给上一家/下一家导航。 */
      html='<div class="btns" style="margin:0 0 10px">'
        + '<a class="btn btn-p" href="'+e(location.pathname)+'?report=1">← 返回完整排行列表（共 '
        + rows.length+' 家）</a></div>'
        + '<div class="note" style="font-size:12.5px;margin:0 0 12px;background:#fff7e6;'
        + 'border:1px solid #ffe0a3;border-radius:8px;padding:9px 12px;line-height:1.7">'
        + '⚠ 这是<b>单家企业</b>的完整报告钻取页——列表里一共有 <b>'+rows.length
        + '</b> 家，这里只展开你点的那 1 家（当前推荐序第 <b>'+cur.rank+'</b> 位）。'
        + '排名没有少，点上面「← 返回」即可看全部。</div>'
        + '<div class="btns" style="margin:0 0 12px">'
        + (prevT?'<a class="btn btn-g" href="'+e(location.pathname)
          + '?detail='+encodeURIComponent(prevT.name)+'">← 上一家（第 '+prevT.rank+' 位）</a> ':'')
        + (nextT?'<a class="btn btn-g" href="'+e(location.pathname)
          + '?detail='+encodeURIComponent(nextT.name)+'">下一家（第 '+nextT.rank+' 位）→</a>':'')
        + '</div>'
        + '<div class="note" style="font-size:12px;margin:0 0 12px"><b>完整报告 · '
        + e(cur.name)+'</b>　展位 '+e(cur.booth||'—')
        + ' · 推荐序第 '+cur.rank+' 位 / 共 '+rows.length
        + ' 位 · 排序分 '+cur.score+'　'+live+'</div>'
        + rowHTML(cur, t, P)
        + '<details style="margin:12px 0"><summary style="cursor:pointer;font-size:12px">'
        + '本页完整信息（统计 / 权重微调 / 筛选 / 导出 / 现场进度）</summary>'
        + _infoHtml + '</details>';
    }
  }else{
    html='<div class="note" style="font-size:12px;margin:0 0 10px"><b>共 '
      + D.stats.companies+' 家 / '+D.stats.jobs+' 条岗位</b>　'+live
      + ' · 明确命中 '+D.stats.states.hit+' / 推断 '+D.stats.states.inferred
      + ' / 不对口 '+D.stats.states.miss
      + ' · 意向岗位 '+e((P.intended_jobs||[]).join('、')||'—')+'</div>'
      + '<div class="btns" style="margin:0 0 12px">'
      + '<button type="button" class="btn '+(sortMode==='queue'?'btn-p':'btn-g')
        +'" data-sort="queue">按排名</button>'
      + '<button type="button" class="btn '+(sortMode==='booth'?'btn-p':'btn-g')
        +'" data-sort="booth">按展位排行</button>'
      + '</div>'
      + '<details style="margin:0 0 12px"><summary style="cursor:pointer;font-size:12px">'
      + '更多设置 / 完整信息（统计 · 权重微调 · 筛选 · 导出 · 现场进度）</summary>'
      + _infoHtml + '</details>';
  }
  let idx=0;
  const groups = sortMode==='booth'
    ? [['t1','按会场序列号（展位号）顺序 · 走一遍不折返 · 评分照常显示', rows]]
    : ['hit','inferred','miss'].map(function(s){
        const g=groupTitle(s); return [g[0],g[1],rows.filter(function(r){return r.state===s;})];
      });
  if(!DETAIL_NAME){
    groups.forEach(function(g){
      const rs=g[2];
      if(!rs.length) return;
      html+='<div class="tier '+g[0]+'"><div class="tier-h">'+g[1]+' <em>'+rs.length+' 家</em></div>';
      rs.forEach(function(r){ html+=rowBrief(r,idx++,P); });
      html+='</div>';
    });
  }
  currentRows=rows;
  suppress=true;
  box.innerHTML=html;
  // 详情页本来就是看细节的，折叠区默认展开。
  if(DETAIL_NAME) box.querySelectorAll('.jobbox details').forEach(function(d){ d.open=true; });
  setTimeout(function(){suppress=false;},60);
  box.querySelectorAll('[data-wk]').forEach(function(b){
    b.onclick=function(){
      if(!wOverride) wOverride=Object.assign({},P.weights);
      const k=b.dataset.wk, d=+b.dataset.wd;
      wOverride[k]=Math.max(0,(Number(wOverride[k])||0)+d);
      const sum=Object.keys(wOverride).reduce(function(n,x){return n+wOverride[x];},0)||1;
      Object.keys(wOverride).forEach(function(x){ wOverride[x]=+(wOverride[x]/sum*100).toFixed(1); });
      render();
    };
  });
  box.querySelectorAll('[data-wreset]').forEach(function(b){
    b.onclick=function(){ wOverride=null; render(); };
  });
  box.querySelectorAll('[data-filter]').forEach(function(b){
    b.onclick=function(){ filter=b.dataset.filter; render(); };
  });
  box.querySelectorAll('[data-sort]').forEach(function(b){
    b.onclick=function(){ sortMode=b.dataset.sort||'queue'; render(); };
  });
  box.querySelectorAll('[data-detail]').forEach(function(b){
    b.onclick=function(){
      window.open(location.pathname+'?detail='+encodeURIComponent(b.dataset.detail),'_blank');
    };
  });
  box.querySelectorAll('[data-fb]').forEach(function(b){
    b.onclick=function(){ saveFeedback(b.dataset.name, b.dataset.fb); };
  });
  box.querySelectorAll('[data-export]').forEach(function(b){
    b.onclick=function(){
      const out={generated_at:new Date().toISOString(), fair_id:P.fair_id,
                 weights:P.weights, sort_mode:sortMode,
                 rows:currentRows.map(function(r){
                   return {rank:r.rank,name:r.name,booth:r.booth,state:r.state,
                           trust:r.trust,score:r.score,effective_weight:r.effective_weight,
                           matched_jobs:r.matched_jobs,questions:r.questions,
                           feedback:feedback[r.name]||null};
                 })};
      try{
        const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
        const a=document.createElement('a');
        a.href=URL.createObjectURL(blob); a.download='双选会现场队列.json'; a.click();
      }catch(err){ console.log('导出数据（当前环境不支持下载）',out); }
    };
  });
  box.querySelectorAll('[data-next]').forEach(function(b){
    b.onclick=function(){
      const t=rows.findIndex(function(r){return r.name===b.dataset.next;});
      const el=document.getElementById('tri-'+t);
      if(el){ el.scrollIntoView({behavior:'smooth',block:'start'}); el.style.outline='2px solid #2563eb'; }
      const cur=b.closest('.row'); if(cur) cur.style.opacity='.45';
      visitedSet[b.dataset.name||'']=1;
      visited++; remainMin=Math.max(0,remainMin-1); render();
    };
  });
  box.querySelectorAll('[data-done]').forEach(function(b){
    b.onclick=function(){
      const cur=b.closest('.row'); if(cur) cur.style.opacity='.45';
      visitedSet[b.dataset.name||'']=1;
      visited++; remainMin=Math.max(0,remainMin-8); render();
    };
  });
  injected=true;
}

function schedule(){ if(suppress) return; clearTimeout(timer); timer=setTimeout(render,700); }

/* #mres 出现后接管内容；同时持续监听采集端画像变化 */
let wait;
wait=setInterval(function(){
  const box=document.getElementById('mres');
  if(!box) return;
  clearInterval(wait);
  new MutationObserver(schedule).observe(box,{childList:true,subtree:true});
  setTimeout(function(){ if(!injected && !box.innerHTML.trim()) render(); },2500);
  refreshFromServer();          // v6：服务模式下先取库里最新画像重建过的报告
  loadFeedback();               // v7：取回现场核实结果（离线读 localStorage）
  lastSig=JSON.stringify(getProfile());
  setInterval(function(){
    const sig=JSON.stringify(getProfile());
    if(sig!==lastSig){ lastSig=sig; render(); }
  },1200);
},200);
})();
</script>
"""
