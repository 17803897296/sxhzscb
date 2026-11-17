"""企业资料采集：企业名 -> 大模型联网判断 -> research_cache.json（李欣企业端的输入）。

链路：
    data/company_names.json
        -> 本脚本（Bing 抓页面 + DeepSeek 抽取）
        -> data/research_cache.json + data/research_report.md
        -> 李欣的 company_module.py 直接吃

要点：
1. text 由本脚本从网页原文截取，**不由模型生成**，保证 jobEvidence 能逐字对上。
2. 模型的岗位输出里必须分职责/地点/阶段，本脚本按匹配端要求的模板拼 requirements。
3. 模型说不知道就写「未知」，本脚本不补、不猜。

依赖：只用标准库。需要 .env 里的 DEEPSEEK_API_KEY。
"""

import argparse
import json
import os

import ind_code
import re
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

from webtext import UA, fetch, strip_html  # noqa: F401  UA 保留在本模块命名空间

NATURE = ["央企", "国企", "民企", "外企", "合资", "事业单位"]
INDUSTRY = ["制造", "医药", "IT", "金融", "建筑", "能源", "消费", "物流", "教育", "政府事业", "其他"]
JOB_SOURCE = ["校招公告", "社招信息", "websearch", "未知"]
URGENCY = ["高", "中", "低", "未知"]

# 行业码的码表文本：直接取自 ind_code.CODES（唯一真源），不在这里抄一遍——
# 抄一份就会漂移，而漂移之后「企业搜索打码」和「匹配端查表」会各说各话。
_CODE_TABLE = "\n".join("  %-10s %s" % (c[0], c[1]) for c in ind_code.CODES)

PROMPT = """你在把一段企业公开资料整理成结构化字段。只输出 JSON，不要解释。

【企业】{name}
【网页原文】
<<<
{page_text}
>>>

【输出格式】
{{
  "nature": "央企|国企|民企|外企|合资|事业单位|未知",
  "city": "总部所在城市，查不到写 未知",
  "industry": "制造|医药|IT|金融|建筑|能源|消费|物流|教育|政府事业|其他|未知",
  "biz_scope": "这家靠什么挣钱：主营产品/服务 + 应用领域，40~70 字，如「汽车空调压缩机及热管理系统的研发生产，配套新能源整车厂」；原文没有业务介绍就写 未知",
  "ind_code": "行业码，只能从下面码表里选一个；判不出写 unknown",

【行业码表】
{codes},
  "bizEvidence": "支撑 biz_scope 的原文片段，逐字复制，不改写",
  "jobSource": "校招公告|社招信息|websearch|未知",
  "evidence": "从原文中逐字复制的一句话，必须是原文片段，不能改写",
  "jobs": [
    {{
      "title": "岗位名称",
      "duty": "这个岗位具体做什么，用原文里的动词，如 软件开发、系统设计",
      "place": "工作地点，原文没有写 未知",
      "stage": "实习|校招|社招|未知",
      "requirements": "学历专业等条件",
      "headcount": 数字或 "未知",
      "urgency": "高|中|低|未知",
      "group": "部门，没有写 未知",
      "salary": "薪资原文，没有写 未知"
    }}
  ]
}}

【铁律】
1. 只写原文里有的。原文没提到岗位，jobs 写 []，jobSource 写 "未知"。
2. duty 必须用原文动词，不许自己概括。没有职责描述就写 "未知"，并让该岗位的 duty 为 "未知"。
3. stage 只有原文出现「实习/校招/校园招聘/应届/春招/秋招/社招」才能填，否则写 "未知"。
4. evidence 必须逐字出现在上面的原文里，一个字都不能改。
5. 不要编造人数、薪资、部门。
6. **社招、联网搜到的岗位记录都算数**：找到社招岗位写 "社招信息"；
   从搜索引擎 / 招聘站 / 高校就业网等公开页面搜到岗位记录、但不是正式校招公告，
   写 "websearch"（意思是记录来自联网搜索，不保证岗位列表齐全）。
   jobSource 优先级：校招公告 → "校招公告"；只有社招 → "社招信息"；
   联网搜到岗位记录 → "websearch"；只找到主营业务介绍 → **"未知" 且 jobs 写 []**，
   不许从业务描述里推测岗位（推测出来的不是事实，会让下游误判）。
7. **ind_code 只看主营，不看它招什么岗**：一家食品厂招「研发工程师」也还是
   food，不许因为岗位名像就往别处写。同一段话列了很多方向时（「期刊覆盖生物、
   化学、医药、机械」），取**它靠什么挣钱**的那一个，不要取列举词。
   控股/投资平台按它实际经营的业务判；纯粹投资控股写 unknown。宁可写 unknown，
   也不要凑一个不贴的码——码错了会让整个专业族的学生被误判。
8. **biz_scope 与岗位无关，任何情况下都要填**：jobs 为空时 biz_scope 只能是唯一的业务
   线索，写空会让下游无法判断这家单位跟学生意向有没有交叉。只写原文说过的产品/服务，
   不写「行业领先」「技术先进」这类空话；bizEvidence 必须逐字出现在原文里，
   找不到可引的句子就 biz_scope 也写 "未知"（宁可空，不可编）。"""


