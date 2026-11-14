/* 自动生成，勿手改：抽自 fair_report_js.py 的页面规则块，由 refresh_all.py 每次重新生成 */
const MI_GEN=['专员','管培','培生','培训','训生','管理','助理','储干','干部','工程','经理','主管','储备','专工',
  /* 「分析」「研究」不携带方向：策略分析岗 ≠ 分析仪器工程师；
     行业研究岗 ≠ AI算法研究工程师 / 有机合成研究员。 */
  '分析','研究'];
/* 岗位名尾部后缀字：跨词边界的 2-gram 常以它们收尾（理岗 / 源专 / 究员），
   只共享到这种片段时不算同职能证据。与 intent_semantics.BOUNDARY_TAIL 同源。 */
const MI_TAIL='岗员师专士长工部科组队站店厂所司局院校';
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
  return b.indexOf(a)>=0 && a.length<b.length;
}
/* 并列方向词：同属一个大类、却是两条不同职业路径的限定词。
   换技术栈不算换方向（Java / Python / C++ 不在表内），机械/结构/电气也不在内
   ——「机械设计工程师」和「结构设计工程师」是同一岗位的两种写法。
   与 intent_semantics.DIR_WORDS 同源。 */
const MI_DIRWORDS=['前端','后端','服务端','客户端','嵌入式','上位机','下位机','固件',
  '移动端','桌面','驱动','全栈','低代码','量化','算法','数据','图像','视觉','图形','游戏',
  'unity','unreal','大模型','ai','人工智能','测试','运维','安全','网络','通信'];
const MI_DIRNEUTRAL=['开发','研发','软件','程序','工程师','技术','应用','系统','岗','员'];
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
function miPairVerdict(title,intent){
  const a=miDirCore(intent), b=miDirCore(title);
  if(!a.t||!b.t||a.t===b.t) return 'ok';
  /* 注意方向：a=意向方向，b=岗位方向。
     b ⊃ a → 岗位更细（测试 → 器件测试）→ ask；
     b ⊂ a → 岗位更泛（AI算法研究 → 算法）→ ok（小类能进大类）。 */
  if(a.t.indexOf(b.t)>=0) return 'ok';
  if(b.t.indexOf(a.t)>=0) return 'ask';
  if(a.k.length&&b.k.length&&!a.k.some(function(w){ return b.k.indexOf(w)>=0; })) return 'no';
  return 'ok';
}
/* 方向不一样时，不能说成「另一种说法」：写清是哪个大类下面的哪个方向。
   与 intent_semantics._reason_by_direction 同源。 */
function miDirReason(t,intent){
  const a=miDirCore(intent), b=miDirCore(t);
  if(!b.t||a.t===b.t||(a.t&&b.t.indexOf(a.t)>=0)) return '';
  if(a.k.length&&b.k.length&&!a.k.some(function(w){ return b.k.indexOf(w)>=0; }))
    return '「'+t+'」与「'+intent+'」同属一个大类但方向不同（'+b.t+' × '+a.t+'）';
  if(!a.t) return '「'+t+'」是「'+intent+'」下的'+b.t+'方向（同一大类，不是同一细分）';
  if(a.t&&b.t.indexOf(a.t)>=0) return '「'+t+'」的方向是'+b.t+'，比「'+intent+'」细，不是同一个岗位';
  return '';
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
/* 同义写法归一：「营运」就是「运营」（营运管培生 / 营运中台管培生） */
function miSyn(s){ return String(s||'').replace(/营运/g,'运营'); }
function miFam(t){ const o=[]; for(const k in MI_FAM){ if(miHas(t,MI_FAM[k])) o.push(k); } return o; }
function miShare(a,b){
  a=miSyn(a); b=miSyn(b);
  const g=miGrams(a).filter(function(x){ return MI_GEN.indexOf(x)<0; });
  /* 2 字窗口会跨词边界造假共享：「风险管理岗」的「理岗」落在「施工管理岗」尾部，
     「采购寻源专员」的「源专」落在「人力资源专员」中间。以岗位后缀字收尾的共享
     不算证据（见 intent_semantics.BOUNDARY_TAIL）。 */
  const shared=g.filter(function(x){ return b.indexOf(x)>=0; });
  if(shared.some(function(x){ return MI_TAIL.indexOf(x.slice(-1))<0; })) return true;
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
    if(t===a || t.indexOf(a)>=0 || (t.length>=4 && a.indexOf(t)>=0)) return t;
    /* 大类岗位名：只有公司业务就是这个细分时才算对口（供应链公司的管培生），
       否则留给 miAskJobs 变成现场提问。 */
    if(miBareGeneric(t) && !miBareGeneric(a)){
      if(miBizOk(a,biz)) return t;
      continue;
    }
    /* 岗位名比意向宽一层（算法工程师 × AI算法研究工程师）：不算对口，
       留给 miAskJobs 变成现场提问；除非公司业务就是这个细分。 */
    if(miWider(t,a)){
      if(miBizOk(a,biz)) return t;
      continue;
    }
    if(miPairVerdict(t,a)==='no') continue;   /* 并列方向不算命中（后端 × 量化 / 前端） */
    if(miRole(a,t) && miShare(a,t)) return t;
  }
  return null;
}

