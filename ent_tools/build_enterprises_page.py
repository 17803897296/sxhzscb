"""生成企业端（企业名录 / 搜索）静态页：demo/enterprises.html

为什么要独立一端：匹配排行是「按你的条件筛过的 48 家」，学生看不到全貌，
也就没法判断「是这家没岗」还是「是我条件把它筛掉了」。企业端给的是**全量
145 家原始名录**，不做任何匹配过滤，只做检索。

数据全部内联（不 fetch），双击也能开。字段只留检索与判断用得上的：
  名称 / 城市 / 性质 / 行业 / 规模 / 主营 / 岗位名 / 岗位数 / 来源

用法：python -X utf8 build_enterprises_page.py
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "careersafari.db"
OUT = ROOT / "demo" / "enterprises.html"

# 岗位描述只留前 N 字：企业端用来判断「这岗像不像我的方向」，
# 不需要完整 JD（完整 JD 在匹配端企业详情里）。
REQ_KEEP = 180


def load():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    out = []
    for row in con.execute("SELECT name, item_json FROM company_jobs ORDER BY name"):
        try:
            it = json.loads(row["item_json"] or "{}")
        except Exception:
            continue
        jobs = []
        for j in it.get("jobs") or []:
            t = (j.get("title") or "").strip()
            if not t:
                continue
            jobs.append({
                "t": t,
                "r": (j.get("requirements") or "").strip()[:REQ_KEEP],
                "s": (j.get("salary") or "").strip(),
                "h": str(j.get("headcount") or "").strip(),
            })
        out.append({
            "n": it.get("name") or row["name"],
            "c": it.get("city") or "",
            "p": it.get("province") or "",
            "nat": it.get("nature") or "",
            "ind": it.get("industry") or "",
            "sc": it.get("scale_tier") or "",
            "biz": (it.get("biz_scope") or "").strip(),
            "js": it.get("jobSource") or "",
            "jobs": jobs,
        })
    con.close()
    return out


def main():
    rows = load()
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    html = TPL.replace("__DATA__", data).replace("__N__", str(len(rows)))
    OUT.write_text(html, encoding="utf-8")
    n_jobs = sum(len(r["jobs"]) for r in rows)
    print("企业端已生成：%s" % OUT)
    print("  %d 家 / %d 个岗位 / %.0f KB" % (len(rows), n_jobs, len(html.encode()) / 1024))


TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>企业端 · 全量名录</title>
<style>
:root{
  --ink:#16181d; --ink2:#3f4653; --muted:#727a89; --faint:#9aa1ad;
  --line:#e6e8ec; --bg:#f4f5f7; --card:#fff;
  --blue:#2f5bd3; --blue-soft:#eef2fd; --blue-line:#c9d6f8;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{
  background:var(--bg);color:var(--ink);font-size:14.5px;line-height:1.6;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px;margin:0 auto;padding:22px 18px 80px}

/* ---------- 页头 ---------- */
.head{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.head h1{font-size:21px;margin:0;letter-spacing:.2px}
.head .sub{color:var(--muted);font-size:13px;margin:5px 0 0}
.head .stat{margin-left:auto;display:flex;gap:22px;padding-top:4px}
.head .stat b{display:block;font-size:20px;line-height:1.2;font-variant-numeric:tabular-nums}
.head .stat span{font-size:11.5px;color:var(--faint)}
.head .stat .s{text-align:right}

/* ---------- 检索条 ---------- */
.bar{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.search{position:relative}
.search input{width:100%;font:inherit;font-size:14.5px;padding:11px 40px 11px 38px;
  border:1px solid var(--line);border-radius:11px;background:#fbfcfd;color:var(--ink);outline:none;transition:.15s}
.search input:focus{border-color:var(--blue-line);background:#fff;box-shadow:0 0 0 3px var(--blue-soft)}
.search .ic{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--faint);font-size:15px}
.search .clr{position:absolute;right:10px;top:50%;transform:translateY(-50%);border:0;background:none;
  color:var(--faint);font-size:16px;cursor:pointer;padding:2px 6px;display:none}
.search .clr.on{display:block}

.filters{margin-top:12px;display:flex;flex-direction:column;gap:9px}
.frow{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}
.flabel{flex:0 0 44px;font-size:12px;color:var(--faint);padding-top:5px}
.chips{display:flex;flex-wrap:wrap;gap:6px;flex:1;min-width:0}
.chip{appearance:none;border:1px solid var(--line);background:#fff;color:var(--ink2);
  font:inherit;font-size:12.5px;padding:4px 11px;border-radius:999px;cursor:pointer;transition:.13s;line-height:1.5}
.chip:hover{border-color:#c9cfda}
.chip.on{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600}
.chip .n{opacity:.6;margin-left:4px;font-size:11px}
.chip.on .n{opacity:.8}

/* ---------- 结果头 ---------- */
.resline{display:flex;align-items:center;gap:10px;margin:0 2px 10px;font-size:12.5px;color:var(--muted)}
.resline b{color:var(--ink);font-variant-numeric:tabular-nums}
.resline .sp{margin-left:auto}
.selbtn{border:1px solid var(--line);background:#fff;border-radius:8px;font:inherit;
  font-size:12px;color:var(--ink2);padding:5px 10px;cursor:pointer}
.selbtn:hover{border-color:#c9cfda}

/* ---------- 企业卡 ---------- */
.list{display:flex;flex-direction:column;gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:13px;
  overflow:hidden;transition:.15s;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.card:hover{border-color:#d6dbe4;box-shadow:0 3px 12px rgba(16,24,40,.07)}
.card.open{border-color:var(--blue-line);box-shadow:0 4px 18px rgba(47,91,211,.1)}
.chead{display:flex;gap:12px;padding:13px 15px;cursor:pointer;align-items:flex-start}
.idx{flex:none;width:26px;height:26px;border-radius:8px;background:#f1f3f6;color:var(--faint);
  display:grid;place-items:center;font-size:11.5px;font-weight:700;margin-top:1px;
  font-variant-numeric:tabular-nums}
.card.open .idx{background:var(--blue);color:#fff}
.cmain{flex:1;min-width:0}
.cname{font-size:15px;font-weight:600;letter-spacing:.1px;line-height:1.4}
.cmeta{display:flex;gap:7px;flex-wrap:wrap;margin-top:5px;align-items:center;font-size:11.5px;color:var(--muted)}
.cmeta .dot{color:#d3d8e0}
.pill{padding:1px 7px;border-radius:5px;background:#f1f3f6;color:#5b6472;font-size:11px}
.pill.ind{background:var(--blue-soft);color:#26418f}
.pill.none{background:#fff4e5;color:#9a6212}
.cbiz{margin-top:7px;font-size:12.5px;color:var(--faint);line-height:1.55;
  display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.card.open .cbiz{-webkit-line-clamp:6;color:var(--ink2)}
.jcount{flex:none;text-align:right;padding-top:2px;min-width:44px}
.jcount b{font-size:15px;font-variant-numeric:tabular-nums}
.jcount span{font-size:10.5px;color:var(--faint);display:block;line-height:1.3}
.card.open .jcount b{color:var(--blue)}

/* 展开区 */
.cbody{display:none;padding:0 15px 15px;border-top:1px solid #f0f2f5;margin-top:0}
.card.open .cbody{display:block}
.sect{margin-top:12px}
.sect-t{font-size:11px;font-weight:700;letter-spacing:.09em;color:var(--faint);text-transform:uppercase;margin-bottom:6px}
.biz{font-size:13px;color:var(--ink2);line-height:1.7;background:#fafbfc;border:1px solid #f0f2f5;
  border-radius:9px;padding:10px 12px}
.job{display:flex;gap:9px;padding:8px 0;border-bottom:1px dashed #eef0f4;align-items:flex-start}
.job:last-child{border-bottom:0}
.job .jt{flex:1;min-width:0}
.job .jn{font-size:13.5px;font-weight:550}
.job .jr{font-size:11.5px;color:var(--faint);margin-top:3px;line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.job .js{flex:none;font-size:11.5px;color:#0f7b57;background:#eaf7f1;border-radius:5px;
  padding:1px 6px;margin-top:2px;white-space:nowrap}
.nojob{font-size:12.5px;color:#9a6212;background:#fffaf0;border:1px solid #f3e2b8;
  border-radius:9px;padding:9px 11px}
.src{font-size:11px;color:var(--faint);margin-top:10px}

/* ---------- 空态 ---------- */
.empty{text-align:center;padding:54px 20px;color:var(--muted)}
.empty .big{font-size:34px;opacity:.25;margin-bottom:10px}
.empty p{margin:0 0 4px;font-size:14px}
.empty .hint{font-size:12.5px;color:var(--faint)}

mark{background:#fff2a8;color:inherit;padding:0 1px;border-radius:2px}
@media(max-width:640px){
  .wrap{padding:16px 12px 70px}
  .head .stat{width:100%;margin-left:0;gap:16px;border-top:1px solid var(--line);padding-top:10px}
  .head .stat .s{text-align:left}
  .flabel{flex-basis:100%;padding-top:0}
}
</style>
</head>
<body>
<div class="wrap">

  <div class="head">
    <div>
      <h1>企业端 · 全量名录</h1>
      <p class="sub">这里不做匹配筛选，<b>来的每一家都在</b>。先看清池子里有什么，再看排行把它排成什么样。</p>
    </div>
    <div class="stat">
      <div class="s"><b>__N__</b><span>参选企业</span></div>
      <div class="s"><b id="st-jobs">—</b><span>招聘岗位</span></div>
      <div class="s"><b id="st-cover">—</b><span>有岗位公告</span></div>
    </div>
  </div>

  <div class="bar">
    <div class="search">
      <span class="ic">⌕</span>
      <input id="q" type="search" placeholder="搜企业名 / 岗位名 / 主营关键词，例如「质检」「锂电」「会计」" autocomplete="off">
      <button class="clr" id="clr" type="button" aria-label="清空">×</button>
    </div>
    <div class="filters" id="filters"></div>
  </div>

  <div class="resline">
    <span>共 <b id="cnt">0</b> 家</span>
    <span class="sp" id="sorthint"></span>
    <button class="selbtn" id="expand" type="button">全部展开</button>
  </div>

  <div class="list" id="list"></div>
  <div class="empty" id="empty" style="display:none">
    <div class="big">⌕</div>
    <p>没有符合条件的单位</p>
    <p class="hint">换个关键词，或把上方的筛选条件清掉</p>
  </div>
</div>

<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* 被筛选的维度：数组，空数组代表不限 */
const F = { ind: [], nat: [], city: [], has: [] };
let Q = '';
let openSet = new Set();

/* ---------- 顶部统计 ---------- */
(function(){
  let jobs = 0, cover = 0;
  DATA.forEach(c => {
    jobs += c.jobs.length;
    if (c.jobs.length) cover++;
  });
  $('#st-jobs').textContent = jobs;
  $('#st-cover').textContent = cover + ' / ' + DATA.length;
})();

/* ---------- 筛选项：按当前数据统计出各维度取值与数量 ----------
   「未知」是有信息量的（说明这家数据没补上），但排在第一眼位置会让整排
   筛选看起来像残缺，所以沉到最后，并单独用弱色标出来。 */
const isUnknown = v => !v || v === '未知';

function countBy(key){
  const m = new Map();
  DATA.forEach(c => {
    const v = c[key] || '未知';
    m.set(v, (m.get(v) || 0) + 1);
  });
  return [...m.entries()].sort((a, b) => {
    const ua = isUnknown(a[0]) ? 1 : 0, ub = isUnknown(b[0]) ? 1 : 0;
    if (ua !== ub) return ua - ub;      // 未知沉底
    return b[1] - a[1];
  });
}

function buildFilters(){
  const defs = [
    { k:'ind',  label:'行业', items: countBy('ind') },
    { k:'nat',  label:'性质', items: countBy('nat') },
    { k:'city', label:'城市', items: countBy('c').filter(x => x[0]).slice(0, 12) },
    { k:'has',  label:'岗位', items: [['有岗位公告', 0], ['无岗位公告', 0]] },
  ];
  $('#filters').innerHTML = defs.map(d => {
    const chips = d.items.map(([v, n]) => {
      const label = v;
      const num = d.k === 'has' ? '' : '<span class="n">' + n + '</span>';
      return '<button class="chip" data-k="' + d.k + '" data-v="' + esc(v) + '">' + esc(label) + num + '</button>';
    }).join('');
    return '<div class="frow"><span class="flabel">' + d.label + '</span><div class="chips">' + chips + '</div></div>';
  }).join('');

  $('#filters').addEventListener('click', e => {
    const b = e.target.closest('.chip');
    if (!b) return;
    const k = b.dataset.k, v = b.dataset.v;
    const arr = F[k];
    const i = arr.indexOf(v);
    if (i < 0) arr.push(v); else arr.splice(i, 1);
    b.classList.toggle('on', i < 0);
    render();
  });
}

/* ---------- 关键词：标题命中优先，其次岗位名，再次主营 ---------- */
function score(c, q){
  if (!q) return 1;
  const n = c.n.toLowerCase(), b = (c.biz || '').toLowerCase();
  let s = 0;
  if (n.includes(q)) s += 100;
  const jobHit = c.jobs.filter(j => j.t.toLowerCase().includes(q)).length;
  if (jobHit) s += 40 + jobHit * 4;
  if (b.includes(q)) s += 10;
  return s;
}

function passes(c){
  if (F.ind.length && !F.ind.includes(c.ind || '未知')) return false;
  if (F.nat.length && !F.nat.includes(c.nat || '未知')) return false;
  if (F.city.length && !F.city.includes(c.c || '未知')) return false;
  if (F.has.length) {
    const want = F.has.includes('有岗位公告');
    if (want !== (c.jobs.length > 0)) return false;
  }
  return true;
}

function hl(text, q){
  const t = esc(text);
  if (!q) return t;
  try {
    return t.replace(new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi'), '<mark>$1</mark>');
  } catch(e){ return t; }
}

function render(){
  const q = Q.trim().toLowerCase();
  let rows = DATA.filter(passes).map(c => ({ c, s: score(c, q) }));
  if (q) rows = rows.filter(r => r.s > 1);
  rows.sort((a, b) => q ? b.s - a.s : (a.c.n.localeCompare(b.c.n, 'zh')));

  $('#cnt').textContent = rows.length;
  $('#empty').style.display = rows.length ? 'none' : 'block';
  $('#sorthint').textContent = q ? '按相关度排' : '按名称排';

  $('#list').innerHTML = rows.map(({ c }, i) => {
    const open = openSet.has(c.n);
    // 「未知」不铺在元信息行里：一行三个「未知」既占地方又像数据坏了。
    // 行业未知单独给个琥珀色小标（这是需要留意的事），其余未知直接省掉。
    const bits = [c.c, c.nat, c.sc].filter(v => v && v !== '未知')
      .map(v => '<span>' + esc(v) + '</span>');
    if (c.ind && c.ind !== '未知') bits.push('<span class="pill ind">' + esc(c.ind) + '</span>');
    else bits.push('<span class="pill none">行业未知</span>');
    const badges = bits.join('<span class="dot">·</span>');

    const jobs = c.jobs.length
      ? c.jobs.map(j =>
          '<div class="job"><div class="jt"><div class="jn">' + hl(j.t, q) + '</div>'
          + (j.r ? '<div class="jr">' + esc(j.r) + '</div>' : '')
          + '</div>' + (j.s && j.s !== '未知' ? '<span class="js">' + esc(j.s) + '</span>' : '') + '</div>'
        ).join('')
      : '<div class="nojob">这家没有抓到具体岗位公告。可能是公告未公开、只现场收简历，或数据还没补上 —— 不代表不招人，需要在现场问。</div>';

    return '<div class="card' + (open ? ' open' : '') + '" data-n="' + esc(c.n) + '">'
      + '<div class="chead" role="button" tabindex="0">'
        + '<div class="idx">' + (i + 1) + '</div>'
        + '<div class="cmain">'
          + '<div class="cname">' + hl(c.n, q) + '</div>'
          + '<div class="cmeta">' + badges + '</div>'
          + (c.biz ? '<div class="cbiz">' + hl(c.biz, q) + '</div>' : '')
        + '</div>'
        + '<div class="jcount"><b>' + c.jobs.length + '</b><span>岗位</span></div>'
      + '</div>'
      + '<div class="cbody">'
        + (c.biz ? '<div class="sect"><div class="sect-t">主营 / 业务范围</div><div class="biz">' + hl(c.biz, q) + '</div></div>' : '')
        + '<div class="sect"><div class="sect-t">岗位公告 · ' + c.jobs.length + ' 个</div>' + jobs + '</div>'
        + (c.js ? '<div class="src">岗位来源：' + esc(c.js) + '</div>' : '')
      + '</div>'
    + '</div>';
  }).join('');
}

/* ---------- 交互 ---------- */
$('#list').addEventListener('click', e => {
  const head = e.target.closest('.chead');
  if (!head) return;
  const card = head.closest('.card');
  const n = card.dataset.n;
  if (openSet.has(n)) openSet.delete(n); else openSet.add(n);
  card.classList.toggle('open');
});

$('#q').addEventListener('input', e => {
  Q = e.target.value;
  $('#clr').classList.toggle('on', !!Q);
  render();
});
$('#clr').addEventListener('click', () => {
  Q = ''; $('#q').value = ''; $('#clr').classList.remove('on'); render();
});

$('#expand').addEventListener('click', () => {
  const allOpen = openSet.size >= DATA.length;
  if (allOpen) { openSet.clear(); $('#expand').textContent = '全部展开'; }
  else { DATA.forEach(c => openSet.add(c.n)); $('#expand').textContent = '全部收起'; }
  render();
});

buildFilters();
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