def load_env(path=".env"):
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def bing(q, n=5):
    try:
        html = fetch("https://cn.bing.com/search?q=" + urllib.parse.quote(q))
    except Exception:
        return []
    out = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.S):
        block = m.group(0)
        link = re.search(r'<h2>\s*<a[^>]+href="([^"]+)"', block)
        title = re.search(r"<h2>(.*?)</h2>", block, re.S)
        body = re.sub(r"<[^>]+>", " ", block)
        body = re.sub(r"\s+", " ", body).strip()
        if link:
            out.append({"url": link.group(1),
                        "title": re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else "",
                        "snippet": body})
        if len(out) >= n:
            break
    return out


SKIP = ("zhihu.com", "weibo.com", "gov.cn", "map.baidu", "baike.baidu.com",
        "qcc.com", "tianyancha", "douyin", "xiaohongshu")

JOB_HINT = ("招聘", "校招", "校园招聘", "岗位", "recruit", "zhaopin", "campus", "job", "hr")


def candidates(results, limit=4):
    """优先招聘页，其次官网首页。"""
    good = [r for r in results if not any(s in r["url"] for s in SKIP)]
    agg = [r for r in results if "aiqicha.baidu.com" in r["url"]]

    def score(r):
        blob = (r["url"] + " " + r["title"]).lower()
        return -sum(1 for k in JOB_HINT if k.lower() in blob or k in blob)

    return [r["url"] for r in sorted(good + agg, key=score)][:limit]


JOB_ACCEPT = ("职责", "岗位名称", "专业", "学历", "招聘人数", "工作地点",
              "任职要求", "岗位要求", "岗位描述", "招聘岗位")


def focus(text, limit=3000):
    """长页面里把窗口挪到招聘信息附近，别只喂导航。"""
    if len(text) <= limit:
        return text
    best = -1
    for kw in ("校园招聘", "招聘岗位", "招聘专业", "岗位职责", "招聘", "应聘"):
        i = text.find(kw)
        if i > 0:
            best = i if best < 0 else min(best, i)
            break
    if best < 0:
        return text[:limit]
    start = max(0, best - 800)
    return text[start:start + limit]


def get_context(name):
    """三条查询：校招 / 社招 / 企业信息。社招也算数——有岗就说明企业有这个需求。"""
    parts, sources = [], []

    for label, query, size in (
            ("招聘页面（校招）", '"%s" 校园招聘' % name, 4000),
            ("招聘页面（社招）", '"%s" 招聘' % name, 2500),
    ):
        results = bing(query)
        for url in candidates(results):
            try:
                text = strip_html(fetch(url))
            except Exception:
                continue
            if len(text) > 300 and sum(1 for k in JOB_ACCEPT if k in text) >= 2:
                parts.append("【%s】\n" % label + focus(text, size))
                sources.append(url)
                break

    general = bing('"%s"' % name)
    for url in candidates(general):
        try:
            text = strip_html(fetch(url))
        except Exception:
            continue
        if len(text) > 300:
            parts.append("【企业信息页面】\n" + focus(text, 1500))
            sources.append(url)
            break

    if not parts:
        merged = " ".join(r["title"] + " " + r["snippet"]
                          for r in (general + bing('"%s" 招聘' % name)))[:3000]
        if merged:
            return merged, [r["url"] for r in general[:1]]
        return "", ["未知"]
    return "\n\n".join(parts), sources


