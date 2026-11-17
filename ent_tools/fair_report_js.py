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
// ORDER 已停用：名次只按排序分，岗位状态不再参与比较（见 recompute）
const WMAP = {岗位:'industry', 行业:'industry', 职责:'scale', 阶段:'scale',
              薪资:'scale', 人数:'scale', 城市:'city', 性质:'company_type',
              作息:'city', 证据:'scale'};
let injected=false, suppress=false, timer=null, lastSig='', filter='all';
let wOverride=null, visited=0, remainMin=210, currentRows=[], visitedSet={};
let feedback={}, sortMode='queue';          // v7：现场核实回流 + 展位动线排序
/* v11：完整报告是另一个页面（新标签 fair.html?detail=企业名）；列表页只显示两行摘要 */
const DETAIL_NAME = new URLSearchParams(location.search).get('detail') || '';
const DIM_LABEL={city:'城市条件',home:'离家近',company_type:'企业类型',
                 industry:'行业',scale:'企业规模档',
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
  const ok=function(x){ return x && String(x).trim() && String(x).trim()!=='未知'; };
  if(w&&w.length){
    const f=w.filter(ok);
    if(f.length) return f;
  }
  const c=r&&r.city;
  return ok(c)?[String(c).trim()]:[];
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
function showComputing(p){
  var el=document.getElementById('recompute-note');
  if(!el){
    el=document.createElement('div'); el.id='recompute-note';
    el.style.cssText='position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:9999;'
      +'background:#1f6feb;color:#fff;padding:9px 16px;border-radius:9px;font-size:13px;'
      +'box-shadow:0 2px 10px rgba(0,0,0,.25);max-width:90vw;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent=(p&&p.note)?p.note:'正在按你的画像重算企业匹配，请稍候……';
}
function hideComputing(){ var el=document.getElementById('recompute-note'); if(el) el.remove(); }

/* ---------- 按需语义补判：payload 没覆盖的意向，现场问一次大模型 ----------
   payload 的 intent_semantics 只覆盖**构建时那份生效画像**的意向。学生换画像 /
   现场新勾一个岗位后，前端拿不到映射，只能退回本地字面规则，报告里如实写
   「本地规则判定」。这里把缺的意向发给服务端补判（每个意向 3–10 秒），拿到后
   并进 D.intent_semantics 再重渲染。离线 / 服务不可用 / 模型超时 → 保持本地兜底，
   不阻塞页面、不编结论。试过的意向记在 semTried 里，避免同一个意向反复打接口。 */
let semTried={}, semNoteTimer=null;
/* 语义服务基址。http(s) 页面一律同源取数（哪个端口就请求哪个端口，
   多实例 8102/8103 各自正确）；只有 file:// 离线打开才回退本机 8102，
   服务端已开 Access-Control-Allow-Origin: *。 */
const SEM_BASE=(function(){
  try{
    if(/^https?:$/.test(location.protocol)) return '';
  }catch(e){}
  return 'http://127.0.0.1:8102';
})();
/* 判定结果在本机存一份：换画像切回来、刷新页面都不用再等模型。 */
const SEM_LS='fair_sem_v1:';
function semLoad(x){
  try{
    const s=localStorage.getItem(SEM_LS+x);
    if(!s) return null;
    const v=JSON.parse(s);
    return (v && v.via==='llm' && (v.titles||[]).length) ? v : null;
  }catch(e){ return null; }
}
function semStore(x,v){ try{ localStorage.setItem(SEM_LS+x, JSON.stringify(v)); }catch(e){} }
function semMissing(intents){
  const out=[];
  (intents||[]).forEach(function(x){
    if(!x) return;
    const m=(D.intent_semantics||{})[x];
    if(m && m.via==='llm') return;
    const ls=semLoad(x);
    if(ls){ D.intent_semantics=D.intent_semantics||{}; D.intent_semantics[x]=ls; return; }
    if(semTried[x]) return;
    out.push(x);
  });
  return out;
}
function semNote(txt){
  let el=document.getElementById('sem-note');
  if(!txt){ if(el) el.remove(); return; }
  if(!el){
    el=document.createElement('div'); el.id='sem-note';
    el.style.cssText='position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:9998;'
      +'background:#0f766e;color:#fff;padding:7px 14px;border-radius:9px;font-size:12.5px;'
      +'box-shadow:0 2px 10px rgba(0,0,0,.2);max-width:90vw;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent=txt;
  clearTimeout(semNoteTimer);
  semNoteTimer=setTimeout(function(){ const n=document.getElementById('sem-note'); if(n) n.remove(); },9000);
}
function ensureSemantics(intents){
  const miss=semMissing(intents);
  if(!miss.length) return;
  miss.forEach(function(x){ semTried[x]=1; });
  semNote('正在让大模型判定：'+miss.join('、')+'（每个约 3–10 秒）');
  function once(isRetry){
    fetch(SEM_BASE+'/api/fair/semantics',
          {method:'POST',headers:{'Content-Type':'application/json'},
           body:JSON.stringify({intents:miss})})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
      .then(function(j){
        const got=(j&&j.map)||{}; let n=0, names=[];
        Object.keys(got).forEach(function(k){
          if(got[k]&&(got[k].titles||[]).length){
            D.intent_semantics=D.intent_semantics||{};
            D.intent_semantics[k]=got[k]; semStore(k,got[k]); n++; names.push(k);
          }
        });
        if(n){ semNote('大模型已判定：'+names.join('、')); lastSig=''; render(); }
        else semNote('这次没问到大模型（'+((j&&j.failed&&j.failed.length)?j.failed.join('、'):'模型不可用')
                     +'），先按本地规则判定');
      })
      .catch(function(){
        if(!isRetry){ setTimeout(function(){ once(true); },900); return; }
        /* 如实说清楚是哪一步断了，别让学生以为是模型判成了这样。 */
        const host=SEM_BASE?SEM_BASE.split('//')[1]:location.host;
        semNote('连不上语义服务 '+host+'：这些意向先按本地规则判定。'
                +'要跑大模型，请在 ent_tools 目录启动 fair_server.py --port 8102');
      });
  }
  once(false);
}
window.__ensureSemantics=ensureSemantics;

/* 命中复核（按需）：页面按本地规则判成命中、但 payload 里没有复核结论的对子
   （现场新填意向 / 不在候选集），成批送服务端复核一次。
   只发「未复核」的对子，结果并进同一张 hit_verdicts；拿不到就保持「未复核」，
   不编结论、不阻塞首屏。服务端同样是「一次调用判一批 + pair 级缓存」。 */
var hvTried={};
function ensureVerify(pairs){
  const todo=[];
  (pairs||[]).forEach(function(p){
    const k=String(p[0])+'||'+String(p[1]);
    if(hvTried[k]) return;
    hvTried[k]=1;
    todo.push([p[0],p[1]]);
  });
  if(!todo.length||!SEM_BASE) return;
  fetch(SEM_BASE+'/api/fair/verify',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pairs:todo})})
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(j){
      const v=(j&&j.verdicts)||{};
      const ks=Object.keys(v);
      if(!ks.length) return;
      D.hit_verdicts=D.hit_verdicts||{};
      ks.forEach(function(k){ D.hit_verdicts[k]=v[k]; });
      lastSig=''; render();
    })
    .catch(function(){ /* 复核失败：保持「未复核」标注，不改判、不编结论 */ });
}

