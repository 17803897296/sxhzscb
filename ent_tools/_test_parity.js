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