def call_model(name, page_text, key, base, model):
    prompt = PROMPT.format(name=name, page_text=page_text, codes=_CODE_TABLE)
    url = base.rstrip("/") + "/chat/completions"

    def post(payload):
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    base_payload = {"model": model, "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}]}
    try:
        payload = post(dict(base_payload, response_format={"type": "json_object"}))
    except urllib.error.HTTPError as exc:
        # 有些免费/第三方接口不支持 response_format，去掉重试一次
        if exc.code in (400, 422):
            payload = post(base_payload)
        else:
            raise
    content = payload["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    start, end = content.find("{"), content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]
    return json.loads(content)


def pick(value, allowed):
    value = str(value or "").strip()
    return value if value in allowed else "未知"


def compose_requirements(job):
    """按匹配端能解析的模板拼，缺项写「未知」。"""
    duty = str(job.get("duty") or "未知").strip()
    place = str(job.get("place") or "未知").strip()
    stage = str(job.get("stage") or "未知").strip()
    req = str(job.get("requirements") or "未知").strip()
    return "职责：%s；工作地点：%s；招聘阶段：%s；要求：%s" % (duty, place, stage, req)


def build_entry(name, raw, page_text, source):
    # 逐岗 URL：岗位是从这一页正文里抽出来的，就把这一页的地址贴到每个岗位上。
    # 只认真实抓到的 http 地址；企业级 source 是「未知」时留空，绝不编一个 URL 出来。
    source_url = str(source or "").strip()
    if not source_url.startswith("http"):
        source_url = ""
    jobs = []
    for job in (raw.get("jobs") or []):
        title = str(job.get("title") or "").strip()
        if not title or title == "未知":
            continue
        count = job.get("headcount")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            count = "未知"
        jobs.append({
            "title": title,
            "requirements": compose_requirements(job),
            "headcount": count,
            "urgency": pick(job.get("urgency"), URGENCY),
            "group": str(job.get("group") or "未知").strip() or "未知",
            "salary": str(job.get("salary") or "未知").strip() or "未知",
            "source_url": source_url,
        })

    source_kind = pick(raw.get("jobSource"), JOB_SOURCE)
    evidence = str(raw.get("evidence") or "").strip()

    # 主营（biz_scope）：下游「岗位方向」判定的**唯一证据源**。必须能在原文里找到
    # 出处，否则退成「未知」——实测量产后模型会凭企业名编一段像模像样的业务描述
    # （「专注于高端装备制造」），编错方向的代价是误杀真匹配，比空着更糟。
    # 注意与岗位解耦：jobs 全空、jobSource=未知 时，主营照样要留下。
    biz_scope = str(raw.get("biz_scope") or "").strip()
    biz_ev = str(raw.get("bizEvidence") or "").strip()
    if not biz_scope or biz_scope == "未知" or not biz_ev or biz_ev not in page_text:
        biz_scope = "未知"

    # 原文对不上就降级，绝不硬留
    if source_kind != "未知" and (not evidence or evidence not in page_text):
        source_kind, jobs, evidence = "未知", [], "未知"
    if source_kind == "未知":
        jobs, evidence = [], "未知"

    return {
        "name": name,
        "nature": pick(raw.get("nature"), NATURE),
        "city": str(raw.get("city") or "未知").strip() or "未知",
        "industry": pick(raw.get("industry"), INDUSTRY),
        "biz_scope": biz_scope,
        "jobs": jobs,
        "jobSource": source_kind,
        "jobEvidence": evidence,
        "source": source,
        "date": time.strftime("%Y-%m-%d"),
        "text": page_text or "未知",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="企业名 -> 大模型采集 -> research_cache.json")
    ap.add_argument("--names", default="data/company_names.json")
    ap.add_argument("--out", default="data/research_cache.json")
    ap.add_argument("--report", default="data/research_report.md")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--dry", action="store_true", help="只抓页面不调模型，用来看覆盖率")
    args = ap.parse_args(argv)

    load_env()
    if args.dry:
        names = json.loads(Path(args.names).read_text(encoding="utf-8-sig"))
        if args.limit:
            names = names[:args.limit]
        ok, no_job, dead, lines = 0, [], [], []
        t0 = time.time()
        for i, name in enumerate(names, 1):
            try:
                text, srcs = get_context(name)
            except Exception as exc:
                text, srcs = "", []
                lines.append("%s：抓取异常（%s）" % (name, exc))
            has_job = "【招聘页面（校招）】" in text or "【招聘页面（社招）】" in text
            if has_job:
                ok += 1
            elif text:
                no_job.append(name)
            else:
                dead.append(name)
            print("%3d/%d %-26s %5d字 招聘页=%s" % (i, len(names), name[:24], len(text), "有" if has_job else "无"))
            time.sleep(args.sleep)
        print("\n覆盖率：%d/%d 抓到招聘页（%.0f%%）" % (ok, len(names), 100.0 * ok / max(1, len(names))))
        print("只有企业信息页：%d 家" % len(no_job))
        print("完全抓不到：%d 家" % len(dead))
        out = Path(args.out).with_name("dry_run_report.md")
        body = ["# 抓取覆盖率试跑", "",
                "- 总数：%d" % len(names),
                "- 抓到招聘页：%d" % ok,
                "- 只有企业信息页：%d" % len(no_job),
                "- 完全抓不到：%d" % len(dead),
                "- 耗时：%.1f 秒" % (time.time() - t0), "",
                "## 只有企业信息页（需要人工补岗位）", ""]
        body += ["- " + n for n in no_job] or ["- 无"]
        body += ["", "## 完全抓不到", ""]
        body += ["- " + n for n in dead] or ["- 无"]
        body += ["", "## 异常", ""] + (["- " + x for x in lines] or ["- 无"])
        out.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("明细：%s" % out)
        return 0
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    if not key:
        print("缺少 DEEPSEEK_API_KEY。在 data/../.env 里写一行：\nDEEPSEEK_API_KEY=你的密钥", file=sys.stderr)
        return 2

    names = json.loads(Path(args.names).read_text(encoding="utf-8-sig"))
    if args.limit:
        names = names[:args.limit]

    entries, report, ok_jobs, ok_fields, ok_biz, no_biz, fail = [], [], 0, 0, 0, [], 0
    # 行业码走旁路（2026-11-17）：research_cache 的 entry 结构被契约严格校验
    # （多一个键就报错），所以码不进 entry，单独攒一份 data/_ind_code_new.json，
    # 由 refresh_all 的 step_ind_code 合并落库。好处是这里「顺手打一次」不增加
    # 任何调用成本，而落库口径仍由 ind_code 一条链统一（人工覆写 > 模型 > 规则）。
    new_codes, code_ok = [], 0
    t0 = time.time()
    for i, name in enumerate(names, 1):
        try:
            page_text, sources = get_context(name)
            if not page_text:
                raise RuntimeError("页面抓不到")
            raw = call_model(name, page_text, key, base, model)
            entry = build_entry(name, raw, page_text, (sources or ["未知"])[0])
            entries.append(entry)
            if entry["jobs"]:
                ok_jobs += 1
            if entry["jobSource"] != "未知":
                ok_fields += 1
            # 主营是匹配端判「岗位方向」的唯一证据源，覆盖率要单独报 —— 只统计
            # 「拿到岗位」会掩盖「有岗无主营」的企业（岗位名不带方向时下游全靠主营）
            if entry["biz_scope"] != "未知":
                ok_biz += 1
            else:
                no_biz.append(name)
            _code = ind_code.normalize(raw.get("ind_code"))
            if _code != ind_code.UNKNOWN:
                code_ok += 1
                new_codes.append({"name": name, "code": _code,
                                  "why": str(raw.get("ind_code_why") or "")[:40],
                                  "date": time.strftime("%Y-%m-%d"), "src": "enrich"})
            if not entry["jobs"]:
                report.append("%s：无岗位（%s）" % (name, entry["jobSource"]))
            print("%3d/%d %-28s %-5s %-8s jobs=%d 主营=%s" % (
                i, len(names), name[:26], entry["nature"], entry["city"][:8],
                len(entry["jobs"]), "有" if entry["biz_scope"] != "未知" else "无"))
        except Exception as exc:
            fail += 1
            print("%3d/%d %-28s 失败：%s" % (i, len(names), name[:26], exc))
            report.append("%s：采集失败（%s）" % (name, exc))
            no_biz.append(name)
            entries.append({
                "name": name, "nature": "未知", "city": "未知", "industry": "未知",
                "biz_scope": "未知",
                "jobs": [], "jobSource": "未知", "jobEvidence": "未知",
                "source": "未知", "date": "未知", "text": "未知"})
        time.sleep(args.sleep)

    Path(args.out).write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if new_codes:
        _cp = Path(args.out).with_name("_ind_code_new.json")
        _old = {}
        if _cp.exists():
            try:
                _old = {x["name"]: x for x in json.loads(_cp.read_text(encoding="utf-8"))}
            except Exception:
                _old = {}
        for x in new_codes:
            _old[x["name"]] = x        # 同名覆盖：本次采集比历史记录新
        _cp.write_text(json.dumps(sorted(_old.values(), key=lambda x: x["name"]),
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("行业码：%d 家（累计 %d）-> %s" % (code_ok, len(_old), _cp))
    lines = ["# 采集报告", "",
             "- 企业数：%d" % len(entries),
             "- 拿到岗位：%d" % ok_jobs,
             "- 拿到主营：%d（缺 %d）" % (ok_biz, len(no_biz)),
             "- 打出行业码：%d" % code_ok,
             "- 有来源：%d" % ok_fields,
             "- 失败：%d" % fail,
             "- 耗时：%.1f 秒" % (time.time() - t0), "", "## 明细", ""]
    lines += ["- " + x for x in report] or ["- 无"]
    if no_biz:
        lines += ["", "## 主营缺失（匹配端判岗位方向会失效，需人工补或跑 _biz_fill2.py）", ""]
        lines += ["- " + n for n in no_biz]
    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n完成：%d 家，带岗位 %d，失败 %d，耗时 %.1f 秒" %
          (len(entries), ok_jobs, fail, time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