function refreshFromServer(){
  if(!/^https?:$/.test(location.protocol)) return;
  var tries=0;
  function go(){
    fetch('/api/fair/payload').then(function(r){return r.json();}).then(function(p){
      /* 提交画像后服务端正在按新画像重算命中/排除，前端轮询直到算完，
         期间绝不把上一个学生的意向岗位结论当成当前结果展示。 */
      if(p && p.computing){ showComputing(p); if(tries++<45) setTimeout(go,4000); return; }
      hideComputing();
      if(!p || !p.rows || !p.rows.length) return;
      D.rows=p.rows; D.weights=p.weights||D.weights; D.profile=p.profile||D.profile;
      D.stats=p.stats||D.stats; D.sources=p.sources||D.sources;
      /* 提交新画像后服务端会按新的 intended_jobs 重算语义映射；
         这里必须同步刷新，否则学生刚勾的岗位查不到映射、静默退化成字面匹配。 */
      if(p.intent_semantics) D.intent_semantics=p.intent_semantics;
      if(p.hard_filter) D.hard_filter=p.hard_filter;
      if(p.active_fair_id) D.active_fair_id=p.active_fair_id;
      if(p.province_map) PM=p.province_map;
      BASE=D.rows; W0=D.weights;
      lastSig=''; render();
    }).catch(function(){ if(tries++<45) setTimeout(go,4000); });
  }
  go();
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
/* ---- 本地兜底匹配规则：与 ent_tools/intent_semantics.py 同一套（不含大模型）----
   只有 payload 的语义映射没覆盖某个意向时才走这里（换学生、现场新勾岗位、模型不可用）。
   旧版是一张 22 词表做关键词相交，会把「战略管培生」算进「供应链管培生」、
   把「IT 数字化管培生」算进「营销管培生」；这里换成后端同源的确定性规则：
   通用词不算证据、同职能族才算同类、销售型意向才收销售岗、学科必须一致。 */
const MI_GEN=['专员','管培','培生','培训','训生','管理','助理','储干','干部','工程','经理','主管','储备','专工',
  /* 「分析」「研究」不携带方向：策略分析岗 ≠ 分析仪器工程师；
     行业研究岗 ≠ AI算法研究工程师 / 有机合成研究员。 */
  '分析','研究'];
/* 岗位名尾部后缀字：跨词边界的 2-gram 常以它们收尾（理岗 / 源专 / 究员），
   只共享到这种片段时不算同职能证据。与 intent_semantics.BOUNDARY_TAIL 同源。 */
const MI_TAIL='岗员师专士长工部科组队站店厂所司局院校';
/* 岗位职能族表（与 ent_tools/job_family.py 同源，构建时由 Python 侧序列化注入）。
   解决「有职能、无方向」的岗位被兜底放行：UI设计师 / 财务专员 / 产品经理 都没有
   方向词，却和「后端开发工程师」不是同一个职能。族表只回答「是否明确跨职能」，
   认不出来就不表态，把结论交回原有规则。 */
const MI_FUNC=__MI_FUNC__;
const MI_FAM={
  supply:['供应链','物流','采购','寻源','仓储','关务','库存','配送','物资','PMC','备件',
          '生产计划','物料计划','计划专员','计划管培','计划助理','综合计划','计划主管','计划岗'],
  data:['数据分析','商业分析','经营分析','业务分析','策略分析','分析岗','量化','BI','数据科学','统计'],
  hr:['人力资源','人力','人资','人事','招聘','薪酬','员工关系','培训专员','培训岗','培训管培',
      '培训主管','培训经理','培训助理'],
  fin:['财务','会计','审计','税务','核算','出纳','资金'],
  legal:['法务','法律','合规','知识产权','诉讼'],
  mkt:['市场','营销','品牌','推广'],
  media:['新媒体','短视频','直播','内容运营','内容策划','公众号','小红书','抖音','B站',
         '视频号','社媒','社交媒体','文案'],
  quality:['质量','品质','品控','质检','质保','体系','客诉'],
  /* 施工建造族：施工员 / 土建工程 / 项目管理 / 机电管理 都是施工现场的同一批岗，
     字面毫不重合，只能按职能族认。「造价 / 成本」属商务条线，不进这一族。 */
  civil:['施工','土建','市政','总承包','工程管理','项目管理','机电管理','机电安装','安装工程','监理'],
  trade:['外贸','国际贸易','进出口','海外业务','海外销售','国际业务','海外市场',
         '国际市场营销','单证','报关','货代','船代','外销']
};
const MI_SALES=['销售','营销','市场','外贸','业务','客户','渠道','商务','贸易','BD','拓展'];
const MI_SUBJ=['语文','数学','英语','物理','化学','生物','政治','历史','地理','体育','音乐',
               '美术','信息技术','理科','文科','科学','心理健康','道德与法治'];
const MI_GENERIC=['管培生','培训生','管理培训生','职能管培生','运营培训生','储备干部',
                  '校招生','专员','助理','实习生','干部','管培岗'];
/* 大类岗位名：名字里不带业务方向，只说了层级 / 身份。
   「供应链管培生」是「管培生」，但「管培生」不是「供应链管培生」——
   小类可以进大类，大类不能反推小类。制造业企业的管培生不一定按供应链培养。
   与 intent_semantics.is_bare_generic 同源。 */
const MI_BARE=['管培生','管培岗','管理培训生','培训生','校招生','储备干部','储干','干部',
  '职能管培生','专员','助理','实习生','见习生','研究员','分析师','工程师','经理','主管',
  '主任','组长','业务员','职员','员工','顾问','人员','人才'];
/* 不抹 MI_GEN：「培训专员 / 培训管培生」是培训条线的岗位名，
   抹掉「培训 / 管理」后会被当成「只写了大类」的岗位。与 Python 同源。 */
const MI_BAREFILL=MI_BARE.concat(['岗位','岗','人员','类','方向','相关','储备'])
  .sort(function(a,b){ return b.length-a.length; });
/* 算方向串时再抹「管理」，与 intent_semantics._FILLER_EXTRA 逐词一致。
   （miBareGeneric 不抹「管理」，两处词表有意不同，别合并。） */
const MI_DIRFILL=MI_BAREFILL.concat(['管理'])
  .sort(function(a,b){ return b.length-a.length; });
/* 级别词是档位不是方向：「初级测试工程师」的方向就是「测试」。
   与 intent_semantics._dir_text 同源。 */
const MI_DIRLEV=['初级','中级','高级','资深'];
function miDirText(t){
  let x=String(t==null?'':t).replace(/[\s（）()\[\]【】、，,·—\-\/_]+/g,'').toLowerCase();
  if(!x) return x;
  MI_DIRLEV.forEach(function(w){ x=x.split(w).join(''); });
  MI_DIRFILL.forEach(function(w){ x=x.split(w).join(''); });
  return x;
}
/* 岗位名是不是比意向宽一层：「算法工程师」对「AI算法研究工程师」。
   小类能进大类、大类不能反推小类 —— 只在「岗位方向 ⊂ 意向方向」时成立，
   并列方向（机械设计 × 结构设计）不算更宽，保持原口径。
   与 intent_semantics.is_wider_job 同源。 */
function miWider(title,intent){
  const a=miDirText(title), b=miDirText(intent);
  if(!a||!b||a===b) return false;
  /* 学科教师特例：岗位与意向都是「同学科 + 教师」，只差学段/修饰（化学教师 ×
     高中化学教师）就是同一个岗，不算上位类。与 intent_semantics.is_wider_job 同源。 */
  if(miHas(title,MI_TEACH)&&miHas(intent,MI_TEACH)){
    const sa=MI_SUBJ.filter(function(s){ return String(title).indexOf(s)>=0; });
    const sb=MI_SUBJ.filter(function(s){ return String(intent).indexOf(s)>=0; });
    if(sa.length&&sa.length===sb.length&&sa.every(function(s){ return sb.indexOf(s)>=0; })) return false;
  }
  return b.indexOf(a)>=0 && a.length<b.length;
}
/* 并列方向词：同属一个大类、却是两条不同职业路径的限定词。
   换技术栈不算换方向（Java / Python / C++ 不在表内），机械/结构/电气也不在内
   ——「机械设计工程师」和「结构设计工程师」是同一岗位的两种写法。
   与 intent_semantics.DIR_WORDS 同源。 */
const MI_DIRWORDS=['前端','后端','服务端','客户端','嵌入式','上位机','下位机','固件',
  '移动端','桌面','驱动','全栈','低代码','量化','算法','数据','图像','视觉','图形','游戏',
  'unity','unreal','大模型','ai','人工智能','测试','运维','安全','网络','通信',
  '供应链','计划','采购','物流','仓储','商业分析'];
/* 职业序列 / 专业方向互斥词：与 intent_semantics 同源。教师×工程师、分析×环保
   这类「共享字但方向不同」的错配，不能因为裸兜底就算命中。 */
const MI_TEACH=['教师','老师','教学','讲师','班主任','教研','学科教师'];
const MI_PROFSEQ=['工程师','专工','研究员','研发','工艺','技术员','化验','检验',
  '医师','医生','护士','临床','会计','律师','设计师'];
const MI_PROFDIR=['分析','环保','合成','工艺','研发','测试','算法',
  '机械','电气','材料','医药','食品','纺织','环境','安全'];
const MI_GENERIC_SUBJ=['学科教师','各科教师','全科教师','学科老师'];
/* 设计方向族：岗位名都以「设计」结尾，但设计对象不同就是并列方向。
   与 intent_semantics.DESIGN_DIR_GROUPS 逐词同源。
   「电磁设计 × 机械设计」不算同一类；「机械设计 × 结构设计」同属机械结构方向，算。 */
const MI_DESIGN_GROUPS={
  mech:['机械','结构','机构','模具','工装','夹具','钣金'],
  elec:['电磁','电气','电子','电路','电力','电机','电控'],
  optic:['光学','光机','光电'],
  appear:['外观','造型','工业设计'],
  sim:['仿真','cae','热设计','散热','流体']};
function miDesignGroup(s){
  const t=String(s==null?'':s).toLowerCase();
  if(t.indexOf('设计')<0) return [];
  const out=[];
  for(const g in MI_DESIGN_GROUPS){
    if(MI_DESIGN_GROUPS[g].some(function(w){ return t.indexOf(w)>=0; })) out.push(g);
  }
  return out;
}
/* 命中判据专用窄义领域词（与 intent_semantics.HIT_DOMAIN 逐词同源）：只放指向明确、
   不会出现在通用职能岗里的词。拦「新能源 × 软件开发 / 装备 × 嵌入式」这类模型按
   「都叫开发」挑出的跨行岗；一方泛词识别不出领域时不拦。 */
const MI_HITDOMAIN={
  it:['软件','互联网','计算机','数据库','前端','后端','嵌入式','java','c++','android','ios','算法工程'],
  elec:['新能源','电气','半导体','芯片','集成电路','光伏','锂电','电力','电芯'],
  mech:['机械','装备','机电','数控','模具','发动机','结构'],
  chem:['化学','化工','精细化工','石化','炼化','高分子','试剂','树脂'],
  medical:['医药','制药','临床','药学','生物','医学','药品','原料药'],
  fin:['证券','基金','银行','保险','期货','金融','信托','私募'],
  civil:['建筑','土木','路桥','地产','施工','造价'],
  food:['食品','餐饮','饮料','乳业','烘焙'],
  edu:['教师','教学','学校','中学','小学','班主任','教研'],
  supply:['供应链','物流','仓储','货运','采购'],
  energy:['石油','煤炭','燃气','核电','风电','发电']
};
function miHitDomain(s){
  const x=String(s==null?'':s).toLowerCase(), out=[];
  Object.keys(MI_HITDOMAIN).forEach(function(f){
    if(MI_HITDOMAIN[f].some(function(k){ return x.indexOf(k)>=0; })) out.push(f);
  });
  return out;
}
/* 泛职能意向（管培生/专员/计划…）：本就要收各领域岗位，不参与跨行领域过滤。
   与 intent_semantics.GENERIC_INTENT_WORDS 逐词同源。语义映射在后端清洗，
   前端这里只做词表镜像以备同源校验。 */
const MI_GENERIC_INTENT=['管培生','培训生','储备干部','储备','专员','助理',
  '计划','干部','校招生'];
function miIsGenericIntent(s){ return MI_GENERIC_INTENT.some(function(w){ return String(s).indexOf(w)>=0; }); }
/* 方向层级：父类 → 下属细分（与 intent_semantics.DIR_PARENTS 同源）。
   采购/物流/仓储 ⊂ 供应链 → 同一条线照旧算；计划不是供应链专属（生产/核电都有
   计划岗），「计划管培生 × 供应链管培生」是并列方向，不算命中。 */
const MI_PARENTS={'供应链':['采购','物流','仓储']};
function miSameLine(ak,bk){
  if(!ak.length||!bk.length) return false;
  const parents=Object.keys(MI_PARENTS);
  for(let i=0;i<parents.length;i++){
    const ks=MI_PARENTS[parents[i]].concat([parents[i]]);
    if(ak.every(function(w){ return ks.indexOf(w)>=0; })
       && bk.every(function(w){ return ks.indexOf(w)>=0; })) return true;
  }
  return false;
}
/* 「设计师」与「工程师」同级：机械设计师 = 机械设计工程师（与 intent_semantics.DIR_NEUTRAL 同源） */
const MI_DIRNEUTRAL=['开发','研发','软件','程序','工程师','设计师','技术','应用','系统','岗','员'];
/* 职责词不是方向：设备维护 / 检修 / 点检 都是设备岗的职责，不是并列的另一条路 */
const MI_DUTY=['维护','保养','维修','点检','巡检','检修','调试'];
function miDirCore(t){
  let x=miDirText(t);
  MI_DIRNEUTRAL.forEach(function(w){ x=x.split(w).join(''); });
  return {t:x, k:MI_DIRWORDS.filter(function(w){ return x.indexOf(w)>=0; })};
}
/* 这一对（岗位 × 意向）能不能算对口：
   ok  算（意向没方向；或岗位没方向；或岗位方向 ⊂ 意向方向）
   ask 值得问一句（同向但岗位更细：测试 → 电气测试 / 器件测试）
   no  不成立（并列方向：后端 × 量化 / 前端 / 嵌入式，同大类两条路）
   与 intent_semantics.pair_verdict 同源。 */
/* 岗位名归一化：与 job_family._norm 逐字符同源（全角括号、顿号、J 码要先抹掉）。 */
function miFuncNorm(s){
  return String(s==null?'':s).replace(/[\s（）()\[\]【】、，,·—\-\/_]+/g,'').toLowerCase();
}
/* 岗位名 → 职能族；认不出来返回 ''（'' 是「不表态」，不是「无职能」）。 */
function miFuncOf(name){
  const x=miFuncNorm(name);
  if(!x) return '';
  for(let i=0;i<MI_FUNC.length;i++){
    if(x.indexOf(MI_FUNC[i][0])>=0) return MI_FUNC[i][1];
  }
  return '';
}
/* 两者是否**明确**属于不同职能。任一侧认不出来就返回 false（不表态）。 */
function miCrossFunc(title,intent){
  const a=miFuncOf(intent);
  if(!a) return false;
  const b=miFuncOf(title);
  return !!b && a!==b;
}
function miPairVerdict(title,intent){
  const a=miDirCore(intent), b=miDirCore(title);
  /* 职能族先判一次：区分「更泛的同类岗」和「另一个职能」。
     放在最前面，让「初级软件工程师 / 开发工程师」这类本身没方向词的意向也受约束。
     与 intent_semantics.pair_verdict 同一处判据逐条同源。 */
  const cross=miCrossFunc(title,intent);
  if(!a.t){
    if(cross) return 'no';
    /* ⓪ 泛研发意向 × 带方向岗位 → ask（与 intent_semantics.pair_verdict ⓪ 同源，
       2026-11-16 用户拍板：宽泛选择要附和方向）。研发工程师不再无条件吃下
       C++研发 / 自动化 / 电控硬件；岗位职能明确是非研发条线（财务 / 人事 /
       销售 / 设计……且名字里没有研发字样）→ 直接 no，不生成问一句的噪音。 */
    /* 软件条线补闸：miDirCore 剥不出「软件」的方向核，泛研发意向 × 编程条线岗
       必须转 ask（软件工程师 ↔ 研发工程师 曾漏网硬命中）。意向自己含软件条线词
       （学生勾的「软件工程师」）时不触发——同条线互配照常命中。与 ⓪ 同源。 */
    if(miSoftBroad(intent) && (b.t || (miHas(title,MI_SOFTLINE) && !miHas(intent,MI_SOFTLINE)))){
      const tf=miFuncOf(title);
      if(MI_NONRD.indexOf(tf)>=0 && !miHas(title,['研发','研究','开发'])) return 'no';
      return 'ask';
    }
    return 'ok';
  }
  /* 岗位没方向词 ≠ 更泛的同类岗：UI 设计师、财务专员、产品经理都没有方向词，
     却和开发不是同一个职能。 */
  if(!b.t) return cross?'no':'ok';
  if(a.t===b.t) return 'ok';
  /* 常识互斥判据（在「更宽/更细」子串判定之前，先挡住共享字但方向相反的错配）。
     与 intent_semantics.pair_verdict ① ② ③ 逐条同源。 */
  /* ① 教师 × 明确专业序列：一个教书、一个做技术/研究/医护，不是同一岗位。
     化学教师 × 化学环保助理专工 → no。 */
  if(miHas(title,MI_TEACH)!==miHas(intent,MI_TEACH)){
    if(miHas(title,MI_PROFSEQ)||miHas(intent,MI_PROFSEQ)) return 'no';
  }
  /* ② 上位泛学科岗位不能反推具体学科意向：高中学科教师 ≠ 高中化学教师。 */
  if(miHas(title,MI_GENERIC_SUBJ) && MI_SUBJ.some(function(s){ return intent.indexOf(s)>=0; })
     && !MI_SUBJ.some(function(s){ return title.indexOf(s)>=0; })) return 'no';
  /* ③ 两名各带不同的专业方向词（分析 × 环保 / 机械 × 电气）：并列细分，不算同一。
     只在一边有这个、另一边有那个且互不相交时拦，避免两边都含同词误伤。 */
  const td=MI_PROFDIR.filter(function(w){ return title.indexOf(w)>=0; });
  const idr=MI_PROFDIR.filter(function(w){ return intent.indexOf(w)>=0; });
  if(td.length&&idr.length&&!td.some(function(w){ return idr.indexOf(w)>=0; })) return 'no';
  /* ④ 设计方向族：都叫「X设计工程师」，但设计对象不同就是两条路
     （电磁设计 × 机械设计 / 光学设计 × 结构设计）。同组（机械 × 结构）不拦。
     与 intent_semantics 的 ④ 逐条同源。 */
  const dgt=miDesignGroup(title), dgi=miDesignGroup(intent);
  if(dgt.length&&dgi.length&&!dgt.some(function(g){ return dgi.indexOf(g)>=0; })) return 'no';
  /* 注意方向：a=意向方向，b=岗位方向。
     b ⊃ a → 岗位更细（测试 → 器件测试）→ ask；
     b ⊂ a → 岗位更泛（AI算法研究 → 算法）→ ok（小类能进大类）。
     ⑤ 共享动作核 + 不同领域限定词 → no：人工智能算法 × 运动控制算法——都以
     「算法」结尾，前面的领域限定互不包含，是并列领域不是同一个岗（与
     intent_semantics.pair_verdict ⑤ 同源）。ai ≡ 人工智能 先归一。 */
  for(let _i=0;_i<MI_ACTSUF.length;_i++){
    const act=MI_ACTSUF[_i];
    if(a.t.slice(-act.length)===act && b.t.slice(-act.length)===act){
      let pa=a.t.slice(0,a.t.length-act.length), pb=b.t.slice(0,b.t.length-act.length);
      pa=MI_AISYN[pa]||pa; pb=MI_AISYN[pb]||pb;
      if(pa&&pb&&pa.indexOf(pb)<0&&pb.indexOf(pa)<0) return 'no';
    }
  }
  if(a.t.indexOf(b.t)>=0) return 'ok';
  /* 职责词不算方向：设备维护工程师 × 设备工程师 → 同一岗位，不是「更细的方向」 */
  let b2=b.t; MI_DUTY.forEach(function(w){ b2=b2.split(w).join(''); });
  if(b2 && b2===a.t) return 'ok';
  if(b.t.indexOf(a.t)>=0) return 'ask';
  if(a.k.length&&b.k.length&&!a.k.some(function(w){ return b.k.indexOf(w)>=0; })){
    if(miSameLine(a.k,b.k)) return 'ok';   /* 采购/物流/仓储 ⊂ 供应链：同一条线照旧算 */
    return 'no';
  }
  /* 兜底：没互相包含、方向词也没交集（多半是岗位名只有职能没有方向）。
     同职能按原约定放行；明确跨职能的不能兜底当成对口。 */
  return cross?'no':'ok';
}
/* 方向不一样时，不能说成「另一种说法」：写清是哪个大类下面的哪个方向。
   与 intent_semantics._reason_by_direction 同源。 */
function miDirReason(t,intent){
  const a=miDirCore(intent), b=miDirCore(t);
  if(!b.t||a.t===b.t||(a.t&&b.t.indexOf(a.t)>=0)) return '';
  if(a.k.length&&b.k.length&&!a.k.some(function(w){ return b.k.indexOf(w)>=0; })){
    if(miSameLine(a.k,b.k))
      return '「'+t+'」是「'+intent+'」条线下的'+b.t+'细分（同一条线）';
    return '「'+t+'」与「'+intent+'」同属一个大类但方向不同（'+b.t+' × '+a.t+'）';
  }
  if(!a.t) return '「'+t+'」是「'+intent+'」下的'+b.t+'方向（同一大类，不是同一细分）';
  if(a.t&&b.t.indexOf(a.t)>=0) return '「'+t+'」的方向是'+b.t+'，比「'+intent+'」细，不是同一个岗位';
  return '';
}
/* 真·轮岗类岗位：只有这类「会不会往某方向培养」才是企业会考虑的事。
   研究员 / 助理工程师 / 分析师 / 专员 / 经理 是定向招聘，问了白问。
   与 intent_semantics.ROTATION_WORDS 同源。 */
const MI_ROTATION=['管培','培训生','储备干部','储干','校招生'];
function miIsRotation(t){
  const x=String(t==null?'':t).replace(/[\s（）()\[\]【】、，,·—\-\/_]+/g,'').toLowerCase();
  if(!x) return false;
  for(let i=0;i<MI_ROTATION.length;i++){ if(x.indexOf(MI_ROTATION[i])>=0) return true; }
  return false;
}
function miBareGeneric(t){
  let x=String(t==null?'':t).replace(/[\s（）()\[\]【】、，,·—\-\/_]+/g,'');
  if(!x) return false;
  if(MI_BARE.indexOf(x)>=0) return true;
  MI_BAREFILL.forEach(function(w){ x=x.split(w).join(''); });
  return x.length<=1;
}
/* 公司业务本身就是那个细分时，大类岗位才可按对口算（供应链公司的管培生）。
   与 intent_semantics.BIZ_EXTRA / biz_ok 同源。 */
const MI_BIZ={supply:['物流','仓储','快递','货运','港口','航运','配送','供应链管理'],
  fin:['证券','基金','银行','保险','期货','资管','私募','信托','金融'],
  data:['大数据','人工智能','智能科技','算法','数据服务','信息技术'],
  hr:['人力资源服务','人力资源'],legal:['律师事务所','法律服务'],
  mkt:['广告','公关','传媒'],trade:['进出口','国际贸易','货代','报关'],
  quality:['检测','认证'],media:['文化传媒','影视','广告传媒']};
function miBizFamilies(txt){
  const out=miFam(txt||'');
  for(const k in MI_BIZ){
    if(miHas(txt||'',MI_BIZ[k]) && out.indexOf(k)<0) out.push(k);
  }
  return out;
}
function miBizOk(intent,txt){
  const fa=miFam(intent), fb=miBizFamilies(txt);
  return fa.some(function(x){ return fb.indexOf(x)>=0; });
}
/* 主营领域族：只用于「值得问一句」的准入 —— 企业的行业/主营得沾意向的方向。
   保险公司的管培生不可能定向培养成化学工程师、教育公司的管培生不会是供应链方向：
   这类跨行岗位连问一句都是骚扰，直接不生成。与 intent_semantics.DOMAIN_FAMILY 逐词同源。 */
const MI_DOMAIN={
  chem:['化学','化工','精细化工','石化','炼化','材料','高分子','塑料','橡胶','涂料','印染','助剂','试剂','树脂','聚碳酸酯','聚酯','聚酰胺'],
  mech:['机械','装备','机电','自动化','数控','模具','发动机','汽车','重工'],
  elec:['电气','电子','半导体','芯片','集成电路','光伏','锂电','新能源','电力','通信','铁塔'],
  civil:['建筑','土木','城建','地产','路桥','园区'],
  edu:['教育','学校','中学','小学','培训','学院','K12','教师','教学','老师'],
  medical:['医院','医疗','医药','制药','生物','临床','护理','卫生','药学'],
  food:['食品','餐饮','饮料','乳业','烘焙','茶饮','古茗'],
  fin:['保险','银行','证券','基金','期货','金融','信托','资管','财富','私募'],
  it:['软件','互联网','信息科技','智能科技','大数据','算法','电商','网络科技','数字科技','计算机'],
  supply:['供应链','物流','仓储','货运','快递','报关','货代','4PL','采购'],
  energy:['能源','石油','煤炭','燃气','核电','风电','发电'],
  textile:['纺织','服装','面料','家纺','鞋','服饰'],
  furniture:['家具','家居','办公家具'],
  safety:['安全','防护','职业健康','安环','应急']
};
function miDomainFamilies(txt){
  return Object.keys(MI_DOMAIN).filter(function(f){ return miHas(txt||'',MI_DOMAIN[f]); });
}
/* 意向方向和企业主营是否沾边 —— 只用于「值得问一句」的准入。
   意向领域明确、但企业主营识别不出或不相交 → 不相关，连问句都不生成。
   只有意向本身是泛词、识别不出领域时 → 宽松放行，避免误杀。
   与 intent_semantics.biz_related 同源。 */
function miBizRelated(intent,bizText){
  const fi=miDomainFamilies(intent).concat(miFam(intent));
  if(!fi.length) return true;
  const fb=miDomainFamilies(bizText).concat(miBizFamilies(bizText));
  return fi.some(function(x){ return fb.indexOf(x)>=0; });
}
const MI_TECH=['工程师','研发','开发','算法','研究员','测试','工艺','设备','质检'];
const MI_PROD=['产品经理','产品专员','产品工程师','产品岗','产品规划','产品运营'];
const MI_OPS=['运营','企宣','公关','会展','会议策划'];
/* 内容 / 电商 / 产品三条运营线，不是「运营管理培训生」的同类入口 */
const MI_OPSCONTENT=['新媒体','电商','直播','短视频','内容运营','内容策划','社群',
                     '用户运营','店铺','产品运营','公众号','小红书','抖音'];
/* 「数据」类意向的岗位必须真的碰数据（化学分析 / 力学分析 / 质量分析 不算） */
const MI_DATATITLE=['数据','商业分析','量化','统计','BI','策略','经营分析','算法','机器学习','数仓','报表','模型'];
const MI_CONFLICT=['内核','测试','DBA','运维','研发','嵌入式','前端','后端','驱动','固件',
                   '编译','工艺','设备','生产','质检','研究员','开发工程师','集成工程师',
                   '服务工程师'];
const MI_SOFT=['开发','研发','软件','程序','算法'];
/* 软件细分方向入口，与 intent_semantics.DEV_TITLE_HINTS 逐词同源 */
const MI_DEVHINT=['前端','后端','嵌入式','全栈','服务端','客户端','Java','C++','C#','Python',
  'PHP','Unity3d','大模型应用','低代码','鸿蒙','Android','IOS','上位机','软件工程师'];
const MI_DEV=['前端','后端','嵌入式','驱动','固件','编译','全栈','服务端','客户端','C++',
              'Java','Python','PHP','研发','开发工程师','Unity3d','C#'];
/* 抹掉后为空 = 意向只说了「软件 / 开发」，没限定方向。
   与 intent_semantics._SOFT_WIDE 逐词同源。 */
const MI_SOFTWIDE=['软件开发工程师','软件研发工程师','软件工程师','软件开发','软件研发',
  '研发工程师','开发工程师','程序开发工程师','程序开发','程序员','工程师','软件','开发','研发','程序'];
function miSoftBroad(t){
  if(!miHas(t,MI_SOFT)) return false;
  let s=String(t||'');
  MI_SOFTWIDE.forEach(function(w){ s=s.split(w).join(''); });
  return !s.trim();
}
/* 动作核后缀 / AI 同义 / 非研发职能族：与 intent_semantics.ACTIVITY_SUFFIXES、
   _AI_SYN、NON_RD_FAMILIES 逐条同源（2026-11-16 泛意向方向对齐）。 */
const MI_ACTSUF=['算法'];
const MI_AISYN={'ai':'人工智能','ml':'机器学习'};
const MI_NONRD=['product','design','finance','hr','admin','legal','sales',
                'media','serv','edu','supply','quality','ehs','device','process'];
/* 软件条线提示词：岗位名剥不出方向核（「软件」在 DIR_NEUTRAL 里），但名字里的
   「软件/程序/编程」明确是编程条线的岗。泛研发意向撞上它们必须问一句，不能硬
   命中——「软件工程师 ↔ 研发工程师」是用户二次点名的漏网对（2026-11-16）。
   意向自己就写了这些词时不触发：软件条线互配是同条线正常命中，不是泛选。
   与 intent_semantics.DEV_SOFTLINE 同源。 */
const MI_SOFTLINE=['软件','程序','编程','编码','代码','信息技术'];
const MI_FIN=['财务','会计','审计','税务','核算','出纳','资金'];
/* 跨专业设计方向词：机械设计不吃 IC / 光学 / 电磁 / 逻辑 这类设计岗 */
const MI_DESIGN=['IC','芯片','光学','光机','电磁','逻辑','物理','数字','模拟','射频','FPGA','版图','PCB'];
/* 软件研发岗专用词：出现它们说明是写代码的岗，机械 / 电气 / 质量类意向不该收 */
const MI_SOFTTITLE=['数据开发','AI','大模型','智能体','低代码','量化','前端','后端','全栈',
                    'Java','Python','C++','算法','软件'];
/* 真正做工程设计的意向：泛「研发/开发工程师」岗名可以算同类 */
const MI_ENGDESIGN=['设计','机械','电气','电子','结构','自动化','控制','工艺','软件','算法','研发','开发'];

function miHas(t,arr){ return arr.some(function(k){ return String(t).indexOf(k)>=0; }); }

/* ---- 岗位层级（校招可投性）：与 ent_tools/job_level.py 同一套规则 ----
   同一职能里「主管 / 资深 / 经理」和「管培生 / 专员」是两档人：前者带团队或有
   年限要求，2027 届应届投不进去。五档：
     1 实习/见习 · 2 校招初入 · 3 常规经验岗 · 4 资深/主管 · 5 管理/专家
   应届只认 1~3 档；一条公告并列多档（「工艺工程师/主管/经理」「营销员/营销主管」）
   取最低档——公告里既然列了工程师、营销员，应届就能投那一档。 */
const MI_LV_CAMPUSMAX=3;
const MI_LVNAME={0:'未识别',1:'实习/见习',2:'校招初入',3:'常规经验岗',4:'资深/主管',5:'管理/专家'};
const MI_LV_LEAD=['总经理','副总经理','总监','首席','合伙人','董事','总裁','经理','负责人','专家','VP','CTO','CEO','CFO','COO'];
/* 名里带「经理」但校招照招的前台业务岗 / 产品岗 */
const MI_LV_LEADEX=['客户经理','大堂经理','值班经理','产品经理','业务经理','客户成功经理','理财经理'];
const MI_LV_SENIOR=['资深','高级','主管','组长','班长','科长','主任','主办','督导','领班','Sr.','Senior'];
const MI_LV_ENTRY=['管培','培训生','校招','应届','储备','储干','专员','助理','初级','专工','专责','干部','实习生'];
const MI_LV_INTERN=['实习','见习'];
const MI_LV_ASSIST=['助理','秘书'];
/* 「高级」不表示资历的固定搭配（高级中学）／「主任」不表示管理岗的固定搭配（班主任） */
const MI_LV_SENIORFALSE=['高级中学','高级技工学校','高级技校','班主任','主任委员'];
/* 能独立成岗的收尾字：只有每段都像独立岗位名时才按「/」拆开分档。
   反例「新市场/新项目开发经理」——斜杠分的是业务对象，拆开会把开发经理误判成常规档。 */
const MI_LV_JOBTAIL=['员','师','生','岗','助','理','管','监','任','工','长','代表','顾问','专员','主管','经理','干部','储干','业'];
function miRm(s,w){ return String(s).split(w).join(''); }
function miLvHas(s,arr){ return arr.some(function(k){ return s.indexOf(k)>=0; }); }
function miLvOne(t){
  if(!t) return 0;
  let scan=String(t);
  MI_LV_SENIORFALSE.forEach(function(w){ scan=miRm(scan,w); });
  if(MI_LV_ASSIST.some(function(w){ return t.slice(-w.length)===w; })) return 3;
  let probe=scan;
  MI_LV_LEADEX.forEach(function(w){ probe=miRm(probe,w); });
  if(miLvHas(probe,MI_LV_LEAD)) return 5;
  if(miLvHas(scan,MI_LV_SENIOR)) return 4;
  if(miLvHas(scan,MI_LV_INTERN)) return 1;
  if(miLvHas(scan,MI_LV_ENTRY)) return 2;
  return 3;
}
function miLvSegs(t){
  const raw=String(t).replace(/、/g,'/').split('/').filter(function(s){ return s.length>0; });
  if(raw.length<=1) return [t];
  const okAll=raw.every(function(s){
    return s.length>=2 && MI_LV_JOBTAIL.some(function(w){ return s.slice(-w.length)===w; });
  });
  return okAll ? raw : [t];
}
function miLevel(t){
  const s=String(t==null?'':t).replace(/（/g,'(').replace(/）/g,')').replace(/　/g,' ').trim();
  if(!s) return 0;
  let best=0;
  miLvSegs(s).forEach(function(seg){ const lv=miLvOne(seg); if(lv && (!best || lv<best)) best=lv; });
  return best;
}
/* 意向 a 能否投岗位 b：应届口径 1~3 档；意向本身是资深/管理岗时才放开到 5 档。 */
function miLevelOk(a,b){
  const tl=miLevel(b); if(!tl) return true;
  const il=miLevel(a);
  const cap=(il>=4)?5:MI_LV_CAMPUSMAX;
  if(tl>cap) return false;
  if(il && il<=2 && tl-il>=2) return false;
  if(il>=4 && tl<=2) return false;
  return true;
}
/* 不是岗位的写法：招聘批次名 / 专业类目名 / 事业单位招考名目 */
const MI_LV_NONJOB=['公务员','参公人员','事业编制','专业技术人员','科学研究人员'];
function miIsJob(t){
  const s=String(t==null?'':t).trim();
  if(!s) return false;
  if(MI_LV_NONJOB.indexOf(s)>=0) return false;
  if(s.indexOf('校园招聘')>=0) return false;
  if(s.indexOf('届')>=0 && /\d/.test(s)) return false;
  if(s.slice(-1)==='类' && s.length<=8) return false;
  if(s.slice(-2)==='专业' || s.indexOf('及其自动化')>=0) return false;
  return true;
}
function miGrams(s){ s=String(s||''); const o=[]; for(let i=0;i+1<s.length;i++) o.push(s.substr(i,2)); return o; }
/* 同义写法归一：「营运」就是「运营」（营运管培生 / 营运中台管培生）。
   行业简称同上：「自控 / 工控 / 仪控 / 集控」都是「控制」，「机修 / 点检」是设备岗的
   两种现场说法——东明石化写「自控工程师」、学生意向「控制工程师」，不归一永远对不上。
   与 intent_semantics.SYNONYMS 逐条同源。
   注意替换顺序：先长后短在这里不敏感（各词互不为前缀），但保持与 Python 一致。 */
function miSyn(s){
  return String(s||'')
    .replace(/营运/g,'运营')
    .replace(/自控/g,'控制').replace(/工控/g,'控制')
    .replace(/仪控/g,'控制').replace(/集控/g,'控制')
    .replace(/机修/g,'设备维修').replace(/点检/g,'设备点检');
}
function miFam(t){ const o=[]; for(const k in MI_FAM){ if(miHas(t,MI_FAM[k])) o.push(k); } return o; }
function miShare(a,b){
  a=miSyn(a); b=miSyn(b);
  const g=miGrams(a).filter(function(x){ return MI_GEN.indexOf(x)<0; });
  /* 2 字窗口会跨词边界造假共享：「风险管理岗」的「理岗」落在「施工管理岗」尾部，
     「采购寻源专员」的「源专」落在「人力资源专员」中间。以岗位后缀字收尾的共享
     不算证据（见 intent_semantics.BOUNDARY_TAIL）。 */
  const shared=g.filter(function(x){ return b.indexOf(x)>=0; });
  if(shared.some(function(x){ return MI_TAIL.indexOf(x.slice(-1))<0; })) return true;
  /* 词首片段不受边界规则约束：那条规则只为挡「风险管理岗」里跨词造出的「理岗」，
     而「施工管理岗」开头就是「施工」。旧版把它当边界片段扔掉，
     「施工员」这个最标准的施工管理入口就永远判不成对口。 */
  if(shared.some(function(x){ return a.indexOf(x)===0; })) return true;
  if(MI_GENERIC.indexOf(a)>=0 && miGrams(a).some(function(x){ return b.indexOf(x)>=0; })) return true;
  /* 意向本身是业务/销售型（外贸业务员、客户经理）时，「销售 xxx」就是同一类 */
  if(miHas(a,MI_SALES) && b.indexOf('销售')>=0) return true;
  /* 软件类意向：前端 / 后端 / 嵌入式 / 全栈 等细分方向是同一职能入口（规则 4c）。
     与 intent_semantics.DEV_TITLE_HINTS 同源。 */
  if(miSoftBroad(a) && miHas(b,MI_DEVHINT)) return true;
  const fa=miFam(a), fb=miFam(b);
  if(!fa.some(function(x){ return fb.indexOf(x)>=0; })) return false;
  return !MI_TECH.some(function(w){ return b.indexOf(w)>=0 && a.indexOf(w)<0; });
}
function miRole(a,b,ignoreLevel){
  if(MI_GENERIC.indexOf(b)>=0 && MI_GENERIC.indexOf(a)<0) return false;
  /* 大类不能反推小类：「管培生 / 研究员 / 专员」不带方向，不能算「供应链管培生 /
     量化研究员 / 数据分析岗」的对口证据。公司业务就是这个细分的例外，
     由 miMatch / miAskJobs 用 miBizOk 单独放行，不在这里开口子。 */
  if(miBareGeneric(b) && !miBareGeneric(a)) return false;
  /* 专业名称 / 招聘批次名不是岗位（「电气工程及其自动化」「2027 届校园招聘」） */
  if(/及其自动化|专业$/.test(b) || b.indexOf('校园招聘')>=0 || /\d{4}\s*届/.test(b)) return false;
  const sa=MI_SUBJ.filter(function(s){ return a.indexOf(s)>=0; });
  const sb=MI_SUBJ.filter(function(s){ return b.indexOf(s)>=0; });
  if(sa.length && sb.length && !sa.some(function(s){ return sb.indexOf(s)>=0; })) return false;
  const fam=miFam(a).some(function(x){ return miFam(b).indexOf(x)>=0; });
  if(b.indexOf('销售')>=0 && !miHas(a,MI_SALES)) return false;
  if(miHas(b,MI_PROD) && !miHas(a,['产品','市场','营销','运营'])) return false;
  /* 数据类意向只收真的碰数据的岗位（化学分析 / 力学分析 / 质量分析 不算） */
  if(a.indexOf('数据')>=0 && !miHas(b,MI_DATATITLE)) return false;
  if(miHas(b,MI_OPS) && a.indexOf('运营')<0 && !fam) return false;
  /* 运营意图不收内容 / 电商 / 产品线的运营岗（新媒体运营、电商运营、产品运营） */
  if(a.indexOf('运营')>=0 && !miHas(a,MI_OPSCONTENT) && miHas(b,MI_OPSCONTENT)) return false;
  if(b.indexOf('柜员')>=0 && a.indexOf('柜员')<0) return false;
  if(b.indexOf('总监')>=0 && a.indexOf('总监')<0) return false;
  if((b.indexOf('仓库')>=0||b.indexOf('仓管')>=0) && !miHas(a,['仓','物流','供应链','采购'])) return false;
  if(miHas(b,MI_FIN) && !miHas(a,MI_FIN)) return false;
  if(b.indexOf('战略')>=0 && a.indexOf('战略')<0) return false;
  if(b.indexOf('数据库')>=0 && a.indexOf('数据库')<0) return false;
  /* 跨专业设计岗：机械设计工程师 不吃 模拟IC / 光学 / 电磁 设计岗 */
  if(b.indexOf('设计')>=0 && MI_DESIGN.some(function(f){ return b.indexOf(f)>=0 && a.indexOf(f)<0; })) return false;
  /* 质量类意向：岗位名自己写着「质量」时，测试 / 研究员 是质量岗的常规叫法 */
  const qual = a.indexOf('质量')>=0 && b.indexOf('质量')>=0;
  const soft=miHas(a,MI_SOFT);
  /* 制造业方向词在供应链语境下是正当同类（生产计划 / 供应链事业群管培生-设备方向），
     孤立的生产、设备、工艺岗仍按跨职能拦掉（下面 fam 不相交）。 */
  const supCtx = miHas(b,['计划','供应链','物流','采购','PMC']);
  for(let i=0;i<MI_CONFLICT.length;i++){
    const k=MI_CONFLICT[i];
    if(b.indexOf(k)>=0 && a.indexOf(k)<0){
      if(soft && MI_DEV.indexOf(k)>=0) continue;
      if(qual && (k==='测试'||k==='研究员')) continue;
      if((k==='生产'||k==='设备'||k==='工艺') && supCtx && fam) continue;
      /* 业务环节词只是岗位名的**前缀语境**，不改变方向：「生产设备管理员」对
         「设备工程师」、「生产管培生」对「管培生」都是同一方向。旧版只认供应链
         语境，于是药业的「生产设备管理员」这类最标准的设备岗被挡掉，企业明明
         有设备岗却显示「推断待核实」。判据用 miShare（与 Python _share_core 同源）。 */
      if((k==='生产'||k==='工艺'||k==='质检') && miShare(a,b)) continue;
      /* 研发 / 开发工程师：只放给真正做工程设计的意向（机械设计、电气、结构），
         且岗位名不能是软件研发岗（数据开发 / AI / 低代码）或别的设计方向（光学 / IC）。 */
      if((k==='研发'||k==='开发工程师') && miHas(a,['设计','工程师','研发','开发','机械','电气'])){
        if(!soft && miHas(b,MI_SOFTTITLE)) return false;
        if(MI_DESIGN.some(function(f){ return b.indexOf(f)>=0 && a.indexOf(f)<0; })) return false;
        if(fam || miHas(a,MI_ENGDESIGN)) continue;
      }
      return false;
    }
  }
  /* 层级：同一职能内「主管 / 资深 / 经理」与「管培生 / 专员」是两档人。
     应届只认实习 / 校招初入 / 常规经验岗；更高档不算对口（企业不删、只说层级不符）。
     ignoreLevel=true 只用于「找出被层级挡下的岗位」那一步，别的地方不要传。 */
  if(!ignoreLevel && !miLevelOk(a,b)) return false;
  return true;
}
/* 同职能、但档位比应届可投的高 —— 与 intent_semantics.level_blocked 同口径。
   换画像时 payload 里那份是按旧画像算的，必须按当前意向重算，否则永远显示那 2 家。 */
function miLevelBlocked(intended,titles){
  const out=[];
  (titles||[]).forEach(function(t){
    if(!t || !miIsJob(t)) return;
    const tl=miLevel(t);
    for(let i=0;i<(intended||[]).length;i++){
      const a=intended[i];
      if(!a) continue;
      if(!miRole(a,t,true) || !miShare(a,t)) continue;
      if(!miLevelOk(a,t)){
        out.push({job:t, intent:a, level:(MI_LVNAME[tl]||'资深/管理')});
        break;
      }
      break;   // 同职能且层级也合适 → 这家有这个岗位的正常入口，不必提示
    }
  });
  return out;
}
function miMatch(a,pool,biz){
  for(let i=0;i<pool.length;i++){
    const t=pool[i];
    if(!t) continue;
    if(t===a) return t;
    if(t.indexOf(a)>=0 || (t.length>=4 && a.indexOf(t)>=0)){
      /* 泛研发意向不能靠「字面包含」吃下带方向的岗位（研发工程师 ⊂ C++研发工程师）：
         方向必须与学生方向域或主营相容、由后端裁决；本地重算时只留给现场问。
         软件条线岗同理——miDirCore 剥不出「软件」的方向核，必须用词表补闸
         （软件研发工程师 ⊂ 研发工程师 曾绕过闸硬命中）。与 ⓪ 同源。 */
      if(miSoftBroad(a) && ((miDirCore(t).t && miDirCore(t).t!==miDirCore(a).t)
                            || (miHas(t,MI_SOFTLINE) && !miHas(a,MI_SOFTLINE)))) continue;
      return t;
    }
    /* 大类岗位名：只有公司业务就是这个细分**且不是轮岗类**时才算对口
       （供应链公司的「商务专员」）；轮岗类（管培生 / 培训生 / 储备干部）
       即使业务对口也不硬命中 —— 先轮岗后定岗，岗位名没有「就是干这个」的
       证据（科力普「管理培训生」×物流专员 曾被 biz_ok 短路成语义命中）。
       一律留给 miAskJobs 变成现场提问。与 intent_semantics 同源（2026-11-16）。 */
    if(miBareGeneric(t) && !miBareGeneric(a)){
      if(!miIsRotation(t) && miBizOk(a,biz)) return t;
      continue;
    }
    /* 岗位名比意向宽一层（算法工程师 × AI算法研究工程师）：不算对口，
       留给 miAskJobs 变成现场提问；除非公司业务就是这个细分。 */
    if(miWider(t,a)){
      if(miBizOk(a,biz)) return t;
      continue;
    }
    if(miPairVerdict(t,a)==='no') continue;   /* 并列方向不算命中（后端 × 量化 / 前端） */
    /* 泛研发意向的 ask（量化开发 × 开发工程师）：方向未定，不冒充命中，
       转现场问 —— 与后端 align_jobs_detail 的 ⓪/R2 出口同一口径。 */
    if(miPairVerdict(t,a)==='ask' && miSoftBroad(a)) continue;
    if(miRole(a,t) && miShare(a,t)) return t;
  }
  return null;
}

/* 大类岗位名（管培生 / 储备干部 / 研究员）：不是对口证据，但值得现场问一句。 */
function miAskJobs(intended,titles,biz){
  const out=[], seen={};
  (titles||[]).forEach(function(t){
    if(!t || seen[t] || !miIsJob(t)) return;
    /* 纯大类里只留轮岗类（管培生 / 培训生 / 储备干部 / 校招生）；
       研究员 / 助理工程师 / 分析师 / 专员 / 经理 是定向招聘，不问。
       与 intent_semantics.ask_jobs 同源。 */
    const bare=miBareGeneric(t);
    if(bare && !miIsRotation(t)) return;
    for(let i=0;i<(intended||[]).length;i++){
      const a=intended[i];
      if(!a || miBareGeneric(a)) continue;
      /* 业务对口：定向大类岗按对口处理不重复问；轮岗类照问——问句升级为
         「主业对口，大概率往这个方向培养」。与 intent_semantics.ask_jobs 同源。 */
      const hot=miBizOk(a,biz);
      if(hot && !miIsRotation(t)) continue;
      /* 企业主营和这个意向方向毫不相关 → 连问一句都不该有
         （保险公司 / 教育公司的管培生不会定向培养成化学工程师）。 */
      if(!miBizRelated(a,biz)) continue;
      /* 岗位比意向宽一层（算法工程师 × AI算法研究工程师）也值得问一句 */
      const wider=!bare && miWider(t,a) && miRole(a,t) && miShare(a,t);
      if(!bare && !wider) continue;
      seen[t]=1;
      out.push({job:t, intent:a,
        why: bare
          ? (hot
             ? ('这家主业就是「'+a+'」所在的方向，「'+t+'」大概率会往这个条线培养，'
                +'但岗位名没写死方向。现场问一句：轮岗后能否定岗到「'+a+'」。')
             : ('岗位名只写「'+t+'」，没写业务方向；不同企业的「'+t
                +'」按不同条线培养。现场问一句：是招「'+a+'」这个方向，还是统一轮岗后再定岗。'))
          : ('岗位名「'+t+'」只写到「'+(miDirText(t)||t)+'」这一层，比「'+a
             +'」宽，看不出是不是这个细分方向。现场问一句：这个岗具体做「'+a+'」吗。')});
      return;
    }
  });
  return out.slice(0,3);
}

/* 命中复核表（构建期由大模型成批判好）：{"意向||企业岗位名": 0|0.5|1}。
   1   = 确认同类；0 = 复核否决；0.5 = 模型弃权（技术栈相邻 / 信息不足以下结论）；
   键不存在 = 未复核（模型不可用 / 该对不在候选集）。
   0 和 0.5 都不算命中证据，未复核的继续用本地规则但文案如实写「未复核」。 */
function hvGet(intent,job){
  const o=(typeof D!=='undefined'&&D&&D.hit_verdicts)||{};
  const k=String(intent)+'||'+String(job);
  return Object.prototype.hasOwnProperty.call(o,k) ? o[k] : null;
}
/* 是否挡掉命中：否决与弃权都挡（「不确定」不进证据，也不冒充「复核过」）。 */
function hvBlocks(intent,job){
  const v=hvGet(intent,job);
  return v===0 || v===0.5;
}
function matchIntent(titles,intended,sem,jobTitles,biz,dirOk){
  const pool=((jobTitles&&jobTitles.length)?jobTitles:titles)||[];
  const out=[];
  (intended||[]).forEach(function(x){
    const m=(sem||{})[x];
    let hitJob=null, via='', reason='';
    if(m&&m.via==='llm'){
      /* 逐个意向岗位名匹配企业真实岗位名：模型挑出的原词 == 企业岗位名，或企业岗位名
         完整包含该原词（s.length>=4）。禁止反向包含：否则「研究员」会被
         「数据科学算法研究员」误判为同类，「管培生」会被「供应链事业群管培生」误判为同类。
         命中后用该岗位名专属的 reason（模型已写明「即另一种说法」或「大模型判断二者类似工作内容」）。 */
      const reasons=m.reasons||{};
      let picked=null;
      for(let k=0;k<(m.titles||[]).length;k++){
        const s=m.titles[k];
        const ex=pool.filter(function(t){
          return s&&t&&(s===t||(s.length>=4&&t.indexOf(s)>=0)) && miLevelOk(x,t)
            /* 大类岗位名（研究员 / 管培生）不能反推细分意向，除非公司业务就是该细分 */
            && !(miBareGeneric(t) && !miBareGeneric(x) && !miBizOk(x,biz))
            /* 比意向宽一层的岗（算法工程师 × AI算法研究工程师）也不能算命中 */
            && !(miWider(t,x) && !miBizOk(x,biz))
            /* 并列方向（后端 × 量化 / 前端 / 嵌入式）：同大类两条路，不能顶替。
               岗位更细（测试 → 器件测试）原本算命中，但**复核 1 只是「确认同类」，
               不能把 ask 提升成命中**——否则复核表一换，页面命中数就与 payload
               不一致（2026-11-15 实测：客诉质量 × 质量 被判 ask，后端 drop、
               前端按复核 1 算命中，10 家分歧由此而来）。 */
            && miPairVerdict(t,x)!=='no'
            /* 泛研发意向的 ask 不能因「模型挑过」就吃下（研发工程师 × C++研发）：
               复核确认(1)除外——但 ask+复核1 本就不许提升成命中（见下条），
               所以泛意向的 ask 一律转现场问。与后端 ⓪ 同源。 */
            && !(miPairVerdict(t,x)==='ask' && miSoftBroad(x))
            && !(miPairVerdict(t,x)==='ask' && hvGet(x,t)===1)
            /* 复核否决的跨方向岗位不能算命中（食品研发 × 药品研发 只共享「研发」）；
               弃权（0.5）同样不算 —— C++ × 后端这种相邻技术栈不进证据 */
            && !hvBlocks(x,t);
        })[0];
        if(ex){ picked={job:ex, reason:(miDirReason(ex,x)||reasons[s]||m.reason||'大模型判定为同类校招岗位')}; break; }
      }
      if(picked){ hitJob=picked.job; via='llm'; reason=picked.reason; }
      else {
        // 只用「3 字以上」的专属组合词兜底，避免「运营 / 管理 / 业务」这类泛词误伤。
        const kw=pool.filter(function(t){
          return (m.terms||[]).some(function(k){ return k&&k.length>=3&&t&&t.indexOf(k)>=0; })
            && miRole(x,t) && miShare(x,t) && miLevelOk(x,t)
            /* 比意向宽一层的岗（算法工程师 × AI算法研究工程师）不能走这条兜底 */
            && !(miWider(t,x) && !miBizOk(x,biz))
            && miPairVerdict(t,x)!=='no'
            && !(miPairVerdict(t,x)==='ask' && miSoftBroad(x))
            && !(miPairVerdict(t,x)==='ask' && hvGet(x,t)===1)
            && !hvBlocks(x,t);
        });
        if(kw.length){ hitJob=kw[0]; via='llm-term'; reason=m.reason||'同属一类校招岗位'; }
      }
    }
    /* 2026-11-15 统一候选集：模型词表只决定「理由怎么写」，不再当作命中门槛。
       原来 `covered` 会让「模型已覆盖该意向」直接跳过本地规则，于是后端按企业全量
       岗位名判命中（爱旭「工艺」× 工艺工程师），前端却因为模型没挑出这个词而判不命中。
       规则本身有 bareGeneric / wider / pairVerdict 三道闸，不会退回「管培生 ×
       供应链管培生」那种只共享泛词的误命中——这条由 _test_match 的反例段守着。 */
    if(!hitJob){
      /* 逐个候选试，不能只试第一个：miMatch 命中的第一个如果是共享字的跨方向岗位
         （食品研发 × 药品研发），被复核否决后必须继续往下找，否则同一家企业里
         真正的同名岗位会被一起漏掉。
         复核结论 1 → 确认命中并标明是大模型复核的；0 / 0.5 → 跳过这个候选；
         没有结论 → 收下，但文案写「未复核」，不冒充复核过。 */
      let t=null, v=null;
      for(let i=0;i<pool.length;i++){
        const cand=pool[i];
        if(!cand || cand===hitJob) continue;
        /* 字面同名不需要复核，也不许写成「大模型复核过」——它本来就是个同名岗位。 */
        const cv=(cand===x) ? null : hvGet(x,cand);
        if(cv===0 || cv===0.5) continue;
        /* 复核 1 只能确认「本地规则本就判为对口」的对子，不能把同向但更细的岗位
           （质量工程师 × 客诉质量工程师 = ask）提升成命中：后端 align_jobs 只收
           pair_verdict=='ok'，前端若放行，页面命中数就多于 payload。 */
        if(cv===1 && miPairVerdict(cand,x)!=='ok') continue;
        if(miMatch(x,[cand],biz)){ t=cand; v=cv; break; }
      }
      if(t){
        hitJob=t;
        via=(v===1)?'llm-verify':'rule';
        reason=(v===1) ? '大模型复核：与「'+x+'」同属一类校招岗位'
          : (t===x ? '字面同名'
             : (t.indexOf(x)>=0 ? '岗位名里含你的意向岗位名（未复核）'
                : '本地规则判定：同职能不同叫法（未复核）'));
      }
    }
    /* 后端按主营业务方向判定的命中（岗位名不带方向：「工艺工程师」在化工厂是化工
       方向，前端只有字面规则，判不出这一层）。直接采信后端结论，标 via:'dir'，
       否则页面会出现「命中岗位」有、而「岗位语义命中」没有的两栏打架。
       只在用服务端 payload 的当前画像时采信；本地换画像（recompute）不传 dirOk。 */
    if(!hitJob && dirOk && dirOk.length){
      for(let i=0;i<dirOk.length;i++){
        if(pool.indexOf(dirOk[i])>=0){
          hitJob=dirOk[i]; via='dir';
          reason='岗位名不带方向，这家按主营业务判定的方向与你的意向一致';
          break;
        }
      }
    }
    if(hitJob) out.push({intent:x, job:hitJob, via:via, reason:reason});
  });
  return out;
}

/* ── 画像×主营复检（2026-11-17，与后端 biz_recheck.py 同源）────────────
   命中不能只看「岗位名 ↔ 意向」字面，还要看「企业主营业务 + 岗位名」是否
   对得上「意向岗位 + 专业」。反面案例：金融公司「管培生」 × 物流专业学生
   选的「管培生」—— 字面全同，但金融公司培养不出物流方向。
   强词一票即可，弱词需 ≥2 个不同弱词；网页导航垃圾短语先剔再匹配。
   规则改动必须与 biz_recheck.py 成对改，parity 才不会两边各说各话。 */
const BIZ_JUNK=['安全生产','主营产品','经营范围','公司简介','联系我们','时政要闻','社会责任','新闻中心','未知','暂无'];
const BIZ_FIELDS=[
 ['物流供应链',['物流','供应链','仓储','运输管理'],['物流','供应链','仓储','运输','货代','配送'],
  ['物流','供应链','仓储','运输','货代','快递','配送','冷链','报关','货运','电商','外贸','进出口','制造','生产','工厂','加工','实业','物流园'],
  ['超市','商超','卖场','跨境','出口','商贸','分销','采购','贸易','批发']],
 ['化学化工材料',['化学','化工','材料','高分子','制药','药学','应用化学'],['化学','化工','材料','制药','药学','试剂','涂料','配方'],
  ['化学','化工','材料','制药','医药','药','试剂','涂料','配方','石化','精细化工','实验','电池材料','树脂','胶粘','助剂','冶金','冶炼'],['分析','检验','化验','检测','质检','测试']],
 ['机械制造',['机械','机电','车辆','过程装备','焊接'],['机械','机电','模具','焊接','液压','装配'],
  ['机械','机电','装备','制造','汽车','模具','重工','设备','零部件','加工','焊接','液压','装配','生产','工厂','实业'],[]],
 ['电气自动化',['电气','自动化','控制'],['电气','自动化','控制','变频'],
  ['电气','自动化','控制','电力','电机','变频','PLC','仪表','智能装备','制造','生产','设备'],[]],
 ['电子信息',['电子','通信','微电子','集成电路','光电'],['电子','通信','集成电路','芯片','半导体','光电','嵌入式'],
  ['电子','通信','芯片','半导体','集成电路','光电','电路','嵌入式','硬件','元器件','封装','研发'],['测试','科技']],
 ['计算机软件',['计算机','软件','信息安全','网络工程','人工智能','数据科学'],['软件','算法','前端','后端','软件测试','运维','数据','人工智能'],
  ['软件','信息技术','互联网','云计算','人工智能','算法','数据','系统集成','数字化','科技','网络'],[]],
 ['金融财会',['金融','会计','财务管理','审计','经济学','保险','证券'],['金融','会计','审计','风控','投资','证券','保险'],
  ['金融','银行','证券','保险','基金','投资','财务','会计','审计','风控','信托','理财','资本','期货','租赁'],[]],
 ['土木建筑',['土木','建筑','工程管理','造价','城乡规划','道路桥梁'],['土木','建筑','造价','施工','监理','结构','规划'],
  ['建筑','土木','施工','工程','地产','建设','监理','造价','市政','装饰','园林','基建'],[]],
 ['医学生物',['临床','医学','生物','护理','药学'],['医学','临床','生物','护理','医疗器械'],
  ['医院','医疗','医药','生物','诊断','器械','健康','临床','护理'],[]],
 ['食品',['食品','营养'],['食品','品控','烘焙','饮料'],
  ['食品','饮料','乳品','烘焙','餐饮','农产品','调味','品控'],[]],
 ['能源环境',['能源','环境','环保','安全工程'],['能源','环保','环境','安全','节能'],
  ['能源','环保','环境','节能','电力','新能源','光伏','风电','储能','脱硫','水处理'],[]],
 ['教育',['师范','教育','学前教育'],['教师','教研','教务','辅导'],
  ['教育','学校','培训','教学','课程','教研'],[]],
 ['设计创意',['设计','美术','动画','视觉'],['设计','视觉','插画','UI'],
  ['设计','视觉','创意','广告','品牌','传媒','文化'],[]],
 ['商贸管理',['工商管理','市场营销','国际贸易','电子商务','管理科学'],['市场','营销','销售','运营','电商','外贸','商务'],
  ['商贸','贸易','销售','市场','营销','电商','零售','品牌','运营','进出口','外贸','跨境'],[]]
];
function miCleanBiz(s){ let x=String(s||''); BIZ_JUNK.forEach(function(j){ x=x.split(j).join(' '); }); return x.replace(/\\s+/g,' ').trim(); }
function miStripGeneric(s){ const G=['管理培训生','管培生','培训生','储备干部','储干','校招生','专员/助理','仓储管理员','管理员','工程师','技术员','代表','办事员','专员','助理','人员','职员','文员','操作工','实习生','学徒','岗','主管','经理','总监','员']; let x=String(s||'').trim(),p; do{ p=x; G.forEach(function(t){ if(x.indexOf(t)>=0) x=x.split(t).join(''); }); x=x.trim(); }while(x!==p); return x; }
function miBizKw(major,intents){
  const kw={},fields={}; const m=String(major||'');
  BIZ_FIELDS.forEach(function(f){ f[1].forEach(function(mk){ if(mk&&m.indexOf(mk)>=0) fields[f[0]]=1; }); });
  (intents||[]).forEach(function(it){
    const core=miStripGeneric(it); if(!core||core.length<2) return; kw[core]=1;
    /* 意向词命中领域：首个命中的领域生效（与后端 student_domain 的 break 同源
       ——原先无 break 全加，「化工销售」会同时拉进商贸管理领域，前端比后端
       松：一个意向开门全体受益。2026-11-17 对齐。） */
    for(let bi=0;bi<BIZ_FIELDS.length;bi++){
      const f=BIZ_FIELDS[bi];
      if(f[2].some(function(mk){ return mk&&core.indexOf(mk)>=0; })){ fields[f[0]]=1; break; }
    }
  });
  BIZ_FIELDS.forEach(function(f){ if(fields[f[0]]){ f[3].forEach(function(k){ kw[k]=1; }); f[4].forEach(function(k){ kw[k]=1; }); } });
  return {kw:Object.keys(kw), fields:Object.keys(fields)};
}
function miBizHit(fields,biz){
  /* 主营判定 = 强词一票制（与后端 biz_hit 同源）。弱词（分析/检测/商超…）
     只参与岗位名/意向名的方向词匹配 —— 推断命中宁可少标不错标：
     广告分析软件公司曾靠「分析」误过闸。 */
  const x=miCleanBiz(biz); if(!x||!fields||!fields.length) return false;
  for(let i=0;i<BIZ_FIELDS.length;i++){ const f=BIZ_FIELDS[i];
    if(fields.indexOf(f[0])<0) continue;
    for(let j=0;j<f[3].length;j++){ if(x.indexOf(f[3][j])>=0) return true; }
  }
  return false;
}
/* 明确命中复检（与后端 filter_real 同源）：跨行业通用职能/实验室岗直放；
   其余岗位主营强词一票（按意向对各算各的方向集，与 pair_fields 同源：
   销售意向放行商贸类主营，不连带给其他意向放行贸易/零售公司）。 */
/* 跨行业通用职能/实验室岗（与后端 _GENERIC_JOB 同源）：这类岗每家公司都设，
   免查主营直放；领域研发/工艺岗必须主营沾边，否则半导体研发会钻进化学匹配。 */
function miGenericJob(s){
  return /采购|供应商|仓储|物流|供应链|客服|售前|售后|技术支持|EHS|安全工程师|计划员|化验|质检|实验|检验|检测|分析|测试|质量|专利|注册|生产管理|生产计划|生产跟单/.test(String(s||''));
}
function miBizOkReal(job,intent,MK,biz,major){
  if(miGenericJob(intent) || miGenericJob(job)) return true;
  const pf=miBizKw(major||'', [intent]).fields;
  return miBizHit(pf.length?pf:(MK&&MK.fields),biz);
}

function e(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function short(s,n){ s=String(s||''); return s.length>n ? s.slice(0,n)+'…' : s; }
function stateLabel(s){ return s==='hit'?'明确命中':s==='inferred'?'推断待核实':'不对口'; }
/* 「只有大类岗 ask」的企业（2026-11-17 用户定口径）：没有真实命中、没有推断
   命中，仅靠无方向的管培生/储备干部 ↔ 具体意向撑着——照常显示但全部后置
   到队尾。管培生↔管培生（意向本身是大类）算真命中，不进这条；有其他命中的
   企业也不进这条。 */
function onlyAsk(r){
  return r.state!=='hit'
    && !(r.infer_matches&&r.infer_matches.length)
    && !!(r.ask_jobs&&r.ask_jobs.length);
}
function groupTitle(s){
  if(s==='hit') return ['t1','优先去 · 有真实岗位证据'];
  if(s==='inferred') return ['t2','按行业推断 · 现场问一句确认'];
  return ['t3','暂不优先'];
}

/* ---------- 采集端：读学生当前画像 ---------- */
function getProfile(){
  let p=null;
  /* 画像也可以直接从地址栏带进来：?p=<base64(JSON)>。
     测试页「用这套画像看排行」走的是这条路——不写 localStorage、不依赖服务端，
     双击本地 html 也能看（之前用绝对路径 /fair.html + localStorage，服务一停就白屏）。 */
  try{
    const m=/[?&]p=([^&]+)/.exec(location.search||'');
    if(m){
      const bin=decodeURIComponent(m[1]);
      const txt=decodeURIComponent(escape(atob(bin)));
      p=JSON.parse(txt);
    }
  }catch(err){}
  if(!p){ try { p=JSON.parse(localStorage.getItem('fair_profile')||'null'); } catch(err){} }
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
  } else {
    vals.city={v:0, b:'工作城市未查到，本项记 0'};
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
    } else {
      vals.home={v:0, b:'无有效工作城市，离家无法计算，本项记 0'};
    }
  } else {
    vals.home={v:0, b:'未填写家乡，离家无法计算，本项记 0'};
  }
  /* 企业类型：直接用画像对「央企/国企/民企/外企/事业单位」的真实排序。
     旧版把 nature 归成「国央企」三桶再查画像键，桶名对不上 → 国企/央企/事业单位整维丢分 */
  const ctr=P.company_type_rank||RULES.ct_rank_def;
  const nat=String(r.nature||'').trim();
  if(ctr[nat]!=null){
    const v=rankValue(ctr[nat], Object.keys(ctr).length);
    vals.company_type={v:v, b:'性质 '+nat+'（你的第'+ctr[nat]+'优先）'};
  } else if(nat && nat!=='未知'){
    const v=rankValue(Object.keys(ctr).length+1, Object.keys(ctr).length+1);
    vals.company_type={v:v, b:'性质 '+nat+'（不在你的排序内，按末档）'};
  } else {
    vals.company_type={v:0, b:'企业性质未查到，本项记 0'};
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
  /* 规模：客观分档，不问用户。'未知'必须走推断（旧版被 or 短路，整维丢失） */
  let st=r.scale_tier||'';
  if(RULES.scale_tiers[st]==null) st=classifyScale(r.nature, r.name, r.industry);
  if(st&&RULES.scale_tiers[st]!=null){
    vals.scale={v:RULES.scale_tiers[st], b:'规模档 '+st+'（客观判定）'};
  } else {
    vals.scale={v:0, b:'规模档无法判定，本项记 0'};
  }
  /* 行业地位 / 成长性：读 145 家增补档案（payload v42 字段）合并后的档位。
     榜单里没有的就是 0 —— 不给中性底板分。
     非企业主体（学校/党政/事业单位）也记 0，只在依据里写明「无第三方榜单」：
     旧版把它们移出分母再归一，#85/#86 只剩两维、用 25 分池算分，跟别的企业不可比。 */
  const rt=(r.v42&&r.v42.rank_tier)||'';
  if(RULES.industry_status_tiers[rt]!=null){
    vals.industry_status={v:RULES.industry_status_tiers[rt],
      b:'行业地位 '+rt+'（第三方榜单/500强）'};
  } else if(r.non_enterprise){
    vals.industry_status={v:0, b:'非企业主体（学校/党政/事业单位），无第三方榜单，本项记 0'};
  } else {
    vals.industry_status={v:0, b:'无公开榜单数据，本项记 0'};
  }
  const gt=(r.v42&&r.v42.growth_tier)||'none';
  const gtw=Number(w.growth)||0;
  if(RULES.growth_tiers[gt]!=null && gtw>0){
    vals.growth={v:RULES.growth_tiers[gt], b:'成长性 '+gt};
  } else if(gt==='unknown' && gtw>0){
    vals.growth={v:0, b:'成长性未查到（记 0，不臆造）'};
  } else if(r.non_enterprise){
    vals.growth={v:0, b:'非企业主体，无成长类榜单，本项记 0'};
  } else if(gtw>0){
    vals.growth={v:0, b:'无成长性数据，本项记 0'};
  } else {
    vals.growth={v:0, b:'无成长性数据（本项不计分，权重已回填其他维度）'};
  }

  /* 分母恒为画像权重总和：漏维按 0 分计入，不再归一放大 */
  const total=Object.keys(w).reduce(function(n,k){return n+(Number(w[k])||0);},0)||100;
  const parts=Object.keys(DIM_LABEL).map(function(k){
    const ww=Number(w[k])||0;
    const vv=vals[k]?vals[k].v:0;
    const bb=vals[k]?vals[k].b:'本项无数据，记 0';
    return {id:k, name:DIM_LABEL[k]||k, weight:ww, value:+vv.toFixed(2),
            contribution:+(ww/total*vv*100).toFixed(1), basis:bb};
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

  /* 学生勾选的意向岗位 ↔ 企业真实岗位名：大模型语义判定（未覆盖时退回字面匹配）。
     2026-11-16：服务端 payload 带 intent_matches_backend（后端对齐 + 泛意向方向
     对齐 + 复核过滤后的最终裁决）。当前画像没被本地改动时直接采信，不再前端
     另算一套 —— 后端算 A、前端算 B 的打架就此消除；换画像 / 自定义画像仍走
     本地规则重算（与后端同源的规则，parity 测试盯着）。 */
  const _bc=!!(P&&P.fair_id&&String(P.fair_id)!==String((typeof D!=='undefined'&&D.active_fair_id)||''));
  const matches=(!_bc && r.intent_matches_backend && r.intent_matches_backend.length)
    ? r.intent_matches_backend
    : matchIntent(r.matched_jobs,P.intended_jobs,P.sem,r.job_titles,
                  (r.name||'')+' '+(r.industry||'')+' '+(r.biz_scope||''),
                  r.dir_hit_jobs);
  return {score:score, effective_weight:+total.toFixed(1),
          parts:parts, questions:qs.slice(0,2),
          intent_hit:matches.map(function(m){return m.intent;}),
          intent_matches:matches,
          /* 推断命中对：与真实命中同一箭头格式、显式标「推断」。画像未改时
             直接采信后端 infer_matches_backend（明确命中企业也有推断对，
             2026-11-17）；换画像走 recompute 现算。 */
          infer_matches:(!_bc && r.infer_matches_backend) ? r.infer_matches_backend : []};
}

/* 推断命中对现算（换画像 / 自定义画像场景）：业务链推断岗（biz_fit.jobs）+
   画像推断岗（inferred_jobs）× 学生意向。口径与后端 _infer_pairs 同源且刻意
   收紧为「同名或互含」：推断岗不是公告原文，宁可少标不错标（职能族兜底会把
   配方×工艺、研发×配方也放进来，太松）。每意向最多一对，业务链岗优先。 */
function miInferPairs(intended,pool,MK,biz,major){
  /* 画像×主营复检（2026-11-17，与后端 filter_infer 同源）：推断岗名是猜的，
     不能自证方向 —— 主营文本必须容纳学生方向，否则整个企业的推断对全撤。
     主营为空/纯「未知」一律不标：没有业务依据就不该推断。全局主营判定
     不过闸时整撤（全局方向集 ⊇ 每对的方向子集，等价快捷路径）。 */
  if(MK && !miBizHit(MK.fields,biz)) return [];
  const out=[],seen={};
  (pool||[]).forEach(function(t){
    t=String(t||'').trim(); if(!t) return;
    for(let i=0;i<(intended||[]).length;i++){
      const a=intended[i];
      if(!a||seen[a]) continue;
      if(t===a||t.indexOf(a)>=0||a.indexOf(t)>=0){
        out.push({intent:a, job:t, via:'infer',
                  reason:'大模型按主营业务推断可能招（非招聘公告原文），现场核实'});
        seen[a]=1; break;
      }
    }
  });
  /* 主营复核按意向对各算各的方向集（与后端 filter_infer per-pair 同源）：
     化工学生的销售意向放行商贸类主营，化学分析意向不跟着吃「贸易」强词。 */
  return out.filter(function(m){
    const pf=miBizKw(major||'', [m.intent]).fields;
    return miBizHit(pf.length?pf:(MK&&MK.fields), biz);
  });
}

function recompute(P){
  /* 换了画像（本地切换测试画像 / 学生现场改勾选）时，payload 里的 state 与
     matched_jobs 是按**上一个画像**算好冻住的。不重算就会出现「换专业后
     明确命中还是 24 家」——分数变了、命中口径没变，是假结果。
     这里用与后端同源的规则重算（parity 测试盯的就是这两套规则的一致性），
     并把原因文案改成按当前意向描述，不冒用服务端结论。 */
  const _custom = !!(P && P.fair_id && String(P.fair_id)!==String(D.active_fair_id||''));
  const rows=BASE.map(function(r){
    const c=calcOne(r,P);
    const x=Object.assign({}, r, c);
    if(_custom){
      const jt=(r.job_titles||[]);
      /* 公司业务（名字 / 行业 / 主营）：判断大类岗位名（管培生）能不能算对口 */
      const biz=(r.name||'')+' '+(r.industry||'')+' '+(r.biz_scope||'')
        +((r.v42&&r.v42.segment)?(' '+r.v42.segment):'');
      /* 画像×主营复检（2026-11-17，与后端 biz_recheck 同源）：主营上下文补上
         岗位简介，方向词匹配用 biz2；matchIntent 的 biz 维持旧口径不动。 */
      const biz2=biz+((r.job_brief)?(' '+r.job_brief):'');
      const MJ=(P.profile&&P.profile.major)||P.major||'';
      const MK=miBizKw(MJ, P.intended_jobs||[]);
      const hits=matchIntent(jt, P.intended_jobs||[], P.sem||{}, jt, biz)
        .filter(function(h){ return miBizOkReal(h.job, h.intent, MK, biz2, MJ); });
      const jobs=[], its=[];
      hits.forEach(function(h){
        if(jobs.indexOf(h.job)<0) jobs.push(h.job);
        if(its.indexOf(h.intent)<0) its.push(h.intent);
      });
      x.state = jobs.length ? 'hit' : 'inferred';
      x.matched_jobs = jobs;
      x.intent_hit = its;
      x.intent_matches = hits;
      /* 推断命中对所有企业都算（明确命中企业也要在同一箭头位置标推断）；
         真实命中里已有的岗位名不重复标（互含也算同一岗位：真实命中
         「脱硫脱硝工艺工程师」时，推断对不再标「工艺工程师」）。 */
      x.infer_matches = miInferPairs(P.intended_jobs||[],
        ((r.biz_fit&&r.biz_fit.jobs)||[]).concat(r.inferred_jobs||[]), MK, biz2, MJ)
        .filter(function(m){
          return !jobs.some(function(rj){ return m.job.indexOf(rj)>=0 || rj.indexOf(m.job)>=0; });
        });
      x.level_blocked = miLevelBlocked(P.intended_jobs||[], jt);
      x.ask_jobs = miAskJobs(P.intended_jobs||[], jt, biz);
      x.state_recalc = true;
      // 工商信息页（爱企查 / 企查查 / 天眼查…）不是招聘公告：换画像重算也不能
      // 把 payload 里已降级的这类企业重新抬成「明确命中」（与后端同源规则）。
      const _src=String((r.evidence&&r.evidence.source)||'').toLowerCase();
      if(x.state==='hit'&&/aiqicha\.baidu\.com|qcc\.com|tianyancha\.com|qixin\.com|11315\.com|gsxt\.gov\.cn/.test(_src)){
        x.state='inferred';
        x.matched_jobs=[];
        x.intent_matches=[];
        x.check_notes=(x.check_notes||[]).concat(['岗位记录只来自工商信息页、非招聘公告，换画像重算仍按推断待核实']);
      }
      if(!jobs.length){
        const ii=(P.intended_jobs||[]).slice(0,3);
        x.reason = '按当前画像（意向：'+(ii.join('、')||'—')
          +((P.intended_jobs||[]).length>3?' 等':'')
          +'）没有查到对口岗位；按行业「'+(r.industry||'未知')+'」推断，现场问一句确认。';
        x.infer_notice = '';
      }
    }
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
    // 名次只由排序分决定（用户 2026-11-13 明确要求）。
    // 可信度 / 待核实 / 岗位状态只是展示标注，不进名次比较；
    // 唯一例外是「现场确认不招」——那是事实层面的出局，仍顺延到队尾。
    const an=(feedback[a.name]||{}).result==='no'?1:0;
    const bn=(feedback[b.name]||{}).result==='no'?1:0;
    // 同分并列必须给确定性次序，且与后端 rank 完全同规则：
    // 命中 > 推断 → 展位号 → 名称。否则同分企业前后端名次会不一样。
    // fit=no 与「现场确认不招」一样顺延到队尾（后端 fair_bridge 同规则）：
    // 主营方向与意向不交叉的企业在页面底部折叠组，名次不该还占着主榜前排。
    const fn=function(x){return x.fit==='no'&&x.biz_fit&&x.biz_fit.via==='llm'
        &&x.state==='inferred'&&!x.state_recalc?1:0;};
    const afn=fn(a), bfn=fn(b);
    const ao=onlyAsk(a)?1:0, bo=onlyAsk(b)?1:0;
    const ah=a.state==='hit'?0:1, bh=b.state==='hit'?0:1;
    return (an-bn) || (afn-bfn) || (ao-bo) || (b.score-a.score) || (ah-bh)
        || String(a.booth||'999').localeCompare(String(b.booth||'999'))
        || a.name.localeCompare(b.name);
  });
  rows.forEach(function(r,i){
    r.rank=i+1;
    r.next=rows[i+1]?rows[i+1].name:'';
    r.rank_reason='第 '+(i+1)+' 位：排序分 '+r.score
      +' / '+({hit:'明确命中（有真实 JD）',inferred:'推断命中，待现场核实',miss:'无对口岗位'})[r.state];
    const fb=feedback[r.name];
    if(fb&&fb.result==='yes') r.rank_reason+='；现场已确认招，推断升级为明确命中';
    if(fb&&fb.result==='no') r.rank_reason+='；现场确认不招，已顺延（保留在队尾供核对）';
    if(r.fit==='no'&&r.biz_fit&&r.biz_fit.via==='llm'&&r.state==='inferred'&&!r.state_recalc)
      r.rank_reason+='；主营方向与你的意向不交叉，已顺延到队尾';
    if(onlyAsk(r)) r.rank_reason+='；仅「管培生/储备干部」大类岗待问方向，已后置';
  });
  return rows;
}

/* ---------- 报告内容（复用原 CSS 类） ---------- */
// 可信度只做「提示」，不参与名次（用户要求：低置信度、要核实都不影响排名）。
function trustBadge(r){
  const n='<span style="font-size:11px;color:#8a94a6">（仅提示，不影响名次）</span>';
  if(r.trust==='low') return '<span style="color:#c0392b;font-weight:650">⚠ 低可信</span>'+n;
  if(r.trust==='medium') return '<span style="color:#b7791f">◐ 待核实</span>'+n;
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
  /* 大类岗位名单独成块，放在提问区：不是命中，是一句该问的话。 */
  const askRows=(r.ask_jobs||[]).map(function(a){
    return '<div class="job"><div class="job-h"><b>问一句：「'+e(a.job)+'」是这个方向吗</b>'
      +'<span>大类岗位名</span></div><div class="job-req">'+e(a.why)+'</div></div>';
  }).join('');
  const notes=(r.check_notes||[]).map(function(n){return '<div class="job-req">· '+e(n)+'</div>';}).join('');
  const notice=r.infer_notice
    ? '<div style="margin:8px 0;padding:8px 10px;background:#fdecea;border-left:3px solid #c0392b;'
      +'color:#c0392b;font-size:13px;font-weight:650;border-radius:0 5px 5px 0">⚠ '
      +e(r.infer_notice)+'</div>' : '';
  const intentLine=(r.intent_matches&&r.intent_matches.length)
    ? '<span class="hits">岗位语义命中：'+e(r.intent_matches.map(function(m){
        // 模型已经把「即另一种说法 / 大模型判断二者类似工作内容」写进 reason，
        // 这类以「大模型判断 / 大模型判定」开头的就直接原样展示，避免再套一层「大模型判定：」。
        const tail = m.via==='literal' ? '（字面近似）'
          : m.via==='llm-verify' ? '（大模型复核：同类，可投）'
          : m.via==='rule' ? '（'+m.reason+'）'
          : (/^大模型(判断|判定|复核)/.test(m.reason||'') ? '（'+m.reason+'）' : '（大模型判定：'+m.reason+'）');
        return m.job+' ↔ 你选的「'+m.intent+'」'+tail;
      }).join('；'))+'</span>'
    : ((r.intent_hit&&r.intent_hit.length)
        ? '<span class="hits">你勾选的岗位命中：'+e(r.intent_hit.join('、'))+'</span>' : '');
  /* 推断命中：与真实命中同一箭头格式，但显式标「推断」、用橙色调区分——
     这些岗位名来自大模型按主营业务的推断（业务链 / 画像补全），不是招聘
     公告原文。学生拿着这对去现场核实，核实了（feedback=yes）才升级成真命中。 */
  const inferLine=(r.infer_matches&&r.infer_matches.length)
    ? '<span class="hits" style="color:#b7791f">推断命中：'
      +e(r.infer_matches.slice(0,2).map(function(m){
        return m.job+' ↔ 你选的「'+m.intent+'」';
      }).join('；'))+(r.infer_matches.length>2?' 等 '+r.infer_matches.length+' 对':'')
      +'（大模型按主营业务推断，非企业公告，现场核实）</span>'
    : '';
  /* 画像×主营复核展示（2026-11-17 用户要求）：完整报告里明示这层复核做过、
     结论如何——不是黑箱。口径与后端 biz_recheck / 前端镜像同源：
     强词一票制；主营为空/识别不出方向时不判。 */
  const _rk=(function(){
    const mj=(P&&((P.profile&&P.profile.major)||P.major))||'';
    const MK=miBizKw(mj, P.intended_jobs||[]);
    if(!MK.fields.length) return '';
    const biz=(r.name||'')+' '+(r.industry||'')+' '+(r.biz_scope||'')
      +((r.v42&&r.v42.segment)?(' '+r.v42.segment):'')
      +(r.job_brief?(' '+r.job_brief):'');
    const ok=miBizHit(MK.fields,biz);
    let hitw=[];
    if(ok){ const x=miCleanBiz(biz);
      BIZ_FIELDS.forEach(function(f){ if(MK.fields.indexOf(f[0])<0) return;
        f[3].forEach(function(k){ if(x.indexOf(k)>=0 && hitw.indexOf(k)<0) hitw.push(k); }); }); }
    const w=e(hitw.slice(0,4).join('、')||MK.fields.slice(0,3).join('、'));
    if(r.state==='hit')
      return ok
        ? '<span class="hits" style="color:#1a6b4f">主营复核：已执行 · 通过——主营里出现你的方向词（'+w+'），命中岗位与主营对得上</span>'
        : '<span class="hits" style="color:#b7791f">主营复核：已执行 · 部分通过——主营未出现方向词，留下的命中都是岗位名/意向自带方向的；无方向的同名命中已降为现场待问</span>';
    if(!ok)
      return '<span class="hits" style="color:#c0392b">主营复核：已执行 · 不通过——主营与你的方向对不上，推断命中已全部撤下</span>';
    if(r.infer_matches&&r.infer_matches.length)
      return '<span class="hits" style="color:#1a6b4f">主营复核：已执行 · 通过——主营里出现你的方向词（'+w+'），推断命中据此保留</span>';
    return '<span class="hits" style="color:#1a6b4f">主营复核：已执行 · 主营沾边——现场问一句（管培生/储备干部培养方向）的准入通过，无推断命中</span>';
  })();
  /* 层级提示：同一职能但写着主管 / 资深 / 经理的岗位，校招投不进这一档。
     不假装对口、也不说人家不招——企业照常留榜，给学生一句现场可问的话。 */
  const lvb=(r.level_blocked||[]);
  const levelLine=lvb.length
    ? '<span class="line" style="color:#8a6d3b">层级提示：'+e(lvb.slice(0,2).map(function(x){
        return '「'+x.job+'」属'+x.level+'档，与你选的「'+x.intent+'」不同档，校招投不进';
      }).join('；'))+(lvb.length>2?' 等 '+lvb.length+' 个岗位':'')
      +' · 企业照常留在榜上，可现场问是否按应届招</span>'
    : '';

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
    ? '<div class="job-req"><b>企业画像（145 家增补档案 · 2026-11-15）</b>'
      +'<span style="color:#8a6d3b">行业地位 / 成长性参与打分；细分、榜单明细、岗位来源只展示</span></div>'
      +'<div class="job-req">行业地位：'+e(v.industry_rank_label||'未查')
        +(v.rank_tier?' / '+e(v.rank_tier)+'（'
          +((RULES.industry_status_tiers||{})[v.rank_tier]!=null
            ? '本项得 '+RULES.industry_status_tiers[v.rank_tier] : '—')+'）':'')
        +(v.industry_rank_label==='不适用（非企业主体）'
          ? ' · 学校/党政/事业单位，本项不参与打分':'' )+'</div>'
      +(vhits||'<div class="job-req">· 无榜单条目（不代表不是龙头）</div>')
      +'<div class="job-req">成长：'+e(v.growth_tier||'—')
        +(v.growth_tier==='unknown'?'（未查到，记 0）':'')
        +(v.growth_note?' · '+e(short(v.growth_note,90)):'')
        +'</div>'
      +(r.growth_flag?'<div class="job-req" style="color:#b3261e">⚠ '+e(r.growth_flag)+'</div>':'')
      +'<div class="job-req">细分领域：'+e(short(v.segment||'—',80))
        +(v.segment_leader?' · 细分领先 '+e(v.segment_leader)
          +(v.segment_leader==='self_claim'?'（企业自述，不作认定）':''):'')
        +'</div>'
      +(srcLine.indexOf('并集 0')>=0?'':'<div class="job-req">岗位来源（三步流程保留）：'+srcLine+'</div>')
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
   +   (r.hit_pos?'<span class="line">命中优先第 '+r.hit_pos+' 位 · 推荐序第 '+r.rank
        +' 位（匹配分 '+r.score+' 照常显示，命中企业在先）</span>':'')
   +   '<span class="line">来源 '+e(short(ev.source,54))+' · '+e(ev.date)+' · 岗位来源 '+e(ev.jobSource)+'</span>'
   +   (r.inferred_job_notice?'<span class="line" style="color:#8a6d3b">'+e(r.inferred_job_notice)
        +(r.inferred_jobs&&r.inferred_jobs.length?'：'+e(r.inferred_jobs.slice(0,6).join('、')):'')+'</span>':'')
   +   (r.job_brief?'<span class="line" style="color:#8a6d3b">岗位情况：'+e(short(r.job_brief,160))+'</span>':'')
   +   (r.llm_basis?'<span class="line" style="color:#1a6b4f"><b>'+e(short(r.llm_basis,240))+'</b></span>':'')
   +   (r.hits.length?'<span class="hits">命中岗位：'+e(r.hits.join('、'))
        +((r.hit_total||r.hits.length)>r.hits.length?' 等 '+r.hit_total+' 个（判定到此，仅列前 '+r.hits.length+' 个）':'')
        +'</span>':'')
   +   intentLine
   +   inferLine
   +   _rk
   +   levelLine
   +   (r.misses.length?'<span class="misses">未对口'
        +((r.miss_total||r.misses.length)>r.misses.length?'（示例 '+r.misses.length+'/'+r.miss_total+' 个）':'')
        +'：'+e(short(r.misses.join('、'),70))
        +((r.job_title_total||0)>r.misses.length+r.hits.length?' · 这家共 '+r.job_title_total+' 个岗位名，其余未逐一列出':'')
        +'</span>':'')
   +   (r.reason?'<span class="line">对口理由：'+e(short(r.reason,90))+'</span>':'')
   + '</div><div class="sc">'+r.score+'<em>缺数据维度记 0</em></div></div>'
   + notice
   + '<div class="calc"><b>算分依据（七项 100 · 按你当前画像实时重算）</b>'
   +   '<div class="calc-sub">七维权重全部计入（合计 100）；缺数据的维度记 0 分，'
   +     '既不给中性底板，也不从分母里拿掉——否则缺数据反而会把分数抬上去。'
   +     '分值只用于排序，不代表企业好坏。</div>'
   +   '<table style="width:100%;border-collapse:collapse;font-size:11.5px;margin-top:6px">'
   +   '<tr style="color:#7b8492"><td>维度</td><td>你的权重</td><td>档位值</td><td>贡献</td><td>依据</td></tr>'
   +   partRows+'</table></div>'
   + '<div class="jobbox"><details><summary>信息缺口 · 现场提问 · 事实校验 · 证据溯源（'+r.gaps.length+' 项缺口）</summary>'
   +   '<div class="job-req"><b>建议现场问（按当前权重 × 缺口可信度实时取前 2）</b></div>'+qRows
   +   (askRows?'<div class="job-req"><b>大类岗位名 · 值得问一句</b>'
        +'（名字没写方向，不能当对口证据）</div>'+askRows:'')
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
  /* 推断命中也用同一箭头格式上列表（2026-11-17 用户要求）：优先用后端/
     本地现算的推断对；推断对为空但有大类岗位名（管培生之类）时，用
     「大类岗 ↔ 意向」垫上——这类企业本来就只剩「问一句」这一条线索，
     列表上不给学生箭头对，学生就只会看到「推断待核实」三个字没东西可看。 */
  const imb=(r.infer_matches||[]).slice(0,2).map(function(m){return m.job+' ↔ '+m.intent;});
  /* 大类岗位（无方向的管培生/储备干部）不是命中：不上箭头行，只留在💬问句行；
     只有这类 ask 证据的企业整体后置到队尾（见排序里的 onlyAsk）。
     推断命中箭头只认真推断对（主营复核通过的那批）。 */
  const focus=top.concat(
    hit.length?['岗位命中：'+hit.join('；')]:[],
    imb.length?['推断命中：'+imb.join('；')+'（大模型按主营业务推断，现场核实）']:[]);
  const line2=focus.length?e(focus.join(' · ')):'按当前画像计算，暂无特别突出的命中项';
  const basisLine=r.llm_basis
    ? '<div class="line" style="color:#1a6b4f"><b>'+e(short(r.llm_basis,120))+'</b></div>' : '';
  /* 层级提示要出现在列表上，不能只藏在「完整报告」里：
     学生扫榜时就得知道「这家有同职能岗位，但写着主管/资深/经理，校招投不进」，
     否则会把排行榜当成「这些岗位我都能投」。 */
  const lvb=(r.level_blocked||[]);
  const lvlLine=lvb.length
    ? '<div class="line" style="color:#8a6d3b">⚠ 层级提示：'
      + e(lvb.slice(0,2).map(function(x){return x.job+'（'+x.level+'）';}).join('、'))
      + (lvb.length>2?' 等 '+lvb.length+' 个':'')
      + ' 与「'+e(lvb[0].intent)+'」不同档，校招投不进这一档</div>'
    : '';
  /* 大类岗位名（管培生 / 储备干部 / 研究员）：不是对口证据，但值得问一句。
     制造业企业的管培生不一定按供应链培养 —— 大类不能反推小类。 */
  const avb=(r.ask_jobs||[]);
  const askLine=avb.length
    ? '<div class="line" style="color:#8a6d3b">💬 值得问一句：有「'
      + e(avb.slice(0,2).map(function(x){return x.job;}).join('、'))
      + '」但没写业务方向，问一句是否按「'+e(avb[0].intent)+'」方向培养</div>'
    : '';
  return '<div class="row" id="tri-'+idx+'">'
   + '<div class="rowhead"><div class="nm"><b>'+e(r.name)+'</b> '+trustBadge(r)+'</div>'
   +   '<div class="sc">'+r.score+'<em>缺数据维度记 0</em></div></div>'
   + '<div class="line">'+line1+'</div>'
   + '<div class="line">'+line2+'</div>'
   + basisLine
   + lvlLine
   + askLine
   + '<div class="btns" style="margin-top:6px">'
   +   '<button type="button" class="btn btn-p" data-detail="'+e(r.name)+'">查看完整报告 →</button>'
   + '</div></div>';
}

/* ---------- v12 硬约束：条件必须可见 ---------- */
// 硬约束会直接删企业，删了却不说等于骗人。这里把「因你的条件删掉了哪些」摆出来。
function hardFilterNote(custom){
  var hf=D.hard_filter||{};
  var em=D.excluded_miss||{};
  var ec=D.excluded_campus||{};
  if(!hf.raw_text && !hf.dropped_count && hf.accept_no_major && !em.count && !ec.count) return '';
  var p=hf.parsed||{};
  var bits=[];
  if((p.cities||[]).length) bits.push('城市：'+p.cities.join('、'));
  if((p.industries||[]).length) bits.push('行业：'+p.industries.join('、'));
  if((p.exclude_natures||[]).length) bits.push('排除性质：'+p.exclude_natures.join('、'));
  var txt=[];
  if(hf.raw_text) txt.push('你的要求「'+e(hf.raw_text)+'」'+(bits.length?'（已理解为 '+e(bits.join('；'))+'）':''));
  if(!hf.accept_no_major) txt.push('未接受教培类企业');
  if(!txt.length && !em.count && !ec.count) return '';
  var names=(hf.dropped||[]).slice(0,6).map(function(d){return e(d.name);}).join('、');
  var more=(hf.dropped||[]).length>6?' 等':'';
  var condLine=txt.length
    ? '<b>已按你的条件筛掉 '+ (hf.dropped_count||0) +' 家企业</b>：'+txt.join('，')
    : '';
  var missLine=em.count
    ? '<br><b>已排除 '+em.count+' 家岗位不对口的企业</b>（模型判定不对口；0 岗与推断待核实的企业仍保留，「没查到」不等于「不招」）'
    : '';
  var campusLine=ec.count
    ? '<br><b>已排除 '+ec.count+' 家校招公告里没有对口岗位的企业</b>（校招公告岗位齐全，不必去现场碰运气；其他来源不保证齐全的仍保留并提示现场确认）'
    : '';
  return '<div class="note" style="font-size:12px;margin:0 0 10px;padding:8px 10px;'
    + 'background:#fff8e6;border-left:3px solid #d9a400;border-radius:0 5px 5px 0">'
    + condLine + missLine + campusLine
    + ((hf.dropped||[]).length? '<br><span style="color:#8a94a6">排除名单：'+names+more+'</span>' : '')
    + '<br><span style="color:#8a94a6">这是硬条件，不是降权——不满足的企业不会出现在任何排名里。</span>'
    + (custom ? '<br><span style="color:#b7791f">注意：这份「已排除」名单是服务端按库内画像'
        + e(D.active_fair_id||'') + '预筛的，切换本地测试画像不会重算；'
        + '它只决定谁不进榜，不影响榜内企业的分数与名次。</span>' : '')
    + '</div>';
}

/* ---------- 结束使用：一键把本次测试存进测试记录 ----------
   原来的「保存到测试库」埋在折叠区里还要手填标签，实测学生测完没人去点，
   测试记录页就一直是空的。现在榜单下面放一个醒目的「结束使用」：
   自动生成标签、自动带上浏览器当前的画像与排名快照，点一下就落库。 */
function fmtGen(t){
  t=String(t||'');
  return t? t.replace('T',' ').slice(0,19) : '未知';
}
function endSessionBar(){
  return '<div style="margin:16px 0 6px;border-top:1px dashed #cfd6e4;padding-top:12px;'
    +'display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
    + '<button type="button" class="btn btn-p" data-end-session '
    +   'style="font-size:13px;padding:8px 18px">结束使用</button>'
    + '<span style="font-size:12px;color:#41506b">点这里把本次填写与排名结果存进测试记录'
    + '（自动记录，不用填标签）</span>'
    + '<a href="/tests.html" target="_blank" style="font-size:12px;color:#2563eb">'
    + '查看测试记录 →</a>'
    + '<span id="end-msg" style="font-size:12px"></span>'
    + '</div>';
}
function bindEndSession(box){
  var btn=box.querySelector('[data-end-session]');
  if(!btn) return;
  btn.onclick=function(){
    var msg=box.querySelector('#end-msg');
    var P=getProfile();
    var top=(currentRows||[]).slice(0,12).map(function(r){
      return {name:r.name,score:r.score,city:r.city,industry:r.industry,state:r.state};});
    var now=new Date(), pad=function(n){return (n<10?'0':'')+n;};
    var tag='结束使用 '+now.getFullYear()+'-'+pad(now.getMonth()+1)+'-'+pad(now.getDate())
      +' '+pad(now.getHours())+':'+pad(now.getMinutes());
    btn.disabled=true;
    if(msg){msg.style.color='#8a94a6';msg.textContent='保存中…';}
    fetch(SEM_BASE+'/api/fair/test-save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tag:tag,top_n:12,
        browser:{profile:{major:P.major,home_city:P.home_city,region:P.region,
                          fair_id:P.fair_id,intended_jobs:P.intended_jobs,
                          weights:(wOverride||P.weights)},
                 top:top},
        note:P.fair_id?('会话画像 '+P.fair_id):''})})
    .then(function(r){return r.json();}).then(function(d){
      if(msg){
        msg.style.color=d&&d.ok?'#0f9960':'#c0392b';
        msg.textContent=d&&d.ok?('已存入测试记录 #'+d.id+'（'+d.n_rows+' 家企业）')
          :('保存失败：'+((d&&d.error)||'未知原因'));
      }
      btn.disabled=false;
    }).catch(function(){
      if(msg){msg.style.color='#c0392b';msg.textContent='保存失败：连不上服务';}
      btn.disabled=false;
    });
  };
}

