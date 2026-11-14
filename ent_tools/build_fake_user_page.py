# -*- coding: utf-8 -*-
"""把抽中的「虚假用户」的采集数据渲染成一页，附离线排名预览。

输入：data/_fake_user.json（画像文档）、data/_rank_out.json（预览排名）
输出：demo/采集数据_<姓名>.html
"""
import html as H
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DEMO = os.path.join(HERE, "..", "demo")

DIM_LABEL = {"city": "城市条件", "home": "离家近", "company_type": "企业类型",
             "industry": "行业", "scale": "企业规模档",
             "industry_status": "行业地位", "growth": "成长性"}


def e(s):
    return H.escape(str(s if s is not None else ""))


def main():
    doc = json.load(open(os.path.join(DATA, "_fake_user.json"), encoding="utf-8"))
    rk = json.load(open(os.path.join(DATA, "_rank_out.json"), encoding="utf-8"))
    ident = doc["identity"]
    ans = doc["answers"]
    w = ans["tri_weights"]
    exp = [x["text"] for x in (doc["resume"]["extraction"].get("experiences") or [])]
    rows = rk["rows"]
    hit_rows = [r for r in rows if r["intent_matches"]]

    # 问卷题单（与页面实际流程一致：Q1–Q16）
    qa = [
        ("Q1", "有没有简历", "上传了简历" if doc["resume"]["uploaded"] else "没有简历，直接口述"),
        ("Q2", "从简历里抽到的这几项，对不对", "预览数据未走这一步（直接按定义给定）"),
        ("Q3", "要改哪几项", "—"),
        ("Q4", "哪段经历最想让我们重点看",
         exp[0] if exp else "—"),
        ("Q5", "学校 / 专业 / 年级", "%s · %s · %s %s" % (ident["school"], ident["major"],
                                                     ident["degree"], ident["grade"])),
        ("Q6", "实习 / 竞赛 / 论文", "；".join(exp) if exp else "—"),
        ("Q7", "教培类企业愿不愿意看", "愿意看" if ans["accept_no_major"] else "不考虑"),
        ("Q8", "大模型推荐的岗位，勾了哪些", "、".join(ans["intended_jobs"])),
        ("Q9", "找工作最看重什么（7 维，可多选）",
         "、".join(DIM_LABEL[k] for k, v in w.items() if v)),
        ("Q10", "七维权重合计 100 + 家乡城市",
         " / ".join("%s %g" % (DIM_LABEL[k], v) for k, v in w.items() if v)
         + "　→　家乡 " + doc["fair"]["home_city"]),
        ("Q11", "城市排序", " > ".join("%s%d" % (k, v) for k, v in
                                    sorted(ans["city_rank"].items(), key=lambda x: x[1]))),
        ("Q12", "企业类型排序", " > ".join("%s%d" % (k, v) for k, v in
                                    sorted(ans["company_type_rank"].items(), key=lambda x: x[1]))),
        ("Q13", "行业排序", " > ".join("%s%d" % (k, v) for k, v in
                                   sorted(ans["industry_rank"].items(), key=lambda x: x[1]))),
        ("Q14", "有没有硬性条件（自由填）",
         "「%s」" % ans["hard_filter"] if ans["hard_filter"] else "没设限"),
        ("Q15", "保存这份画像", "保存（本次为预览，未入库）"),
        ("Q16", "知情同意", "已勾选"),
    ]

    wbar = "".join(
        '<div class="wrow"><span class="wn">%s</span>'
        '<span class="wt"><i style="width:%.0f%%"></i></span>'
        '<span class="wv">%g</span></div>' % (DIM_LABEL[k], (v / 30.0) * 100, v)
        for k, v in w.items())

    qa_html = "".join(
        '<tr><th>%s</th><td class="q">%s</td><td class="a">%s</td></tr>' % (k, e(q), e(a))
        for k, q, a in qa)

    top = rows[:10]
    top_html = "".join(
        '<tr><td class="rk">%d</td><td class="nm">%s%s</td><td class="bo">%s</td>'
        '<td class="sc">%.1f</td><td class="ht">%s</td></tr>' % (
            r["rank"], e(r["name"]),
            ' <span class="tag">%s</span>' % ("命中" if r["state"] == "hit" else "推断"),
            e(r["booth"]), r["score"],
            e("；".join("%s ↔ %s" % (m["job"], m["intent"]) for m in r["intent_matches"][:2]) or "—"))
        for r in top)

    hit_html = "".join(
        '<tr><td class="rk">%d</td><td class="nm">%s</td><td class="bo">%s</td><td class="sc">%.1f</td>'
        '<td class="ht">%s</td></tr>' % (
            r["rank"], e(r["name"]), e(r["booth"]), r["score"],
            e("；".join("%s ↔ %s" % (m["job"], m["intent"]) for m in r["intent_matches"])))
        for r in hit_rows)

    page = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>虚假用户采集数据 · __NAME__</title>