/* 大类岗位名（管培生 / 储备干部 / 研究员）：不是对口证据，但值得现场问一句。 */
function miAskJobs(intended,titles,biz){
  const out=[], seen={};
  (titles||[]).forEach(function(t){
    if(!t || seen[t] || !miIsJob(t)) return;
    for(let i=0;i<(intended||[]).length;i++){
      const a=intended[i];
      if(!a || miBareGeneric(a) || miBizOk(a,biz)) continue;
      const bare=miBareGeneric(t);
      /* 岗位比意向宽一层（算法工程师 × AI算法研究工程师）也值得问一句 */
      const wider=!bare && miWider(t,a) && miRole(a,t) && miShare(a,t);
      if(!bare && !wider) continue;
      seen[t]=1;
      out.push({job:t, intent:a,
        why: bare
          ? ('岗位名只写「'+t+'」，没写业务方向；不同企业的「'+t
             +'」按不同条线培养。现场问一句：是招「'+a+'」这个方向，还是统一轮岗后再定岗。')
          : ('岗位名「'+t+'」只写到「'+(miDirText(t)||t)+'」这一层，比「'+a
             +'」宽，看不出是不是这个细分方向。现场问一句：这个岗具体做「'+a+'」吗。')});
      return;
    }
  });
  return out.slice(0,3);
}

function matchIntent(titles,intended,sem,jobTitles,biz){
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
            /* 只拦并列方向（后端 × 量化 / 前端 / 嵌入式）：同大类两条路，不能顶替。
               岗位更细（测试 → 器件测试）照旧算命中，理由里写清方向不同。 */
            && miPairVerdict(t,x)!=='no';
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
            && miPairVerdict(t,x)!=='no';
        });
        if(kw.length){ hitJob=kw[0]; via='llm-term'; reason=m.reason||'同属一类校招岗位'; }
      }
    }
    /* 该意向已有大模型判定（且判定出了同类岗位）却没匹配上时，不再走兜底：
       否则「管培生」会因与「供应链管培生」共享「管培」二字被误判为命中。
       兜底只用于 sem 里没覆盖到这个意向（新勾岗位 / 换学生 / 模型失败）的情况，
       规则与后端 intent_semantics 同源，判完写明是本地规则，不冒充大模型。 */
    const covered = m && m.via==='llm' && (m.titles||[]).length>0;
    if(!hitJob && !covered){
      const t=miMatch(x,pool,biz);
      if(t){
        hitJob=t;
        via='rule';
        reason=(t===x) ? '字面同名'
          : (t.indexOf(x)>=0 ? '岗位名里含你的意向岗位名'
             : '本地规则判定：同职能不同叫法');
      }
    }
    if(hitJob) out.push({intent:x, job:hitJob, via:via, reason:reason});
  });
  return out;
}