/* ---------- v12 测试数据库：保存本次填写 + 排序结果 ---------- */
function testSaveBox(rows){
  var hf=D.hard_filter||{};
  var hfLine=hf.raw_text
    ? '硬约束「'+e(hf.raw_text)+'」已剔除 <b>'+hf.dropped_count+'</b> 家'
    : '未设硬约束';
  return '<details style="margin:18px 0 0;border-top:1px dashed #cfd6e4;padding-top:10px">'
    + '<summary style="cursor:pointer;font-size:12px;color:#41506b">'
    + '实验记录：保存本次填写与排序结果 / 查看并删除历史记录</summary>'
    + '<div class="note" style="font-size:12px;margin:8px 0">'
    + '当前共 '+rows.length+' 家企业参与排名；'+hfLine
    + '；教培类：'+(hf.accept_no_major?'已纳入':'已剔除')
    + '<br>测试跑出来的记录可逐条删除，只保留真人跑的；删除不影响企业库和画像。'
    + ' <a href="/tests.html" target="_blank" style="color:#2563eb">打开独立测试记录页 →</a>'
    + '</div>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
    + '<input id="tst-tag" placeholder="标签：会场名 / 组别，如 南大场-A组" '
    +   'style="flex:1;min-width:180px;padding:6px 8px;border:1px solid #cfd6e4;border-radius:5px;font-size:12px">'
    + '<input id="tst-sid" placeholder="问卷编号（选填）" '
    +   'style="width:130px;padding:6px 8px;border:1px solid #cfd6e4;border-radius:5px;font-size:12px">'
    + '<button type="button" class="btn btn-p" data-test-save>保存到测试库</button>'
    + '<span id="tst-msg" style="font-size:12px;color:#0f9960"></span>'
    + '</div>'
    + '<div id="tst-list" style="margin-top:10px"></div>'
    + '</details>';
}
function bindTestSave(box){
  var btn=box.querySelector('[data-test-save]');
  if(!btn) return;
  var list=box.querySelector('#tst-list');

  // 历史记录列表：逐条查看「填了什么 / 排出了什么」，逐条删除测试脏数据
  function loadList(){
    if(!list) return;
    list.innerHTML='<span style="font-size:12px;color:#8a94a6">读取记录中…</span>';
    fetch('/api/fair/test-list?limit=50').then(function(r){return r.json();}).then(function(d){
      var items=(d&&d.items)||[];
      if(!items.length){ list.innerHTML='<div class="note" style="font-size:12px">暂无测试记录。</div>'; return; }
      var h='<div style="font-size:12px;color:#41506b;margin:4px 0 6px">共 '+items.length+' 条（最新在前）</div>'
        + '<div style="max-height:280px;overflow:auto;border:1px solid #e2e7f0;border-radius:6px">';
      items.forEach(function(it){
        h+='<div style="display:flex;gap:8px;align-items:center;padding:6px 8px;border-bottom:1px solid #eef1f6;font-size:12px;flex-wrap:wrap">'
          + '<span style="color:#8a94a6;min-width:30px">#'+it.id+'</span>'
          + '<span style="color:#8a94a6;min-width:118px">'+e(it.created_at)+'</span>'
          + '<b style="min-width:120px;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+e(it.tag||'未命名')+'</b>'
          + '<span style="min-width:100px">'+e(it.major||'—')+'</span>'
          + '<span style="color:#8a94a6">'+it.n_rows+' 家</span>'
          + (it.hard_filter?'<span style="color:#b7791f" title="'+e(it.hard_filter)+'">硬约束</span>':'')
          + '<button type="button" class="btn btn-g" data-test-view="'+it.id+'">查看</button>'
          + '<button type="button" class="btn btn-g" data-test-del="'+it.id+'" style="color:#c0392b;border-color:#e6b3b3">删除</button>'
          + '</div>'
          + '<div id="tst-detail-'+it.id+'" style="display:none;padding:8px 10px;background:#f7f9fc;font-size:12px;line-height:1.7"></div>';
      });
      h+='</div>';
      list.innerHTML=h;
      list.querySelectorAll('[data-test-view]').forEach(function(b){
        b.onclick=function(){
          var pane=list.querySelector('#tst-detail-'+b.dataset.testView);
          if(!pane) return;
          if(pane.style.display!=='none'){ pane.style.display='none'; return; }
          pane.style.display='block'; pane.textContent='读取中…';
          fetch('/api/fair/test-detail?id='+b.dataset.testView).then(function(r){return r.json();}).then(function(dd){
            var it=(dd&&dd.item)||{}, top=it.top||[];
            pane.innerHTML='<div><b>输入</b>：专业 '+e(it.major||'—')
              +'；权重 '+e(JSON.stringify(it.weights||{}))+'</div>'
              +'<div>意向岗位 '+e((it.intended||[]).join('、')||'—')
              +'；硬约束 '+e(it.hard_filter||'无')+'</div>'
              +'<div style="margin-top:4px"><b>输出前 '+top.length+' 名</b>：'
              + top.map(function(t,i){ return (i+1)+'. '+e(t.name)+' ('+t.score+')'; }).join('　')
              +'</div>'
              +'<div>硬约束剔除 '+((it.dropped||[]).length)+' 家；备注 '+e(it.note||'—')+'</div>';
          }).catch(function(){ pane.textContent='读取失败'; });
        };
      });
      list.querySelectorAll('[data-test-del]').forEach(function(b){
        b.onclick=function(){
          var id=b.dataset.testDel;
          if(!window.confirm('确定删除测试记录 #'+id+'？删除后不可恢复，真人跑的记录请保留。')) return;
          b.disabled=true;
          fetch('/api/fair/test-delete',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({id:id})}).then(function(r){return r.json();}).then(function(dd){
            if(dd&&dd.ok){ loadList(); }
            else { b.disabled=false; window.alert('删除失败：'+((dd&&dd.error)||'未知原因')); }
          }).catch(function(){ b.disabled=false; window.alert('删除失败：连不上服务'); });
        };
      });
    }).catch(function(){
      if(list) list.innerHTML='<span style="font-size:12px;color:#c0392b">读取失败（离线模式不支持）</span>';
    });
  }
  loadList();

  btn.onclick=function(){
    var tagEl=box.querySelector('#tst-tag'), sidEl=box.querySelector('#tst-sid');
    var msg=box.querySelector('#tst-msg');
    var tag=(tagEl&&tagEl.value||'').trim();
    if(!tag){
      if(msg){msg.style.color='#c0392b';msg.textContent='先填个标签，否则以后分不清是哪一场';}
      return;
    }
    btn.disabled=true;
    if(msg){msg.style.color='#8a94a6';msg.textContent='保存中…';}
    fetch('/api/fair/test-save',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tag:tag,sid:(sidEl&&sidEl.value||'').trim(),top_n:12})
    }).then(function(r){return r.json();}).then(function(d){
      if(msg){
        msg.style.color=d&&d.ok?'#0f9960':'#c0392b';
        msg.textContent=d&&d.ok
          ? ('已保存（记录 #'+d.id+'，'+d.n_rows+' 家企业）')
          : ('保存失败：'+((d&&d.error)||'未知原因'));
      }
      if(d&&d.ok) loadList();
      btn.disabled=false;
    }).catch(function(err){
      if(msg){msg.style.color='#c0392b';msg.textContent='保存失败：连不上服务（离线模式不支持）';}
      btn.disabled=false;
    });
  };
}