<style>
:root{--bg:#f7f7f5;--surface:#fff;--surface-2:#fbfbfa;--ink:#16181d;--ink-2:#3f4550;
 --muted:#7c8593;--faint:#a8b0bc;--line:#e6e6e3;--line-2:#d6d6d2;
 --accent:#3355d6;--accent-soft:#eef1fc;--accent-ink:#24409e;
 --ok:#1f8a4c;--ok-soft:#e8f5ec;--warn:#b7791f;--warn-soft:#fdf6e3;--bad:#c0392b;--bad-soft:#fdecea;
 --shadow:0 1px 2px rgba(20,22,28,.04),0 4px 16px rgba(20,22,28,.05)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:940px;margin:0 auto;padding:30px 20px 80px}
h1,h2,h3{margin:0;font-weight:660;letter-spacing:-.01em}
p{margin:0}
.kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);font-weight:650}
h1{font-size:26px;margin-top:6px}
.sub{color:var(--muted);font-size:13.5px;margin-top:6px}
.sec{margin-top:26px}
h2{font-size:16px;display:flex;align-items:center;gap:8px}
h2 .no{display:inline-flex;width:22px;height:22px;border-radius:6px;background:var(--accent-soft);
 color:var(--accent-ink);font-size:12px;align-items:center;justify-content:center;font-weight:700}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
 padding:16px 18px;box-shadow:var(--shadow);margin-top:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.kv .k{font-size:11.5px;color:var(--muted)}
.kv .v{font-size:15px;font-weight:620;margin-top:1px}
.kv .v.big{font-size:18px}
table{width:100%;border-collapse:collapse}
.qa th{width:46px;text-align:left;color:var(--faint);font-weight:600;font-size:12px;
 padding:8px 6px;border-bottom:1px solid var(--surface-2);vertical-align:top}
