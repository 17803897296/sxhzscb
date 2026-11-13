# -*- coding: utf-8 -*-
"""
双选会助手 Demo v2 · 生成器

运行：
    cd ent_tools
    python -X utf8 demo_v2/build_demo.py

产出：
    demo_v2/job_family_map.json   固化的岗位族映射（模拟采集阶段模型输出）
    demo_v2/self_check.json       启动自检结果
    demo_v2/demo_v2.html          单文件离线页面（数据内联，不调模型）
    桌面/双选会助手_DemoV2.html    同上，方便双击

不改动原有 portal.py / fair.html / questionnaire.json / 任何契约字段。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules as R
import core

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def main():
    companies, booth_by_name = core.load_data()
    family_map = core.build_family_map(companies)
    payload, _ = core.build_payload(companies, booth_by_name, family_map)
    check = core.self_check(payload, family_map)

    # 固化落盘（4.6 八、：运行时只查表，不调用模型）
    with open(os.path.join(HERE, "job_family_map.json"), "w", encoding="utf-8") as f:
        json.dump({"version": R.VERSION, "entries": family_map}, f,
                  ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "self_check.json"), "w", encoding="utf-8") as f:
        json.dump(check, f, ensure_ascii=False, indent=1)

    # Python 侧用默认配置试算，验证计分口径
    weights = {d["id"]: d["default"] for d in R.DIMENSIONS}
    label_only = set(check["label_only_dimensions"])
    intended = {"TECH"}
    queue, excluded = core.apply_classification(payload, intended, family_map)
    for rec in queue:
        rec["_demo_score"] = core.score(rec, weights, R.CITY_TEMPLATES["均衡"],
                                        "南京", {"IT"}, R.SCORING, label_only)
    queue.sort(key=lambda r: -r["_demo_score"]["score"])

    print("=== 启动自检 ===")
    print(f"企业 {check['companies']} 家 / 岗位 {check['jobs']} 条 / 去重岗位名 {check['distinct_titles']}")
    print(f"族分布 {check['family_distribution']}  单族占比 {check['top_family_share']}")
    print(f"未命中 {len(check['unmapped_titles'])} 条  校验问题 {check['validation_issues']}")
    print(f"城市可用 {check['city_available']}  企业类型可用 {check['company_type_available']}")
    print(f"降级为标签的维度 {check['label_only_dimensions']}")
    print(f"默认配置四分支：队列 {len(queue)} 家 / 排除 {len(excluded)} 家")
    print("默认权重前 5 家：")
    for r in queue[:5]:
        print(f"  {r['_demo_score']['score']:6.2f}  {r['name'][:22]:24s} {r['state']:15s} "
              f"生效权重 {r['_demo_score']['effective_weight']}%")

    for rec in payload:
        rec.pop("_demo_score", None)

    cfg = {
        "version": R.VERSION,
        "families": R.FAMILIES,
        "familyOrder": R.FAMILY_ORDER,
        "dimensions": R.DIMENSIONS,
        "cityTemplates": R.CITY_TEMPLATES,
        "cityTierDefault": R.CITY_TIER_DEFAULT,
        "companyTypeTier": R.COMPANY_TYPE_TIER,
        "homeTier": R.HOME_TIER,
        "cityProvince": R.CITY_PROVINCE,
        "industryMap": R.INDUSTRY_MAP,
        "questionBank": R.QUESTION_BANK,
        "scoring": R.SCORING,
        "labelOnly": check["label_only_dimensions"],
        "industries": sorted({r["industry"] for r in payload if r["industry"] != "未知"}),
        "cities": sorted({r["dims"]["city"]["city"] for r in payload
                          if r["dims"]["city"]["city"]}),
        "selfCheck": check,
    }
    data = {"config": cfg, "companies": payload, "familyMap": family_map}

    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out = os.path.join(HERE, "demo_v2.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    if os.path.isdir(DESKTOP):
        shutil.copyfile(out, os.path.join(DESKTOP, "双选会助手_DemoV2.html"))
    print(f"\n已生成：{out}")
    if os.path.isdir(DESKTOP):
        print(f"桌面副本：{os.path.join(DESKTOP, '双选会助手_DemoV2.html')}")


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>双选会助手 Demo v2 · 会前规划队列</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6eb;--txt:#1c2430;--sub:#6b7684;
--blue:#2563eb;--blue-l:#eff4ff;--green:#0f9960;--green-l:#eaf7f1;
--amber:#b7791f;--amber-l:#fff6e5;--red:#c0392b;--red-l:#fdecea;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:#fff;border-bottom:1px solid var(--line);padding:16px 22px}
h1{margin:0;font-size:19px;font-weight:650}
.sub{color:var(--sub);font-size:12px;margin-top:3px}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
.chip{background:var(--blue-l);color:var(--blue);border:1px solid #d6e4ff;
border-radius:12px;padding:2px 9px;font-size:11px}
.chip.warn{background:var(--amber-l);color:var(--amber);border-color:#f3dfb0}
.wrap{display:flex;gap:18px;padding:18px 22px;align-items:flex-start}
aside{width:310px;flex:0 0 310px;position:sticky;top:14px;
background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px}
main{flex:1;min-width:0}
.sec{margin-bottom:16px}
.sec h3{font-size:12px;color:var(--sub);font-weight:600;letter-spacing:.4px;
margin:0 0 7px;text-transform:uppercase}
.row{display:flex;align-items:center;gap:8px;margin:5px 0}
.row label{flex:0 0 108px;font-size:12px}
.row input[type=range]{flex:1;min-width:0}
.row .v{flex:0 0 30px;text-align:right;font-variant-numeric:tabular-nums;font-size:12px}
select,input[type=text],input[type=number]{width:100%;padding:5px 7px;border:1px solid var(--line);
border-radius:6px;background:#fff;color:var(--txt);font-size:12px}
.fams{display:flex;flex-wrap:wrap;gap:5px}
.fam{border:1px solid var(--line);border-radius:14px;padding:3px 9px;font-size:11px;
cursor:pointer;background:#fff;user-select:none}
.fam.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.hint{font-size:11px;color:var(--sub);margin-top:6px;line-height:1.5}
.sum{font-size:12px;font-weight:600;margin-top:7px}
.sum.bad{color:var(--red)}
.preview{background:var(--blue-l);border-radius:7px;padding:8px 10px;font-size:11.5px}
.preview ol{margin:4px 0 0 17px;padding:0}
.bar{display:flex;gap:14px;align-items:center;background:#fff;border:1px solid var(--line);
border-radius:9px;padding:10px 14px;margin-bottom:12px;font-size:12.5px}
.bar b{font-size:16px;color:var(--blue)}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:12px 14px;margin-bottom:9px;display:flex;gap:12px;align-items:flex-start}
.card.done{opacity:.45}
.card.cur{border-color:var(--blue);box-shadow:0 0 0 2px var(--blue-l)}
.rank{flex:0 0 30px;font-size:17px;font-weight:700;color:var(--sub);text-align:center}
.body{flex:1;min-width:0}
.ttl{font-size:14.5px;font-weight:600;cursor:pointer}
.ttl:hover{color:var(--blue);text-decoration:underline}
.meta{font-size:11.5px;color:var(--sub);margin-top:2px}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.tag{font-size:10.5px;padding:1px 7px;border-radius:10px;border:1px solid var(--line)}
.tag.s1{background:var(--green-l);color:var(--green);border-color:#c9ecdc}
.tag.s2{background:var(--amber-l);color:var(--amber);border-color:#f3dfb0}
.tag.s3{background:var(--blue-l);color:var(--blue);border-color:#d6e4ff}
.tag.s4{background:var(--red-l);color:var(--red);border-color:#f6d2ce}
.notice{margin-top:8px;background:var(--red-l);border-left:3px solid var(--red);
padding:7px 10px;font-size:12.5px;font-weight:650;color:var(--red);border-radius:0 5px 5px 0}
.score{flex:0 0 74px;text-align:right}
.score b{font-size:21px;font-variant-numeric:tabular-nums}
.score small{display:block;color:var(--sub);font-size:10.5px}
.btns{display:flex;gap:5px;margin-top:8px;flex-wrap:wrap}
button{font-size:11.5px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;
background:#fff;color:var(--txt);cursor:pointer}
button:hover{border-color:var(--blue);color:var(--blue)}
button.p{background:var(--blue);color:#fff;border-color:var(--blue)}
button.p:hover{color:#fff;opacity:.88}
.mask{position:fixed;inset:0;background:rgba(20,28,40,.45);display:none;z-index:20}
.panel{position:fixed;top:0;right:0;bottom:0;width:min(560px,94vw);background:#fff;
z-index:21;overflow:auto;padding:20px 24px;display:none;box-shadow:-8px 0 28px rgba(0,0,0,.12)}
.panel h2{margin:0 0 4px;font-size:19px}
.panel h4{margin:18px 0 7px;font-size:13px;color:var(--sub);
border-bottom:1px solid var(--line);padding-bottom:5px}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:600}
.q{background:var(--amber-l);border-radius:6px;padding:8px 10px;margin:5px 0;font-size:12.5px}
.close{float:right}
.foot{font-size:11px;color:var(--sub);margin-top:16px;border-top:1px solid var(--line);padding-top:9px}
</style>
</head>
<body>
<header>
  <h1>双选会助手 · Demo v2 会前规划队列</h1>
  <div class="sub">四分支硬约束 · 分层给分 · 有效权重可见 · 现场事件驱动队列　|　单文件离线，演示时不调用任何模型</div>
  <div class="chips" id="chips"></div>
</header>
<div class="wrap">
<aside>
  <div class="sec">
    <h3>1 意向岗位族（硬约束）</h3>
    <div class="fams" id="fams"></div>
    <div class="hint">可多选。「不限」岗位不会被硬删；无 JD 企业按行业映射推断，照常一起排序。</div>
  </div>
  <div class="sec">
    <h3>2 六项权重（合计 100）</h3>
    <div id="weights"></div>
    <div class="sum" id="wsum"></div>
    <div class="hint">双休实测 0/277 不可采：不计入排序，自动转现场提问卡。其余维度「未采到」时按剩余权重重新归一，绝不给最低档。</div>
  </div>
  <div class="sec">
    <h3>3 城市模板（代替逐档填分）</h3>
    <select id="tpl"></select>
    <div class="row" style="margin-top:8px"><label>城市条件强度</label>
      <input type="range" id="intensity" min="0" max="100" value="100"><span class="v" id="intv">100</span></div>
    <div class="hint">展开高级模式可逐档调整；特质（交通枢纽 / 生态宜居）作为叠加小加分，不与能级互斥。</div>
    <div id="tierEdit" style="display:none;margin-top:7px"></div>
    <button onclick="document.getElementById('tierEdit').style.display=
      document.getElementById('tierEdit').style.display==='none'?'block':'none'">高级：逐档分值</button>
  </div>
  <div class="sec">
    <h3>4 家乡 / 行业偏好</h3>
    <div class="row"><label>家乡城市</label><select id="home"></select></div>
    <div class="row"><label>偏好行业</label><select id="ind"></select></div>
  </div>
  <div class="sec">
    <h3>5 现场状态</h3>
    <div class="row"><label>剩余分钟</label><input type="number" id="mins" value="210"></div>
    <div class="hint" id="live"></div>
  </div>
  <div class="sec">
    <h3>实时预览 · 前 5 家</h3>
    <div class="preview"><ol id="preview"></ol></div>
  </div>
</aside>
<main>
  <div class="bar">
    <span>队列 <b id="qcount">0</b> 家</span>
    <span>排除 <b id="xcount">0</b> 家</span>
    <span>当前指针 <b id="ptr">1</b></span>
    <span>剩余 <b id="left">210</b> 分钟</span>
    <span style="margin-left:auto"><button onclick="showExcluded()">查看排除清单</button></span>
  </div>
  <div id="queue"></div>
</main>
</div>
<div class="mask" id="mask" onclick="closePanel()"></div>
<div class="panel" id="panel"></div>

<script>
const DATA = __DATA__;
const CFG = DATA.config, COMPANIES = DATA.companies, SC = CFG.selfCheck;
const LB = new Set(CFG.labelOnly);
const state = {
  intended: new Set(['TECH']),
  weights: Object.fromEntries(CFG.dimensions.map(d=>[d.id,d.default])),
  template: '均衡', intensity: 1, home: '', industry: '',
  done: new Set(), rejected: new Set(), upgraded: new Set(),
  ptr: 0, mins: 210, queue: [], excluded: []
};

/* ---------- 自检条 ---------- */
document.getElementById('chips').innerHTML = [
  `词表 ${CFG.version} · ${SC.distinct_titles} 个岗位名 100% 归族`,
  `城市可用 ${SC.city_available}/118`,
  `企业类型可用 ${SC.company_type_available}/118`,
  `单族占比 ${(SC.top_family_share*100).toFixed(0)}%`,
  SC.label_only_dimensions.length ? `降级为标签：${SC.label_only_dimensions.join('、')}` : '无维度降级',
  `校验问题 ${Object.values(SC.validation_issues).reduce((a,b)=>a+b,0)} 项`,
].map((t,i)=>`<span class="chip ${i===4?'warn':''}">${t}</span>`).join('');

/* ---------- 控件 ---------- */
document.getElementById('fams').innerHTML = CFG.familyOrder.map(f=>
  `<span class="fam ${state.intended.has(f)?'on':''}" data-f="${f}">${CFG.families[f]}</span>`).join('');
document.getElementById('fams').onclick = e=>{
  const f=e.target.dataset.f; if(!f)return;
  state.intended.has(f)?state.intended.delete(f):state.intended.add(f);
  if(state.intended.size===0)state.intended.add('TECH');
  e.target.classList.toggle('on'); refresh();
};
document.getElementById('weights').innerHTML = CFG.dimensions.map(d=>
  `<div class="row"><label title="${d.name}">${d.name.length>9?d.name.slice(0,9)+'…':d.name}</label>
   <input type="range" min="0" max="60" value="${d.default}" data-w="${d.id}"
    ${d.kind==='unavailable'?'disabled style="opacity:.45"':''}>
   <span class="v" id="v_${d.id}">${d.default}</span></div>`).join('');
document.getElementById('weights').oninput = e=>{
  const k=e.target.dataset.w; if(!k)return;
  state.weights[k]=+e.target.value; document.getElementById('v_'+k).textContent=e.target.value;
  refresh();
};
document.getElementById('tpl').innerHTML = Object.keys(CFG.cityTemplates)
  .map(t=>`<option ${t==='均衡'?'selected':''}>${t}</option>`).join('');
document.getElementById('tpl').onchange = e=>{state.template=e.target.value;refresh();};
document.getElementById('intensity').oninput = e=>{
  state.intensity=+e.target.value/100; document.getElementById('intv').textContent=e.target.value; refresh();};
document.getElementById('home').innerHTML = '<option value="">（不选）</option>'+
  CFG.cities.map(c=>`<option ${c==='南京'?'selected':''}>${c}</option>`).join('');
document.getElementById('home').onchange = e=>{state.home=e.target.value;refresh();};
document.getElementById('ind').innerHTML = '<option value="">（不限行业）</option>'+
  CFG.industries.map(c=>`<option>${c}</option>`).join('');
document.getElementById('ind').onchange = e=>{state.industry=e.target.value;refresh();};
document.getElementById('mins').oninput = e=>{state.mins=+e.target.value||0;renderBar();};

function tierEditor(){
  const t=CFG.cityTemplates[state.template];
  document.getElementById('tierEdit').innerHTML=Object.entries(t).map(([k,v])=>
    `<div class="row"><label>${k}</label><input type="range" min="0" max="100" value="${v*100}"
      data-t="${k}"><span class="v">${v.toFixed(1)}</span></div>`).join('')+
    `<div class="hint">改这里即自定义档位分值；间距由你的偏好决定，不是客观测量值。</div>`;
  document.getElementById('tierEdit').oninput=e=>{
    const k=e.target.dataset.t;if(!k)return;
    CFG.cityTemplates[state.template][k]=+e.target.value/100;
    e.target.nextElementSibling.textContent=(+e.target.value/100).toFixed(1);refresh();};
}
tierEditor();
document.getElementById('tpl').addEventListener('change',tierEditor);

/* ---------- 四分支 ---------- */
function classify(rec){
  if(rec.jobs.length){
    const fams=[...new Set(rec.jobs.map(j=>j.family))];
    const hit=fams.some(f=>state.intended.has(f));
    const hasAny=fams.includes('ANY');
    if(hit||(hasAny&&state.intended.size))
      return {state:'confirmed_hit',families:fams,inferred:null,reason:null};
    return {state:'confirmed_miss',families:fams,inferred:null,
            reason:'有 JD，岗位族全不命中意向：'+fams.join('/')};
  }
  const rule=CFG.industryMap[rec.industry];
  if(rule){
    const cand=[rule.primary].concat(rule.secondary?[rule.secondary]:[]);
    if(cand.some(f=>state.intended.has(f)))
      return {state:'inferred_hit',families:cand,
        inferred:{is_inferred:true,family:rule.primary,
          basis:`网上未查到岗位信息；依据行业「${rec.industry}」的受控映射推断`,
          confidence:rule.confidence,question:rule.question},reason:null};
    return {state:'inferred_miss',families:cand,inferred:null,
            reason:`行业「${rec.industry}」映射的岗位族不含意向`};
  }
  return {state:'inferred_miss',families:[],inferred:null,
          reason:'无 JD 且 industry 未知，无法推断岗位'};
}

/* ---------- 维度取值（未知 -> null，绝不给最低档） ---------- */
function dimValues(rec){
  const d=rec.dims,vals={};
  if(d.city.status==='hit'){
    const base=CFG.cityTemplates[state.template][d.city.tier_key] ?? d.city.value;
    let v=base*state.intensity;
    v=Math.min(1,v+d.city.traits.length*0.05);
    vals.city={v,basis:d.city.basis+(d.city.traits.length?' + '+d.city.traits.join('/'):''),conf:.8};
  }
  if(d.home.status==='hit'&&state.home){
    vals.home={v:homeValue(d.home.city,state.home),basis:d.home.basis,conf:.7};
  }
  if(d.company_type.status==='hit')
    vals.company_type={v:d.company_type.value,basis:d.company_type.basis,conf:.9};
  if(d.industry.status==='hit')
    vals.industry={v:(!state.industry||d.industry.category===state.industry)?1:0,
                   basis:d.industry.basis,conf:.9};
  if(d.scale.status==='hit')
    vals.scale={v:d.scale.value,basis:d.scale.basis,conf:d.scale.confidence};
  return vals;
}
function homeValue(city,home){
  if(!city||!home)return 0;
  if(city===home)return CFG.homeTier['同城'];
  const p=CFG.cityProvince[city],hp=CFG.cityProvince[home];
  if(p&&hp&&p===hp)return CFG.homeTier['同省'];
  return CFG.homeTier['其他'];
}

/* ---------- 计分：未知不计入分母 + 有效权重可见 ---------- */
function score(rec,vals,dynLB){
  const lb=new Set([...LB,...dynLB]);
  const active=Object.fromEntries(Object.entries(vals).filter(([k])=>!lb.has(k)));
  const keys=Object.keys(active);
  const pool=keys.reduce((s,k)=>s+(state.weights[k]||0),0);
  const total=Object.values(state.weights).reduce((a,b)=>a+b,0)||1;
  if(pool<=0)return {score:0,eff:0,parts:{},pool:0};
  const parts={};
  let s=0;
  for(const k of keys){
    const w=state.weights[k]||0,c=w/pool*active[k].v*100;
    s+=c;parts[k]={v:active[k].v,w,c,basis:active[k].basis,conf:active[k].conf};
  }
  // 岗位相关度不占权重，也不加进分数（否则分数会 >100 不好解释）；
  // 只作为同分时的次序微调，保证排序分严格落在 0–100。
  const rel=rec.jobs.length?rec.jobs.filter(j=>state.intended.has(j.family)).length/rec.jobs.length:0;
  return {score:+s.toFixed(2),eff:+(pool/total*100).toFixed(1),parts,pool,rel:+rel.toFixed(3)};
}

/* ---------- 现场提问：权重 x (1-证据可信度) 自动取前 1-2 问 ---------- */
function questions(rec,vals){
  const qs=[];
  if(rec.inferred)qs.push({q:'【显著提示】'+rec.inferred.basis+
    '。'+rec.inferred.question+'（推断置信度 '+rec.inferred.confidence+'）',
    p:999,infer:true});
  for(const d of CFG.dimensions){
    if(vals[d.id])continue;
    const st=rec.dims[d.id].status;
    if(st==='unknown'||st==='unavailable'){
      let q=CFG.questionBank[d.id]||('请问'+d.name+'的情况？');
      q=q.replace('{home}',state.home||'家乡');
      qs.push({q, p:(state.weights[d.id]||0)*(st==='unavailable'?1:0.9), infer:false});
    }
  }
  return qs.sort((a,b)=>b.p-a.p).slice(0,2);
}

/* ---------- 主流程 ---------- */
function compute(){
  const q=[],x=[],raw=[];
  for(const rec of COMPANIES){
    if(state.rejected.has(rec.id)){x.push({name:rec.name,reason:'现场确认不招，已移除（顺延）'});continue;}
    const c=classify(rec);
    if(c.state==='confirmed_miss'||c.state==='inferred_miss'){
      x.push({name:rec.name,reason:c.reason});continue;
    }
    raw.push({rec,c,vals:dimValues(rec)});
  }
  /* 动态区分度自检（4.8 五、）：常数偏移不改变次序 -> 降级为标签。
     实测修正：最低档占比高 ≠ 无区分度（「离家近」在家乡外地时天然稀疏，
     却是学生最个性化的偏好），不能删；只标为「稀疏命中」并提示本次命中家数。 */
  const dynLB=new Set(), sparse=new Set();
  for(const k of ['city','home','company_type','industry','scale']){
    const vs=raw.filter(o=>o.vals[k]).map(o=>o.vals[k].v);
    if(vs.length>10){
      const mn=Math.min(...vs),mx=Math.max(...vs);
      const low=vs.filter(v=>Math.abs(v-mn)<1e-9).length/vs.length;
      if((mx-mn)<0.05)dynLB.add(k);                 // 真·常数偏移
      else if(low>0.8)sparse.add(k);                  // 稀疏但有效，保留权重
    }
  }
  state.dynLB=[...dynLB];state.sparse=[...sparse];
  for(const o of raw){
    const sc=score(o.rec,o.vals,dynLB);
    q.push({...o.rec,...o.c,vals:o.vals,sc,qs:questions(o.rec,o.vals),
      evidence:o.c.state==='confirmed_hit'?1:0});
  }
  q.sort((a,b)=>b.sc.score-a.sc.score||b.sc.rel-a.sc.rel||b.evidence-a.evidence||
    String(a.booth).localeCompare(String(b.booth)));
  state.queue=q;state.excluded=x;
  const ch=document.getElementById('chips');
  let n; while((n=ch.querySelector('.chip.dyn'))) n.remove();
  if(state.dynLB.length)ch.insertAdjacentHTML('beforeend',
    `<span class="chip warn dyn">常数偏移已降级：${state.dynLB.join('、')}</span>`);
  if(state.sparse.length)ch.insertAdjacentHTML('beforeend',
    `<span class="chip dyn">稀疏命中但保留权重：${state.sparse.join('、')}</span>`);
}
function stay(r){
  if(r.state==='inferred_hit')return ['问一句就走','s2'];
  if(r.sc.score>=55)return ['值得聊','s1'];
  return ['顺路停','s3'];
}
function refresh(){compute();renderBar();renderQueue();renderPreview();renderWsum();}
function renderWsum(){
  const t=Object.values(state.weights).reduce((a,b)=>a+b,0);
  const el=document.getElementById('wsum');
  el.textContent='合计 '+t+' 分'+(t!==100?'（非 100 时按比例归一，不影响排序）':'');
  el.className='sum'+(t!==100?' bad':'');
}
function renderBar(){
  document.getElementById('qcount').textContent=state.queue.length;
  document.getElementById('xcount').textContent=state.excluded.length;
  document.getElementById('ptr').textContent=Math.min(state.ptr+1,state.queue.length||1);
  document.getElementById('left').textContent=state.mins;
  const rest=state.queue.length-state.ptr;
  document.getElementById('live').textContent=
    `还剩 ${state.mins} 分钟，后面还有 ${rest} 家；不预设逛几家、不按平均时间分配，按实际消耗推进。`;
}
function renderPreview(){
  document.getElementById('preview').innerHTML=state.queue.slice(0,5).map(r=>
    `<li>${r.name.slice(0,16)} <span style="color:#6b7684">${r.sc.score} 分</span></li>`).join('')||'<li>当前无候选</li>';
}
function renderQueue(){
  document.getElementById('queue').innerHTML=state.queue.map((r,i)=>{
    const [st,cls]=stay(r);
    const tags=`<span class="tag ${cls}">${st}</span>
      <span class="tag ${r.state==='inferred_hit'?'s4':'s1'}">${r.state==='inferred_hit'?'推断待核实':'明确命中'}</span>
      <span class="tag">生效权重 ${r.sc.eff}%</span>
      <span class="tag">${r.dims.city.city||'城市未知'} · ${r.booth||'无展位号'}</span>`;
    const notice=(r.inferred&&CFG.scoring.significant_infer_notice)?
      `<div class="notice">⚠ 网上没有岗位信息，根据「${r.industry}」行业我们推断他们有
       ${CFG.families[r.inferred.family]}类岗位，您可以去现场问一句：${r.inferred.question}</div>`:'';
    const q=r.qs.length?`<div class="meta">建议核实：${r.qs[0].q.slice(0,52)}…</div>`:'';
    const btns=`<div class="btns">
      <button onclick="report(${i})">查看报告</button>
      ${r.state==='inferred_hit'?`<button class="p" onclick="act(${i},'yes')">招</button>
        <button onclick="act(${i},'no')">不招 → 顺延</button>`:''}
      <button onclick="act(${i},'done')">聊完了</button>
      <button onclick="act(${i},'skip')">跳过</button></div>`;
    return `<div class="card ${state.done.has(r.id)?'done':''} ${i===state.ptr?'cur':''}">
      <div class="rank">${i+1}</div>
      <div class="body"><div class="ttl" onclick="report(${i})">${r.name}</div>
        <div class="meta">${r.industry} · ${r.nature} · ${r.jobs.length} 条岗位记录</div>
        <div class="tags">${tags}</div>${notice}${q}${btns}</div>
      <div class="score"><b>${r.sc.score}</b><small>排序分</small>
        <small>信息完整度 ${infoComp(r)}%</small></div></div>`;
  }).join('')||'<div class="card">当前意向下无候选企业，请调整意向岗位族。</div>';
}
function infoComp(r){
  const ks=['city','company_type','industry','scale'];
  return Math.round(ks.filter(k=>r.dims[k].status==='hit').length/ks.length*100);
}
function act(i,what){
  const r=state.queue[i];if(!r)return;
  if(what==='no')state.rejected.add(r.id);
  if(what==='yes'){state.upgraded.add(r.id);}
  if(what==='done'||what==='skip')state.done.add(r.id);
  state.mins=Math.max(0,state.mins-(what==='done'?8:what==='no'?1:2));
  document.getElementById('mins').value=state.mins;
  state.ptr=Math.min(state.ptr+1,Math.max(0,state.queue.length-1));
  refresh();
}
function report(i){
  const r=state.queue[i];if(!r)return;
  const parts=Object.entries(r.sc.parts);
  const hitRows=parts.map(([k,p])=>{
    const n=CFG.dimensions.find(d=>d.id===k)?.name||k;
    return `<tr><td>${n}</td><td>${p.v.toFixed(2)}</td><td>${p.w} 分</td>
      <td>+${p.c.toFixed(1)}</td><td>${p.basis}</td></tr>`;}).join('');
  const missRows=CFG.dimensions.filter(d=>!r.sc.parts[d.id]).map(d=>{
    const st=r.dims[d.id].status;
    const label=st==='unavailable'?'全库不可采（转现场提问）':st==='unknown'?'未采到（不计入分母）':'已降级为标签';
    return `<tr><td>${d.name}</td><td colspan="4">${label} · ${r.dims[d.id].basis}</td></tr>`;}).join('');
  const jobRows=r.jobs.length?r.jobs.map(j=>
    `<tr><td>${j.title}</td><td>${CFG.families[j.family]||j.family}</td><td>${j.form}</td></tr>`).join(''):
    `<tr><td colspan="3">网上未查到岗位记录（推断见下方显著提示）</td></tr>`;
  const qs=r.qs.map(q=>`<div class="q">${q.q}</div>`).join('')||'<div class="q">无需现场核实</div>';
  const gaps=CFG.dimensions.filter(d=>r.dims[d.id].status!=='hit')
    .map(d=>d.name+'：'+r.dims[d.id].basis).join('；')||'无';
  document.getElementById('panel').innerHTML=`
    <button class="close" onclick="closePanel()">关闭 ✕</button>
    <h2>${r.name}</h2>
    <div class="meta">展位 ${r.booth||'未知'} · ${r.industry} · ${r.nature} ·
      ${r.state==='inferred_hit'?'推断待核实':'明确命中'} · 排序分 ${r.sc.score} ·
      生效权重 ${r.sc.eff}%</div>
    ${r.inferred?`<div class="notice" style="margin-top:12px">⚠ ${r.inferred.basis}。
      置信度 ${r.inferred.confidence}。${r.inferred.question}</div>`:''}
    <h4>命中了什么 / 算分依据</h4>
    <table><tr><th>维度</th><th>档位值</th><th>你的权重</th><th>贡献</th><th>依据</th></tr>
      ${hitRows||'<tr><td colspan="5">无可计算维度</td></tr>'}</table>
    <div class="hint">档位分值由你的偏好与城市模板生成，不是客观测量值；行业地位以「规模/体制代理分」名义参与，已列依据。</div>
    <div class="hint" style="color:#b7791f">本次仅 ${r.sc.eff}% 的权重生效：未采到 / 不可采的维度不计入分母，
      分数按剩余权重重新归一，因此不代表企业整体更好，也不与生效权重低的企业直接可比。</div>
    <h4>没命中 / 没查到</h4>
    <table><tr><th>维度</th><th colspan="4">状态与原因</th></tr>${missRows||'<tr><td colspan="5">无</td></tr>'}</table>
    <h4>岗位记录</h4><table><tr><th>岗位名</th><th>岗位族</th><th>form</th></tr>${jobRows}</table>
    <h4>信息缺口</h4><div class="hint">${gaps}</div>
    <h4>建议现场提问（按「你的权重 × 证据可信度」自动取前 1–2 问）</h4>${qs}
    <h4>顺延出口</h4>
    <div class="btns"><button class="p" onclick="act(${i},'no');closePanel()">现场确认无此岗位 → 顺延至下一家</button>
      <button onclick="act(${i},'done');closePanel()">聊完了，指针前移</button></div>
    <div class="foot">排序只是会前时间分配建议，不代表企业好坏或岗位承诺（README 4.2）。</div>`;
  document.getElementById('mask').style.display='block';
  document.getElementById('panel').style.display='block';
}
function closePanel(){
  document.getElementById('mask').style.display='none';
  document.getElementById('panel').style.display='none';
}
function showExcluded(){
  document.getElementById('panel').innerHTML=`
    <button class="close" onclick="closePanel()">关闭 ✕</button>
    <h2>排除清单（118 → ${state.queue.length}）</h2>
    <div class="hint">每条带原因，防止推断错误静默误删；② 有数据没岗位 / ④ 推断也没岗位。</div>
    <table><tr><th>企业</th><th>排除原因</th></tr>
    ${state.excluded.map(x=>`<tr><td>${x.name}</td><td>${x.reason}</td></tr>`).join('')}</table>`;
  document.getElementById('mask').style.display='block';
  document.getElementById('panel').style.display='block';
}
state.home=document.getElementById('home').value;
refresh();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
