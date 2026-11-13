// 契约校验：node check.js index.html
// 按「企业画像采集端 v1.1」的 9 个采集字段 + 4 个技术字段校验。
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const script = src.match(/<script>([\s\S]*?)<\/script>/)[1];

function grab(name){
  const i = script.indexOf("const " + name + " =");
  if (i < 0) throw new Error("not found: " + name);
  const j = script.indexOf("[", i);
  let depth = 0, k = j;
  for (; k < script.length; k++){
    if (script[k] === "[") depth++;
    else if (script[k] === "]") { depth--; if (depth === 0) break; }
  }
  return eval(script.slice(j, k + 1));
}

const C = grab("COMPANIES"), R = grab("RESULTS");

const E = {
  nature:   ["央企","国企","民企","外企","合资","事业单位"],
  industry: ["制造","医药","IT","金融","建筑","能源","消费","物流","教育","政府事业","其他"],
  urgency:  ["高","中","低","未知"],
  jobSource:["校招公告","社招信息","主营业务推测","未知"],
  tier:     ["优先去","可以看","建议放弃"]
};

const err = [];
const req = (obj, fields, label) => {
  for (const f of fields)
    if (obj[f] === undefined || obj[f] === null || obj[f] === "")
      err.push(`${label}.${f} 缺失`);
};

if (new Set(C.map(c => c.id)).size !== C.length) err.push("企业 id 重复");
if (new Set(R.map(r => r.id)).size !== R.length) err.push("结果 id 重复");

// ---------- 企业 ----------
for (const c of C){
  req(c, ["id","name","nature","city","industry","jobSource","source","date"], c.id || "(无 id)");

  if (!E.nature.includes(c.nature))     err.push(`${c.id}.nature 越界: ${c.nature}`);
  if (!E.industry.includes(c.industry)) err.push(`${c.id}.industry 越界: ${c.industry}`);
  if (!E.jobSource.includes(c.jobSource)) err.push(`${c.id}.jobSource 越界: ${c.jobSource}`);

  if (!Array.isArray(c.jobs)) { err.push(`${c.id}.jobs 不是数组`); continue; }

  // 证据等级与岗位的对应关系
  if (c.jobSource === "未知" && c.jobs.length)
    err.push(`${c.id}: jobSource=未知 但仍有岗位`);
  if (c.jobSource !== "未知" && !c.jobEvidence)
    err.push(`${c.id}: 非未知来源缺 jobEvidence`);
  if (c.jobSource === "校招公告" && !c.jobs.length)
    err.push(`${c.id}: 校招公告却没记录岗位`);

  // 岗位字段
  for (const j of c.jobs){
    if (!j.title) err.push(`${c.id}: 岗位缺 title`);
    if (!E.urgency.includes(j.urgency))
      err.push(`${c.id}/${j.title}: urgency 越界: ${j.urgency}`);
    if (j.headcount !== "未知" && !(Number.isInteger(j.headcount) && j.headcount > 0))
      err.push(`${c.id}/${j.title}: headcount 应为正整数或"未知": ${j.headcount}`);
    for (const f of ["requirements","group","salary"])
      if (j[f] === undefined || j[f] === null || j[f] === "")
        err.push(`${c.id}/${j.title}: ${f} 缺失（查不到请写"未知"）`);
  }

  // 重复岗位名
  const titles = c.jobs.map(j => j.title);
  if (new Set(titles).size !== titles.length)
    err.push(`${c.id}: 同一企业内岗位重名`);
}

// ---------- 结果 ----------
for (const r of R){
  req(r, ["id","tier"], r.id || "(无 id)");
  if (!E.tier.includes(r.tier)) err.push(`${r.id}.tier 越界: ${r.tier}`);
  if (typeof r.score !== "number") err.push(`${r.id}.score 非数字`);
  if (!Array.isArray(r.reasons)) err.push(`${r.id}.reasons 非数组`);
  if (!Array.isArray(r.gaps))    err.push(`${r.id}.gaps 非数组`);
  if (!C.some(c => c.id === r.id)) err.push(`${r.id} 在结果中但不在企业表`);
}

const orphan = C.filter(c => !R.some(r => r.id === c.id));
if (orphan.length) err.push("企业无结果: " + orphan.map(c => c.id).join(","));

// ---------- 摘要 ----------
const count = (arr, key) => arr.reduce((a, o) => (a[o[key]] = (a[o[key]] || 0) + 1, a), {});
const jobs = C.reduce((n, c) => n + c.jobs.length, 0);

console.log("企业:", C.length, "| 岗位:", jobs, "| 结果:", R.length);
console.log("分档:", JSON.stringify(count(R, "tier")));
console.log("企业类型:", JSON.stringify(count(C, "nature")));
console.log("岗位来源:", JSON.stringify(count(C, "jobSource")));
console.log(err.length ? "❌ " + err.join("\n❌ ") : "✅ 契约校验全部通过");
process.exit(err.length ? 1 : 0);
