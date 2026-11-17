// 排序范围口径自测：名单里有什么就排什么，名单外的内置企业不许混进榜。
// 跑法（ent_tools 下）：node _test_roster_pool.js
// 离线跑，不需要起服务：把页面 JS 抽出来，喂一份小规模假企业数据。
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;

// 两个假企业库：A 在场内、B 是「上一场/内置库里」的干扰项
const COMPANIES = [
  { id: 'a1', name: '南京甲材料有限公司', city: '南京', nature: '国企', industry: '制造',
    jobs: [{ title: '材料研发工程师', urgency: '高', requirements: '材料专业，双休' }], jobSource: '官网' },
  { id: 'a2', name: '苏州乙电子有限公司', city: '苏州', nature: '民企', industry: '电子',
    jobs: [{ title: '工艺工程师', urgency: '中', requirements: '电子专业' }], jobSource: '官网' },
  { id: 'b1', name: '北京丙保险有限公司', city: '北京', nature: '央企', industry: '金融',
    jobs: [{ title: '管培生', urgency: '低', requirements: '不限' }], jobSource: '官网' },
  { id: 'b2', name: '上海丁咨询有限公司', city: '上海', nature: '外企', industry: '咨询',
    jobs: [{ title: '咨询顾问', urgency: '中', requirements: '英语好' }], jobSource: '官网' },
];
const BUILTIN = COMPANIES.map(c => c.name);

let src = fs.readFileSync(path.join(ROOT, 'js/match.js'), 'utf8');
src = src.replace(/<style>[\s\S]*?<\/style>/g, '')
         .replace(/<script>/g, '')
         .replace(/<\/script>/g, '');
src = src.replace('__COMPANIES__', JSON.stringify(COMPANIES));
src = src.replace('__NAMES__', JSON.stringify(BUILTIN));

global.window = {};
global.document = {
  getElementById: () => null,
  createElement: () => ({ style: {}, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [] }),
  addEventListener() {}, querySelector: () => null, body: { appendChild() {} },
};
global.localStorage = { getItem: () => null, setItem() {} };
global.fetch = () => Promise.reject(new Error('offline'));
eval(src);

let fails = 0;
function chk(ok, label, detail) {
  if (!ok) fails++;
  console.log((ok ? 'PASS ' : 'FAIL ') + label + (detail ? '  ' + detail : ''));
}

const PROF = { values: {}, target_company_types: [], home_city: '未知',
               direction_interest: '材料', major_fit: '只看专业对口的', major: '材料' };

/* ① 名单里的企业原样进池，顺序跟名单一致 */
const roster1 = ['南京甲材料有限公司', '苏州乙电子有限公司'];
const pool1 = rosterPool(roster1);
chk(pool1.length === 2, '名单 2 家 → 池 2 家', '实际 ' + pool1.length);
chk(pool1.map(c => c.name).join(',') === roster1.join(','), '池顺序与名单一致');
chk(pool1.every(c => !c.roster_stub), '名单内企业都拿到了画像');

/* ② 名单外的内置企业不许出现（这次报的错就是它） */
const rows1 = match(pool1, PROF);
chk(rows1.length === 2, '匹配结果只有名单内 2 家', '实际 ' + rows1.length);
chk(rows1.every(r => roster1.includes(r.name)), '结果里没有北京丙/上海丁');
chk(!rows1.some(r => ['北京丙保险有限公司', '上海丁咨询有限公司'].includes(r.name)),
  '名单外的内置企业 0 家混入');

/* ③ 名单里有、库里没有的企业：给占位，进「资料不全」，不许被丢掉 */
const roster2 = ['南京甲材料有限公司', '杭州戊新材料有限公司'];
const pool2 = rosterPool(roster2);
chk(pool2.length === 2, '名单 2 家（1 家库里没有）→ 池 2 家');
const stub = pool2.find(c => c.name === '杭州戊新材料有限公司');
chk(!!stub && stub.roster_stub === true, '库里没有的那家是占位');
chk(!!stub && stub.jobs.length === 0, '占位没有岗位');
const rows2 = match(pool2, PROF);
const stubRow = rows2.find(r => r.name === '杭州戊新材料有限公司');
chk(!!stubRow && stubRow.tier === '资料不全', '占位落「资料不全」，没被丢掉',
  stubRow ? stubRow.tier : '没出现');
chk(rows2.filter(r => ['优先去', '可以看', '建议放弃'].includes(r.tier)).length === 1,
  '推荐三档只由名单里有画像的那家占位');

/* ④ 不录名单 = 用内置名单，行为跟以前一致 */
const pool4 = rosterPool(BUILTIN);
chk(pool4.length === COMPANIES.length, '没录名单时池 = 内置 ' + COMPANIES.length + ' 家',
  '实际 ' + pool4.length);
const rows4 = match(pool4, PROF);
chk(rows4.length === COMPANIES.length, '结果 = 内置名单全量');

/* ⑤ 名单里的重复名不会让同一家排两次 */
const pool5 = rosterPool(['南京甲材料有限公司', '南京甲材料有限公司']);
chk(pool5.length === 2, '重复名由名单解析去重（这里只验池不少给）');

console.log('');
console.log(fails ? ('失败 ' + fails + ' 条') : '全部通过');
process.exit(fails ? 1 : 0);
