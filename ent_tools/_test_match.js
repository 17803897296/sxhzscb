// 页面级测试：用 demo/fair.html 里真正的 matchIntent（含本地规则兜底）跑服务端 payload。
const fs = require('fs');
const http = require('http');

eval(fs.readFileSync(__dirname + '/_js_matchintent.js', 'utf8'));

function get(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', c => b += c); r.on('end', () => res(JSON.parse(b))); })
      .on('error', rej);
  });
}

let fails = 0;
function chk(ok, label, detail) {
  if (!ok) fails++;
  console.log((ok ? 'PASS ' : 'FAIL ') + label + (detail ? '  ' + detail : ''));
}

(async () => {
  const PORT = process.env.FAIR_PORT || 8102;   // 可用 FAIR_PORT=8199 跑临时服务
  const p = await get('http://127.0.0.1:' + PORT + '/api/fair/payload');
  const sem = p.intent_semantics || {};
  const intended = (p.profile && p.profile.intended_jobs) || [];
  const rows = p.rows || [];
  console.log('payload: rows=' + rows.length + '  fair=' + p.active_fair_id);
  console.log('意向：' + intended.join(' / '));
  console.log('语义映射键：' + Object.keys(sem).join(' / '));
  console.log('');
  /* 映射现在会把内置测试画像的意向一起跑（换画像时不必现场等模型），
     所以键数多于当前画像意向是正常的；要求是**当前画像每个意向都被覆盖**。 */
  const missingSem = intended.filter(function(x){ return !sem[x]; });
  chk(missingSem.length === 0, '语义映射覆盖当前画像 ' + intended.length + ' 个意向',
    missingSem.length ? ('缺 ' + missingSem.join('、')) : (Object.keys(sem).length + ' 个键（含测试画像，可多于画像）'));

  const hitsByIntent = {};
  for (const it of intended) {
    const m = sem[it] || {};
    const words = m.titles || [];
    const hitJobs = new Set();
    let n = 0;
    for (const r of rows) {
      const one = matchIntent(r.matched_jobs || [], intended, sem, r.job_titles || []).filter(x => x.intent === it)[0];
      if (one) { n++; hitJobs.add(one.job + '(' + one.via + ')'); }
    }
    hitsByIntent[it] = [...hitJobs];
    chk(words.length > 0 && n > 0,
      it + '  语义 ' + words.length + ' 条 · 命中企业 ' + n + '/' + rows.length,
      [...hitJobs].slice(0, 4).join('、'));
  }

  console.log('\n[反例] 历史误判不得出现');
  const FORBID = {
    '供应链管培生': ['战略管培生', 'IT数字化管培生', '营销管培生', '会计信息化管培生', '临床医学专员'],
    '采购寻源专员': ['战略管培生', 'KA销售管培生'],
    '数据分析岗': ['数据开发工程师', 'DBA'],
    '供应链计划专员': ['启明星计划竞赛教练'],
    '运营管理培训生': ['综合柜员'],
    // 并列方向不是「同职能不同叫法」：后端 × 量化 / 前端 / 嵌入式 / Unity / 大模型
    '后端开发工程师': ['量化开发工程师', '前端开发工程师', 'AI系统开发工程师', '低代码开发工程师'],
  };
  for (const it of Object.keys(FORBID)) {
    const s = (sem[it] && sem[it].titles) || [];
    const got = s.filter(t => FORBID[it].some(b => t === b || t.indexOf(b) >= 0));
    chk(got.length === 0, it + ' 未混入 ' + FORBID[it].join('/'), got.join('、'));
  }

  console.log('\n[reason] 必须能解释');
  for (const it of intended) {
    const rs = (sem[it] && sem[it].reasons) || {};
    const n = Object.keys(rs).length;
    const need = Math.min(3, ((sem[it] && sem[it].titles) || []).length);
    chk(n >= need, it + '  reason ' + n + ' 条', Object.values(rs)[0] || '');
  }

  console.log('\n[兜底] payload 未覆盖该意向时，本地规则要与后端同源');
  const fb = [
    // [意向, 企业岗位清单, 期望命中(null=不该命中)]
    ['供应链管培生', ['战略管培生'], null],
    ['供应链管培生', ['IT数字化管培生'], null],
    ['供应链管培生', ['会计信息化管培生'], null],
    ['供应链管培生', ['供应链事业群管培生-供应链运营方向'], '供应链事业群管培生-供应链运营方向'],
    ['采购寻源专员', ['供应商质量管理工程师'], null],
    ['采购寻源专员', ['物流仓储储备人员'], '物流仓储储备人员'],
    ['新媒体运营专员', ['图书直播与短视频出镜专员'], '图书直播与短视频出镜专员'],
    ['新媒体运营专员', ['市场营销'], null],
    ['质量工程师', ['原料药质量研究员'], '原料药质量研究员'],
    // 标准化 ≠ 质量：一个是体系/标准文本，一个是检验与过程管控，不能当同职能
    ['质量工程师', ['标准化工程师'], null],
    ['外贸业务员', ['KA销售管培生'], 'KA销售管培生'],
    ['外贸业务员', ['国际市场营销'], '国际市场营销'],
    ['外贸业务员', ['外贸仓库科长'], null],
    ['外贸业务员', ['销售总监（海外业务）'], null],
    ['外贸业务员', ['海外技术支持工程师'], null],
    ['数据分析岗', ['数据开发工程师'], null],
    ['中学语文教师', ['初中学习机教师(语文、数学、英语)'], '初中学习机教师(语文、数学、英语)'],
    ['中学语文教师', ['化学教师'], null],
    // 电气 / 机械是两个专业方向（同属硬件，但不是同职能）——旧夹具这两条写反了
    ['电气工程师', ['机械设计工程师'], null],
    ['机械设计工程师', ['模拟IC设计工程师'], null],
    ['机械设计工程师', ['光学设计工程师'], null],
    ['机械设计工程师', ['结构设计工程师'], '结构设计工程师'],
    ['管培生', ['管培生'], '管培生'],
    // 大类不能反推小类：「算法工程师」是「AI算法研究工程师」的上位类，
    // 意向是细分方向时，光写「算法工程师」的岗位不算对口（转现场提问）。
    ['AI算法研究工程师', ['算法工程师'], null],
    ['AI算法研究工程师', ['算法研究员'], null],
    ['AI算法研究工程师', ['AI算法研究工程师'], 'AI算法研究工程师'],
    // 反过来：意向是大类、岗位是细分 —— 小类可以进大类
    ['算法工程师', ['AI算法研究工程师'], 'AI算法研究工程师'],
    // 笼统软件意向仍可投细分方向岗（规则 4c 不受上面收紧影响）
    ['软件开发工程师', ['前端工程师'], '前端工程师'],
    // 级别词不是方向：「初级测试工程师」的方向就是「测试」，与「测试工程师」同级
    ['初级测试工程师', ['测试工程师'], '测试工程师'],
    // 同一个大类下的两个方向，不能互相顶替（后端 ↔ 量化 / 前端 / Unity）
    ['后端开发工程师', ['量化开发工程师'], null],
    ['后端开发工程师', ['前端开发工程师'], null],
    ['后端开发工程师', ['Unity3d程序开发工程师'], null],
    ['后端开发工程师', ['嵌入式软件'], null],
    // 意向没写方向时，细分方向可以进（管培生 → 供应链管培生 同理）
    ['开发工程师', ['量化开发工程师'], '量化开发工程师'],
    ['开发工程师', ['嵌入式软件'], '嵌入式软件'],
    // 技术栈不是方向：换语言不换路
    ['后端开发工程师', ['C++开发工程师'], 'C++开发工程师'],
  ];
  for (const [intent, pools, expect] of fb) {
    const got = matchIntent(pools, [intent], {}, pools)[0];
    const job = got ? got.job : null;
    chk(job === expect, '兜底 ' + intent + ' × ' + pools[0], '→ ' + (job || '不命中') +
      (got ? ' [' + got.via + '] ' + got.reason : ''));
  }

  console.log('\n==== 失败项 ' + fails + ' ====');
  process.exit(fails ? 1 : 0);
})();
