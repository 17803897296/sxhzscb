// 前后端同源校验：JS 本地规则 vs Python intent_semantics，逐对比较。
const fs = require('fs');
eval(fs.readFileSync(__dirname + '/_js_matchintent.js', 'utf8'));

const d = JSON.parse(fs.readFileSync(__dirname + '/data/_parity.json', 'utf8'));
let diff = [], total = 0, agree = 0;
const byIntent = {};
for (const it of d.ints) {
  byIntent[it] = [];
  for (const t of d.titles) {
    const py = !!d.mat[it][t];
    const js = !!(miRole(it, t) && miShare(it, t));
    total++;
    if (py === js) { agree++; continue; }
    byIntent[it].push({ t, py, js });
    diff.push([it, t, py, js]);
  }
}
console.log('比较 ' + total + ' 对，一致 ' + agree + '，不一致 ' + diff.length +
  '（一致率 ' + (100 * agree / total).toFixed(2) + '%）');
for (const it of d.ints) {
  if (!byIntent[it].length) continue;
  console.log('\n[' + it + '] ' + byIntent[it].length + ' 处不一致');
  byIntent[it].slice(0, 12).forEach(x =>
    console.log('   ' + (x.py ? 'Py有/JS无' : 'Py无/JS有') + '  ' + x.t));
}
fs.writeFileSync(__dirname + '/data/_parity_diff.json', JSON.stringify(diff, null, 1));

/* 方向判定（pair_verdict: ok/ask/no）必须逐对同源：
   命中最终结论 = role && share && verdict==='ok'，只比前两项会漏掉
   「电磁设计 × 机械设计」这类 role/share 都对、方向不同的错配。 */
let vdiff = [];
if (d.ver) {
  for (const it of d.ints) {
    for (const t of d.titles) {
      const py = d.ver[it][t], js = miPairVerdict(t, it);
      if (py !== js) vdiff.push([it, t, py, js]);
    }
  }
  console.log('\n方向判定：' + (d.ints.length * d.titles.length) + ' 对，不一致 ' + vdiff.length);
  vdiff.slice(0, 12).forEach(x =>
    console.log('   ' + x[0] + ' × ' + x[1] + '  py=' + x[2] + ' js=' + x[3]));
}

/* 轮岗类判定（is_rotation_job vs miIsRotation）必须同源：
   它决定「值得问一句」只问管培生 / 培训生 / 储备干部 / 校招生，
   前端判错就会问出后端不会问的「研究员是否按 X 方向培养」。 */
if (d.rot) {
  const rdiff = [];
  for (const t of d.titles) {
    const py = !!d.rot[t], js = !!miIsRotation(t);
    if (py !== js) rdiff.push([t, py, js]);
  }
  console.log('\n轮岗类判定：' + d.titles.length + ' 个岗位，不一致 ' + rdiff.length);
  rdiff.slice(0, 10).forEach(x =>
    console.log('   ' + x[0] + '  py=' + x[1] + ' js=' + x[2]));
  if (rdiff.length) process.exit(1);
}
if (vdiff.length) process.exit(1);
