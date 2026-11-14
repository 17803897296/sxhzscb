# -*- coding: utf-8 -*-
"""把 data/test_users/u*.json 打成 demo/test_users.html（一键切换测试画像）。

页面只做三件事：
  1 展示三套随机画像（专业 / 意向岗位 / 排序偏好 / 七维权重）；
  2 点「用这套画像打开排行」→ 写 localStorage.fair_profile → 跳 /fair.html；
  3 点「清空」→ 删 localStorage → 回到服务端当前画像（李炳成）。

画像 JSON 原样内联，不联网；复制按钮能直接拿到完整文档去 Post /api/fair/profile。
"""
import glob
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "demo", "test_users.html")

DIM_LABEL = {
    "city": "城市条件", "home": "离家近", "company_type": "企业类型",
    "industry": "行业", "scale": "企业规模档",
    "industry_status": "行业地位", "growth": "成长性",
}


def chip(s):
    return '<span class="chip">%s</span>' % html.escape(str(s))


def card(i, doc):
    a = doc["answers"]
    ident = doc["identity"]
    w = [(DIM_LABEL.get(k, k), v) for k, v in a["tri_weights"].items() if v]
    w.sort(key=lambda x: -x[1])
    wsum = sum(v for _, v in w) or 1
    bars = "".join(
        '<div class="bar"><span class="bl">%s</span>'
        '<span class="bt"><i style="width:%.1f%%"></i></span>'
        '<span class="bv">%d</span></div>' % (html.escape(k), v / wsum * 100, v)
        for k, v in w)
    top = lambda m: min(m, key=m.get) if m else "—"
    js = json.dumps(doc, ensure_ascii=False)
    return """
  <section class="card" data-uid="%(fid)s">
    <header>
      <div class="who"><b>%(name)s</b><span class="tag">%(fid)s</span></div>
      <div class="meta">%(major)s · 老家 %(home)s · 期望入职 2027 年 7 月</div>
    </header>
    <div class="row"><span class="k">意向岗位</span><div class="v">%(jobs)s</div></div>
    <div class="row"><span class="k">偏好排序</span><div class="v">
      城市 1=%(c1)s · 企业类型 1=%(t1)s · 行业 1=%(i1)s
      <span class="dim">（只列第 1 优先，其余按默认顺序）</span></div></div>
    <div class="row"><span class="k">七维权重</span><div class="v bars">%(bars)s</div></div>
    <div class="acts">
      <button class="primary" onclick="useUser(%(idx)d)">用这套画像打开排行 →</button>
      <button onclick="copyJson(%(idx)d, this)">复制 JSON</button>
      <button class="ghost" onclick="toggleJson(%(idx)d)">看原始数据</button>
      <button class="warn" onclick="realRun(%(idx)d)">提交入库，跑完整链路</button>
    </div>
    <div class="st" id="real-%(idx)d"></div>
    <pre class="raw" id="raw-%(idx)d">%(raw)s</pre>
  </section>""" % {
        "fid": html.escape(doc["fair_id"]),
        "name": html.escape(ident.get("name", "")),
        "major": html.escape(ident.get("major", "")),
        "home": html.escape(doc["fair"].get("home_city", "")),
        "jobs": "".join(chip(j) for j in a["intended_jobs"]),
        "c1": top(a["city_rank"]), "t1": top(a["company_type_rank"]),
        "i1": top(a["industry_rank"]),
        "bars": bars,
        "idx": i,
        "raw": html.escape(json.dumps(doc, ensure_ascii=False, indent=2)),
    }


def main():
    files = sorted(glob.glob(os.path.join(HERE, "data", "test_users", "u*.json")))
    docs = [json.load(open(p, encoding="utf-8")) for p in files]
    assert docs, "没有 data/test_users/u*.json，先跑 gen_test_users.py"
    cards = "".join(card(i, d) for i, d in enumerate(docs))
    docs_js = json.dumps(docs, ensure_ascii=False)
    page = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>测试用户 · 3 套随机画像</title>