function render(){
  const box=document.getElementById('mres');
  if(!box) return;
  /* 报告视图（?report=1 / ?detail=）下，采集流程留下的「导入名单」卡片只剩一个
     白色空壳（标题和输入框都被隐藏了，padding 还留着），首屏看着像没加载完。
     这里按「有没有可见子元素」判断，有内容的卡片不动。 */
  (function(){
    const q=new URLSearchParams(location.search);
    if(q.get('report')!=='1' && !q.get('detail')) return;
    document.querySelectorAll('.mb-card').forEach(function(el){
      const any=Array.prototype.some.call(el.children, function(ch){
        return ch.getBoundingClientRect().height>2;
      });
      if(!any) el.style.display='none';
    });
  })();
  const P=getProfile();
  /* 当前画像的意向若不在 payload 的语义映射里（换画像 / 现场新勾），
     先让服务端补判；拿不到就用本地规则兜底，不阻塞首屏。 */
  ensureSemantics(P.intended_jobs);
  const allRows=recompute(P);
  /* 本地规则判成命中、payload 里没有复核结论的对子（现场新填意向）→ 成批补复核。
     只收 via='rule' 的：语义映射命中的本来就来自大模型，不重复判。 */
  const _unv=[];
  allRows.forEach(function(r){
    (r.intent_matches||[]).forEach(function(m){
      if(m.via==='rule' && hvGet(m.intent,m.job)===null) _unv.push([m.intent,m.job]);
    });
  });
  if(_unv.length) ensureVerify(_unv);
  /* 换画像后 payload 里的 hit/inferred 统计是按旧画像的，这里按当前结果重数 */
  const _hitN=allRows.filter(function(r){return r.state==='hit';}).length;
  const _infN=allRows.filter(function(r){return r.state==='inferred';}).length;
  const _customNow=!!(P.fair_id && D.active_fair_id
                      && String(P.fair_id)!==String(D.active_fair_id));
  const rows=allRows.filter(function(r){
    if(filter==='high') return r.trust==='high';
    if(filter==='infer') return r.state==='inferred';
    if(filter==='low') return r.trust==='low';
    return true;
  });
  /* 与你的方向无关的家数（业务判定 no）：只用于顶部计数，卡片渲染见下方折叠组 */
  const _noN=rows.filter(function(r){
    return r.biz_fit && r.biz_fit.fit==='no' && !r.state_recalc;
  }).length;
  if(sortMode==='booth'){
    rows.sort(function(a,b){
      return String(a.booth||'zzz').localeCompare(String(b.booth||'zzz'),'zh',{numeric:true});
    });
    // 导逛模式下补站位，避免卡片仍写「队列第 N 位」与实际位置不符。
    rows.forEach(function(r,i){ r.tour_pos=i+1; });
  }
  if(sortMode==='hit'){
    // 明确命中优先（用户 2026-11-15 要求）：先排 state==='hit' 的企业（组内按分），
    // 推断待核实的全部排后面——推断企业分数再高也不越过命中企业。
    // 现场确认不招的照旧垫底；组内同分用 展位号→名称 定确定性次序。
    rows.sort(function(a,b){
      const an=(feedback[a.name]||{}).result==='no'?1:0;
      const bn=(feedback[b.name]||{}).result==='no'?1:0;
      const ao=onlyAsk(a)?1:0, bo=onlyAsk(b)?1:0;
      const ah=a.state==='hit'?0:1, bh=b.state==='hit'?0:1;
      return (an-bn) || (ah-bh) || (ao-bo) || (b.score-a.score)
        || String(a.booth||'999').localeCompare(String(b.booth||'999'))
        || a.name.localeCompare(b.name);
    });
    rows.forEach(function(r,i){ r.hit_pos=i+1; });
  }
  const fbKeys=Object.keys(feedback);
  const fbYes=fbKeys.filter(function(k){return feedback[k].result==='yes';}).length;
  const fbNo=fbKeys.length-fbYes;
  const live=P.is_session?'<span style="color:#0f9960">● 已接入本次采集画像 '+e(P.fair_id)+'</span>'
                      :(P.is_server?'<span style="color:#0f9960">● 正在使用服务端最近一次采集的画像 '+e(P.fair_id)+'（本标签页未走采集流程，结果已按该画像算好）</span>'
                      :'<span style="color:#b7791f">○ 尚未生成画像，正在用默认画像演示</span>');
  // 缓存体检：payload 里带着每一项结论是不是「本版代码」算出来的。
  // 以前改完规则忘了清缓存，页面照旧显示旧结论且完全看不出来；现在摆在明面上。
  const _cacheNote=(function(){
    const cs=D.cache_status||{}, ks=Object.keys(cs);
    if(!ks.length) return '';
    const ok=ks.filter(function(k){return cs[k].ok;}).length;
    const bad=ks.filter(function(k){return !cs[k].ok;});
    return '<b>缓存体检</b>：'+ks.length+' 项，本版代码生成 '+ok+' 项'
      +(bad.length ? '；<span style="color:#b7791f">需注意：'+bad.map(function(k){
          return k+'（'+cs[k].why+'）';}).join('；')+'</span>'
        : '；全部与当前代码一致')+'<br>';
  })();
  const _infoHtml='<div class="note" style="background:#eff4ff;border:1px solid #d6e4ff;border-radius:8px;'
    +'padding:9px 12px;margin:0 0 14px;font-size:12px;line-height:1.8">'
    +'<b>三端协调报告 · v7 现场回流 / 展位动线</b>　'+live+'<br>'
    +(_customNow
      ? '<span style="color:#b7791f">⚠ 当前画像来自浏览器本地（'+e(P.fair_id)+'），'
        +'<b>分数、岗位命中/层级提示已按这套画像重算</b>；企业理由、现场提问、'
        +'缺口说明仍是服务端「'+e(D.active_fair_id)+'」那份，只作展示。'
        +'要跑完整链路（含大模型重判命中）请把这套 JSON 提交到 /api/fair/profile。</span><br>'
      : '')
    +'写库：'+(P.saved_to_db
        ? '✓ 本次画像已写入 careersafari.db（'+e(P.db_fair_id)+'），匹配端已按它重建'
        : (P.db_fair_id ? '库内生效画像 '+e(P.db_fair_id)+'；本次会话尚未提交新画像'
                        : '离线模式：点下载 JSON 后运行 fair_ingest.py --file 即可入库'))+'<br>'
    +'采集端：'+e(P.major)+' / 勾选岗位 '+e((P.intended_jobs||[]).join('、')||'—')
      +' / 家乡 '+e(P.home_city)+' / 期望城市 '+e(P.region)
      +' / 权重 '+Object.keys(P.weights).map(function(k){return k+':'+P.weights[k];}).join('、')+'<br>'
    +'企业端：'+e(D.sources['企业端'])+'<br>匹配端：'+e(D.sources['匹配端'])
      +'；结论已与企业端 JD / 行业做事实校验<br>'
    +_cacheNote
    +'共 '+D.stats.companies+' 家 / '+D.stats.jobs+' 条岗位；明确命中 '+_hitN
      +' · 推断 '+_infN
      +(_customNow?'（按当前画像重算）':'')
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
    +'<b>各维权重微调</b>（改完立即重算，不必回采集端重新生成画像）：'
    + Object.keys(P.weights).filter(function(k){
        // 数据不可用的维度（当前是成长性）不参与微调：调它只会让分数虚降。
        return !(D.weights_disabled&&D.weights_disabled[k]);
      }).map(function(k){
        return '<span style="display:inline-block;margin:0 10px 0 0">'
          +e(DIM_LABEL[k]||k)+' <b>'+P.weights[k]+'</b> '
          +'<button type="button" class="btn btn-g" data-wk="'+k+'" data-wd="-5">−5</button> '
          +'<button type="button" class="btn btn-g" data-wk="'+k+'" data-wd="5">+5</button></span>';
      }).join('')
    + ' <button type="button" class="btn btn-g" data-wreset="1">恢复画像权重</button>'
    + (D.weights_disabled&&Object.keys(D.weights_disabled).length
        ? '<div class="note" style="font-size:12px;margin:6px 0 0">'
          + Object.keys(D.weights_disabled).map(function(k){
              return '<b>'+e(DIM_LABEL[k]||k)+'</b>：'+e(D.weights_disabled[k]);}).join('<br>')
          + '</div>' : '')
    + '</div>'
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
        + ' 位 · 排序分 '+cur.score+'　'+live
        + ' · 数据生成于 '+e(fmtGen(D.generated_at))+'</div>'
        + rowHTML(cur, t, P)
        + '<details style="margin:12px 0"><summary style="cursor:pointer;font-size:12px">'
        + '本页完整信息（统计 / 权重微调 / 筛选 / 导出 / 现场进度）</summary>'
        + _infoHtml + '</details>';
    }
  }else{
    /* 「共 50 家 / 572 条岗位」以前会被后半句挤成两行（「共 50 家 /」／
       「572 条岗位」），断在斜杠上很难读。这一小段不允许折行，让它整体换行。 */
    html='<div class="note" style="font-size:12px;line-height:1.75;margin:0 0 10px"><b style="white-space:nowrap">共 '
      + D.stats.companies+' 家 / '+D.stats.jobs+' 条岗位</b>　'+live
      + ' · 明确命中 '+_hitN+' / 推断 '+_infN
      + (_noN?'（其中 '+_noN+' 家的主营与你的方向不交叉，已折叠到列表底部）':'')
      /* 不在榜里的「不对口」不参与排名（硬过滤直接剔掉），没有可显示的计数；
         原来取 D.stats.states.miss 会渲染成 undefined。换画像后按当前结果重数。 */
      + (_customNow?'（按当前画像重算）':'')
      + ' · 意向岗位 '+e((P.intended_jobs||[]).join('、')||'—')
      + ' · 数据生成于 '+e(fmtGen(D.generated_at))+'</div>'
      + hardFilterNote(_customNow)
      + '<div class="btns" style="margin:0 0 12px">'
      + '<button type="button" class="btn '+(sortMode==='queue'?'btn-p':'btn-g')
        +'" data-sort="queue">按排名</button>'
      + '<button type="button" class="btn '+(sortMode==='booth'?'btn-p':'btn-g')
        +'" data-sort="booth">按展位排行</button>'
      + '<button type="button" class="btn '+(sortMode==='hit'?'btn-p':'btn-g')
        +'" data-sort="hit">明确命中优先</button>'
      + (sortMode==='hit'
        ? '<div class="note" style="font-size:12px;margin:8px 0 0">先排<b>明确命中</b>的 '
          +_hitN+' 家（组内按分），再排<b>推断待核实</b>的 '+_infN
          +' 家——推断企业分数再高也不越过命中企业。</div>'
        : '')
      + '</div>'
      + '<details style="margin:0 0 12px"><summary style="cursor:pointer;font-size:12px">'
      + '更多设置 / 完整信息（统计 · 权重微调 · 筛选 · 导出 · 现场进度）</summary>'
      // 演示/验收常要换一套画像看效果，入口放这里：学生正常浏览不会碰到。
      + '<div style="font-size:12px;margin:8px 0 10px">换个身份看效果：'
      + '<a href="/test_users.html" target="_blank">测试数据（3 套随机画像）</a>'
      + ' · <a href="/tests.html" target="_blank">测试记录</a></div>'
      + _infoHtml + '</details>';
  }
  // v12 测试数据库：把本次填写与排序结果存一条记录，供现场实验分析。
  // 只在自己跑实验时用，学生正常浏览不会误触（按钮在折叠区里，且要填标签才保存）。
  html+=testSaveBox(rows);
  let idx=0;
  // 排名模式不再按「命中/推断/不对口」分组渲染——那会把全局分数顺序打乱，
  // 出现第 4 名分数低于第 5 名的情况。现在只有一条连续的榜单，名次即分数序；
  // 岗位状态仍逐条显示在卡片上（展示，不影响名次）。
  /* 业务判定为「与你的方向无关」（biz_fit.fit='no'）的企业：不进排行，
     收到列表底部一个折叠组里。数据、展位号、导逛路线一律不动 ——
     0 岗 ≠ 不招，这里只是「这次别把排队时间花在那里」。
     换画像后 payload 的判定不再对应当前意向（state_recalc），就不折叠。 */
  const _noRows = rows.filter(function(r){
    return r.biz_fit && r.biz_fit.fit==='no' && !r.state_recalc;
  });
  const _mainRows = _noRows.length
    ? rows.filter(function(r){ return _noRows.indexOf(r)<0; })
    : rows;
  const groups = sortMode==='booth'
    ? [['t1','按会场序列号（展位号）顺序 · 走一遍不折返 · 评分照常显示', _mainRows]]
    : [['t1','按排序分从高到低 · 可信度与岗位状态仅作提示，不影响名次', _mainRows]];
  if(!DETAIL_NAME){
    groups.forEach(function(g){
      const rs=g[2];
      if(!rs.length) return;
      html+='<div class="tier '+g[0]+'"><div class="tier-h">'+g[1]+' <em>'+rs.length+' 家</em></div>';
      rs.forEach(function(r){ html+=rowBrief(r,idx++,P); });
      html+='</div>';
    });
    if(_noRows.length){
      html+='<details class="tier" style="margin:14px 0 0">'
        + '<summary class="tier-h" style="cursor:pointer">'
        + '与你的方向无关 <em>'+_noRows.length+' 家</em>'
        + '<span style="font-weight:400;font-size:12px;color:#8a94a6;margin-left:8px">'
        + '主营业务与你的意向方向不交叉，建议这个展位跳过；'
        + '展位号与岗位数据都还在，导逛路线不变</span></summary>'
        + '<div style="padding:8px 0 0">';
      _noRows.forEach(function(r){ html+=rowBrief(r,idx++,P); });
      html+='</div></details>';
    }
    /* 榜单末尾：结束使用按钮（一键存测试记录） */
    html+=endSessionBar();
  }
  currentRows=rows;
  suppress=true;
  box.innerHTML=html;
  // 详情页本来就是看细节的，折叠区默认展开。
  if(DETAIL_NAME) box.querySelectorAll('.jobbox details').forEach(function(d){ d.open=true; });
  bindTestSave(box);
  bindEndSession(box);
  /* 数据版本轮询：重建 payload 后，开着的报告页 15 秒内感知并自动刷新。
     只在独立报告视图（?report=1 / ?detail=）才自动 reload ——
     采集对话页里报告是内嵌挂载的，自动 reload 会把学生填到一半的对话清零
     （2026-11-15 修复：采集页只提示，不刷新）。 */
  (function(){
    if(window.__verPoll) return; window.__verPoll=1;
    const REPORT_VIEW = DETAIL_NAME || new URLSearchParams(location.search).get('report')==='1';
    setInterval(function(){
      if(document.hidden) return;
      fetch(SEM_BASE+'/api/fair/version').then(function(r){return r.json();}).then(function(v){
        if(v&&v.generated_at&&D.generated_at&&v.generated_at!==D.generated_at
           &&!window.__verWarn){
          window.__verWarn=1;
          const b=document.createElement('div');
          b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;'
            +'background:#fff7e6;border-bottom:1px solid #ffe0a3;padding:10px 14px;'
            +'font-size:13px;color:#b7791f';
          if(REPORT_VIEW){
            b.textContent='⚠ 数据已更新（'+fmtGen(v.generated_at)+'），3 秒后自动刷新页面载入新版结果……';
            document.body.appendChild(b);
            setTimeout(function(){location.reload();},3000);
          }else{
            b.textContent='ℹ 匹配端数据已更新（'+fmtGen(v.generated_at)+'）；本次不影响当前对话，稍后可在报告页查看新版结果。';
            document.body.appendChild(b);
            setTimeout(function(){ b.remove(); },8000);
          }
        }
      }).catch(function(){});
    },15000);
  })();
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

# —— 职能族表注入 ——
# 单一数据源在 job_family.FAMILIES：页面侧不手抄第二份，构建时序列化进来。
# 这样「Python 判定」和「JS 判定」共用同一张表，parity 门禁只需比对逻辑，不用比对数据。
import json as _json          # noqa: E402
import job_family as _job_family   # noqa: E402

REPORT_JS = REPORT_JS.replace(
    "__MI_FUNC__",
    _json.dumps(_job_family.JS_TABLE, ensure_ascii=False, separators=(",", ":")))