.qa td{padding:8px 6px;border-bottom:1px solid var(--surface-2);vertical-align:top;font-size:13.5px}
.qa td.q{width:190px;color:var(--muted)}
.qa td.a{color:var(--ink)}
.qa tr:last-child td,.qa tr:last-child th{border-bottom:0}
.wrow{display:flex;align-items:center;gap:10px;margin:7px 0}
.wn{width:88px;color:var(--ink-2);font-size:13px}
.wt{flex:1;height:8px;background:var(--surface-2);border:1px solid var(--line);border-radius:5px;overflow:hidden}
.wt i{display:block;height:100%;background:var(--accent);border-radius:4px}
.wv{width:34px;text-align:right;font-variant-numeric:tabular-nums;font-weight:650;font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.chips span{background:var(--surface-2);border:1px solid var(--line);border-radius:6px;
 padding:2px 8px;font-size:12.5px;color:var(--ink-2)}
.chips b{color:var(--accent-ink)}
.rk{color:var(--faint);font-variant-numeric:tabular-nums;font-size:12.5px;width:34px}
.nm{font-weight:600}
.bo{color:var(--muted);font-size:12.5px;width:56px}
.sc{text-align:right;font-weight:700;font-variant-numeric:tabular-nums;width:54px}
.ht{color:var(--ok);font-size:12.5px}
list td{border-bottom:1px solid var(--surface-2);padding:7px 6px}
.tag{font-size:11px;border-radius:4px;padding:0 5px;background:var(--ok-soft);color:var(--ok)}
.warnbox{background:var(--warn-soft);border:1px solid #f0e3c2;border-left:3px solid var(--warn);
 border-radius:8px;padding:10px 14px;font-size:13px;color:#6b5316;margin-top:10px}
.note{color:var(--muted);font-size:12.5px;margin-top:8px}
footer{margin-top:30px;color:var(--faint);font-size:12px;line-height:1.7}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;background:var(--surface-2);
 padding:1px 5px;border-radius:4px;border:1px solid var(--line)}
</style></head><body><div class="wrap">

<header>
  <div class="kicker">采集端 · 虚假用户样本（随机抽中）</div>
  <h1>__NAME__ · __CODE__</h1>
  <div class="sub">__SCHOOL__ · __MAJOR__ · __DEGREE__ __GRADE__　|　这份数据只是预览，<b>没有写进库里</b></div>
</header>

<div class="sec">
  <h2><span class="no">1</span>基本信息</h2>
  <div class="card grid">
    <div class="kv"><div class="k">姓名</div><div class="v big">__NAME__</div></div>
    <div class="kv"><div class="k">学校 / 专业</div><div class="v">__SCHOOL__ · __MAJOR__</div></div>
    <div class="kv"><div class="k">学历 / 届别</div><div class="v">__DEGREE__ · __GRADE__</div></div>
    <div class="kv"><div class="k">家乡</div><div class="v">__HOME__</div></div>
    <div class="kv"><div class="k">意向岗位</div><div class="v">__INTENT__</div></div>
    <div class="kv"><div class="k">教培类企业</div><div class="v">__TRAIN__</div></div>
    <div class="kv"><div class="k">硬性条件</div><div class="v">__HARD__</div></div>
    <div class="kv"><div class="k">画像完整度</div><div class="v">__QUALITY__</div></div>
  </div>
</div>

<div class="sec">
  <h2><span class="no">2</span>七维权重（加起来 100）</h2>
  <div class="card">__WBAR__</div>
</div>

<div class="sec">
  <h2><span class="no">3</span>三类排序（1 = 最想要）</h2>
  <div class="card">
    <div class="note">城市</div><div class="chips">__CITY__</div>
    <div class="note" style="margin-top:10px">企业类型</div><div class="chips">__CT__</div>
    <div class="note" style="margin-top:10px">行业</div><div class="chips">__IND__</div>
  </div>
</div>

<div class="sec">
  <h2><span class="no">4</span>问卷逐题答案（Q1–Q16）</h2>
  <div class="card"><table class="qa">__QA__</table></div>
</div>

<div class="sec">
  <h2><span class="no">5</span>如果提交，会排成什么样（离线预览）</h2>
  <div class="warnbox">
    这部分是<b>预览</b>：用这份画像 + demo 页面自己的 <code>calcOne()</code> 算的，
    只筛了 __FILTERNOTE__，<b>没有重跑企业的岗位判定</b>（库里 86 家仍是上一个画像的过滤结果），
    所以名次只能看「分数怎么来的」，不能当成最终榜单。
  </div>
  <div class="card"><table class="list">
    <tr><td class="rk">#</td><td class="nm">企业</td><td class="bo">展位</td>
        <td class="sc">分数</td><td class="ht">岗位语义命中</td></tr>
    __TOP__
  </table>
  <div class="note">前 10 名；共 __NHIT__ / __NTOT__ 家有岗位语义命中。</div></div>

  <details style="margin-top:10px">
    <summary style="cursor:pointer;color:var(--accent);font-size:13px">展开全部 __NHIT__ 家命中企业</summary>
    <div class="card" style="margin-top:8px"><table class="list">__HITALL__</table></div>
  </details>
</div>

<footer>
  数据文件：<code>ent_tools/data/_fake_user.json</code>　预览排名：<code>ent_tools/data/_rank_out.json</code><br>
  三个虚假用户（陈思远 / 林悦 / 周航）原本是 2026-11-14 全链路回归用的样本，跑完就删了；
  这次是按原定义重建的预览版，只放在本地，不写 <code>careersafari.db</code>。
</footer>
</div></body></html>"""

    intent = "、".join(ans["intended_jobs"])
    page = (page.replace("__NAME__", e(ident["name"]))
                .replace("__CODE__", e(doc["fair_id"].split("-")[2]))
                .replace("__SCHOOL__", e(ident["school"]))
                .replace("__MAJOR__", e(ident["major"]))
                .replace("__DEGREE__", e(ident["degree"]))
                .replace("__GRADE__", e(ident["grade"]))
                .replace("__HOME__", e(doc["fair"]["home_city"]))
                .replace("__INTENT__", e(intent))
                .replace("__TRAIN__", "愿意看" if ans["accept_no_major"] else "不考虑")
                .replace("__HARD__", e(ans["hard_filter"] or "没设限"))
                .replace("__QUALITY__", "%s 分（%s）" % (doc["quality"]["score"], doc["quality"]["level"]))
                .replace("__WBAR__", wbar)
                .replace("__CITY__", "".join('<span>%s <b>%d</b></span>' % (e(k), v) for k, v in
                                             sorted(ans["city_rank"].items(), key=lambda x: x[1])))
                .replace("__CT__", "".join('<span>%s <b>%d</b></span>' % (e(k), v) for k, v in
                                           sorted(ans["company_type_rank"].items(), key=lambda x: x[1])))
                .replace("__IND__", "".join('<span>%s <b>%d</b></span>' % (e(k), v) for k, v in
                                            sorted(ans["industry_rank"].items(), key=lambda x: x[1])))
                .replace("__QA__", qa_html)
                .replace("__TOP__", top_html)
                .replace("__HITALL__", hit_html)
                .replace("__NHIT__", str(len(hit_rows)))
                .replace("__NTOT__", str(len(rows)))
                .replace("__FILTERNOTE__",
                         ("「%s」" % rk["input"]["city_filter"]) if rk["input"].get("city_filter")
                         else (("「%s 行业」" % rk["input"]["industry_filter"]) if rk["input"].get("industry_filter")
                               else ("无（写的是「%s」，但这类条件现在不参与筛选，等于不设限）" % ans["hard_filter"]
                                     if ans["hard_filter"] else "无（未设城市/行业限制）"))))

    out = os.path.join(DEMO, "采集数据_%s.html" % ident["name"])
    open(out, "w", encoding="utf-8").write(page)
    print("已生成 %s（%.1f KB）" % (out, len(page) / 1024))


if __name__ == "__main__":
    main()