<style>
:root{--ink:#16181d;--muted:#6b7280;--line:#e6e8ec;--bg:#f5f6f8;--card:#fff;
      --blue:#2f5bd3;--green:#0f7b57}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
 font-size:14px;line-height:1.65;-webkit-font-smoothing:antialiased}
.app{max-width:940px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 22px;font-size:13.5px}
.note{background:#fff8e6;border:1px solid #f0d9a8;color:#7a5a12;border-radius:10px;
 padding:11px 14px;font-size:13px;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
 padding:18px 20px;margin:0 0 16px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.card header{margin-bottom:12px}
.who{display:flex;align-items:center;gap:10px;font-size:16px}
.tag{font:11px/1 ui-monospace,Menlo,monospace;color:var(--muted);
 background:#f1f3f6;border-radius:6px;padding:4px 7px}
.meta{color:var(--muted);font-size:13px;margin-top:3px}
.row{display:flex;gap:12px;padding:7px 0;border-top:1px dashed var(--line)}
.k{flex:0 0 74px;color:var(--muted);font-size:13px}
.v{flex:1;min-width:0}
.chip{display:inline-block;background:#eef3ff;color:#26418f;border-radius:999px;
 padding:2px 9px;margin:0 6px 6px 0;font-size:12.5px}
.dim{color:var(--muted);font-size:12px}
.bars{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px}
.bar{display:flex;align-items:center;gap:8px;font-size:12.5px}
.bl{flex:0 0 68px;color:#4b5563}
.bt{flex:1;height:6px;background:#eef0f4;border-radius:3px;overflow:hidden}
.bt i{display:block;height:100%;background:#8aa4e8;border-radius:3px}
.bv{flex:0 0 26px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
button{font:inherit;font-size:13px;border:1px solid var(--line);background:#fff;
 color:var(--ink);border-radius:8px;padding:7px 13px;cursor:pointer}
button:hover{border-color:#c9cfda}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
button.primary:hover{background:#284fbe}
button.ghost{color:var(--muted)}
.raw{display:none;margin:12px 0 0;background:#0f1115;color:#d7dce5;border-radius:10px;
 padding:14px;font:12px/1.55 ui-monospace,Menlo,monospace;max-height:320px;overflow:auto}
.raw.on{display:block}
.foot{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
button.warn{border-color:#f0d9a8;background:#fffaf0;color:#8a6d3b}
button.warn:hover{background:#fff4dd}
.st{font-size:12.5px;color:#8a6d3b;margin-top:8px;min-height:0;line-height:1.6}
.st:empty{display:none}
.hint{color:var(--muted);font-size:12.5px;margin-top:18px;line-height:1.8}
code{background:#eceff4;border-radius:4px;padding:1px 5px;font-size:12px}
</style></head><body><div class="app">
<h1>测试用户 · 3 套随机画像</h1>
<p class="sub">由 <code>ent_tools/gen_test_users.py</code> 生成，专业 / 老家 / 意向岗位 / 排序偏好 / 权重各不相同，用来验收「换一个学生会怎样」。</p>
<div class="note">点击「用这套画像打开排行」会把画像写进本浏览器 localStorage 再跳转 <code>/fair.html</code>；测试完点底部「清空画像」即可回到服务端当前画像（李炳成）。画像只存在本地，不写库。</div>
__CARDS__
<div class="foot">
  <button onclick="clearUser()">清空本地画像，回到服务端画像</button>
  <button class="warn" onclick="restoreActive()">恢复提交前的库内画像</button>
  <button onclick="location.href='/fair.html'">直接打开排行页（用现有画像）</button>
</div>
<p class="hint">
原文件：<code>ent_tools/data/test_users/u1.json ~ u3.json</code>（完整 fair_profile 文档）<br>
也可以 <code>POST /api/fair/profile</code>，body 为 <code>{"document": &lt;那三份 JSON 之一&gt;}</code>，服务端会落库并重建 payload。<br>
换一批随机：<code>python -X utf8 gen_test_users.py --seed 7</code> 然后 <code>python -X utf8 build_test_users_page.py</code>。
</p>
</div>
<script>
const USERS = __DOCS__;
function useUser(i){
  /* 画像用地址栏带走（base64），同时写一份 localStorage 备用。
     以前是 location.href='/fair.html'：绝对路径 + 只靠 localStorage，
     服务一停就白屏 / 退回采集问卷。相对路径 + URL 传参后，
     双击本地 html 也能直接看排行，不需要 8102。 */
  const enc = btoa(unescape(encodeURIComponent(JSON.stringify(USERS[i]))));
  try{ localStorage.setItem('fair_profile', JSON.stringify(USERS[i])); }catch(e){}
  location.href = 'fair.html?report=1&p=' + encodeURIComponent(enc);
}
function copyJson(i, btn){
  const t = JSON.stringify(USERS[i], null, 2);
  const done = ()=>{ const o=btn.textContent; btn.textContent='已复制'; setTimeout(()=>btn.textContent=o,1200); };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(done).catch(()=>fallback());
  } else fallback();
  function fallback(){
    const ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta);
    ta.select(); try{ document.execCommand('copy'); done(); }catch(e){}
    document.body.removeChild(ta);
  }
}
function toggleJson(i){
  const el=document.getElementById('raw-'+i); el.classList.toggle('on');
}
function clearUser(){
  try{ localStorage.removeItem('fair_profile'); }catch(e){}
  location.href = '/fair.html';
}

/* ---- 完整链路：提交入库 → 服务端按新画像重算命中（调大模型）→ 跳报告 ----
   会替换库内「生效画像」（生产演示用的那份），提交前把原画像备份到本浏览器，
   点底部「恢复提交前的库内画像」可以提交回去。约 1~3 分钟，会消耗模型调用。 */
async function realRun(i){
  if(!confirm('会用这套画像替换库内生效画像，并调用大模型重算全部企业的岗位命中（约 1–3 分钟）。提交前会自动把原画像备份到本浏览器，可一键恢复。继续？')) return;
  const box=document.getElementById('real-'+i);
  box.textContent='读取当前生效画像，做备份…';
  try{
    const a=await fetch('/api/fair/active-profile').then(x=>x.json());
    if(a && a.document) localStorage.setItem('fair_prev_active_profile', JSON.stringify(a.document));
  }catch(e){}
  box.textContent='提交画像…';
  let j={};
  try{
    j=await fetch('/api/fair/profile',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({document:USERS[i]})
    }).then(x=>x.json());
  }catch(e){ box.textContent='提交失败：'+e; return; }
  box.textContent='已提交（'+(j.fair_id||USERS[i].fair_id)+'），后台重算中…';
  const t0=Date.now();
  const timer=setInterval(async function(){
    try{
      const p=await fetch('/api/fair/payload').then(x=>x.json());
      if(p && p.ok!==false && !p.computing){
        clearInterval(timer);
        try{ localStorage.setItem('fair_profile', JSON.stringify(USERS[i])); }catch(e){}
        box.textContent='重算完成，正在打开报告…';
        location.href='fair.html?report=1&p='+encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(USERS[i])))));
        return;
      }
      box.textContent='后台重算中…已 '+(Math.round((Date.now()-t0)/1000))+' 秒'
        +(p && p.profile ? '（'+p.profile+'）' : '');
    }catch(e){}
    if(Date.now()-t0>300000){
      clearInterval(timer);
      box.textContent='超过 5 分钟还没完成：去看服务端日志（可能模型超时）。库里已换成测试画像。';
    }
  },4000);
}
async function restoreActive(){
  const raw=localStorage.getItem('fair_prev_active_profile');
  if(!raw){ alert('没有备份：这个浏览器还没提交过测试画像。'); return; }
  if(!confirm('把库内生效画像恢复成提交测试画像之前的那一份？同样要重算 1–3 分钟。')) return;
  try{
    await fetch('/api/fair/profile',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({document:JSON.parse(raw)})
    });
    alert('已提交恢复，后台重算中（1–3 分钟）。完成后刷新 /fair.html。');
  }catch(e){ alert('恢复失败：'+e); }
}
</script></body></html>
""".replace("__CARDS__", cards).replace("__DOCS__", docs_js)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print("写 %s（%d 套画像，%d 字符）" % (OUT, len(docs), len(page)))


if __name__ == "__main__":
    main()
