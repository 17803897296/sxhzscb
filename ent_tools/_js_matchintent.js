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
function miPairVerdict(title,intent){
  const a=miDirCore(intent), b=miDirCore(title);
  if(!a.t||!b.t||a.t===b.t) return 'ok';
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
     b ⊂ a → 岗位更泛（AI算法研究 → 算法）→ ok（小类能进大类）。 */
  if(a.t.indexOf(b.t)>=0) return 'ok';
  /* 职责词不算方向：设备维护工程师 × 设备工程师 → 同一岗位，不是「更细的方向」 */
  let b2=b.t; MI_DUTY.forEach(function(w){ b2=b2.split(w).join(''); });
  if(b2 && b2===a.t) return 'ok';
  if(b.t.indexOf(a.t)>=0) return 'ask';
  if(a.k.length&&b.k.length&&!a.k.some(function(w){ return b.k.indexOf(w)>=0; })){
    if(miSameLine(a.k,b.k)) return 'ok';   /* 采购/物流/仓储 ⊂ 供应链：同一条线照旧算 */
    return 'no';
  }
  return 'ok';
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
    /* 纯大类里只留轮岗类（管培生 / 培训生 / 储备干部 / 校招生）；
       研究员 / 助理工程师 / 分析师 / 专员 / 经理 是定向招聘，不问。
       与 intent_semantics.ask_jobs 同源。 */
    const bare=miBareGeneric(t);
    if(bare && !miIsRotation(t)) return;
    for(let i=0;i<(intended||[]).length;i++){
      const a=intended[i];
      if(!a || miBareGeneric(a) || miBizOk(a,biz)) continue;
      /* 企业主营和这个意向方向毫不相关 → 连问一句都不该有
         （保险公司 / 教育公司的管培生不会定向培养成化学工程师）。 */
      if(!miBizRelated(a,biz)) continue;
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

/* 命中复核表（构建期由大模型成批判好）：{"意向||企业岗位名": 0|1}。
   0 = 复核否决，不算命中；1 = 确认同类；键不存在 = 未复核（模型不可用 / 该对不在
   候选集）。未复核的继续用本地规则，但文案如实写「未复核」，不冒充复核结论。 */
function hvGet(intent,job){
  const o=(typeof D!=='undefined'&&D&&D.hit_verdicts)||{};
  const k=String(intent)+'||'+String(job);
  return Object.prototype.hasOwnProperty.call(o,k) ? o[k] : null;
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
            /* 并列方向（后端 × 量化 / 前端 / 嵌入式）：同大类两条路，不能顶替。
               岗位更细（测试 → 器件测试）原本算命中，但**复核 1 只是「确认同类」，
               不能把 ask 提升成命中**——否则复核表一换，页面命中数就与 payload
               不一致（2026-11-15 实测：客诉质量 × 质量 被判 ask，后端 drop、
               前端按复核 1 算命中，10 家分歧由此而来）。 */
            && miPairVerdict(t,x)!=='no'
            && !(miPairVerdict(t,x)==='ask' && hvGet(x,t)===1)
            /* 复核否决的跨方向岗位不能算命中（食品研发 × 药品研发 只共享「研发」） */
            && hvGet(x,t)!==0;
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
            && !(miPairVerdict(t,x)==='ask' && hvGet(x,t)===1)
            && hvGet(x,t)!==0;
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
         复核结论 1 → 确认命中并标明是大模型复核的；0 → 跳过这个候选；
         没有结论 → 收下，但文案写「未复核」，不冒充复核过。 */
      let t=null, v=null;
      for(let i=0;i<pool.length;i++){
        const cand=pool[i];
        if(!cand || cand===hitJob) continue;
        /* 字面同名不需要复核，也不许写成「大模型复核过」——它本来就是个同名岗位。 */
        const cv=(cand===x) ? null : hvGet(x,cand);
        if(cv===0) continue;
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
    if(hitJob) out.push({intent:x, job:hitJob, via:via, reason:reason});
  });
  return out;
}
