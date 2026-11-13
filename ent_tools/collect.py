"""按企业名自动采集公开资料，生成 raw_research.json。

只采三个字段：企业类型、所在城市、所属行业。
岗位不采 —— 校招公告分散在各校就业网，抓取成功率低，留给人工或模型。

纯标准库，Bing 搜索 + 规则抽取。
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

NATURE_RULES = [
    ("央企", ("中央企业", "中央直接管理", "国务院国资委", "央企")),
    ("国企", ("国有企业", "国有独资", "国有控股", "省属国有", "市属国有", "国资")),
    ("事业单位", ("事业单位", "公办", "科研院所")),
    ("外企", ("外商独资", "外资企业", "外国法人独资")),
    ("合资", ("中外合资", "合资经营")),
    ("民企", ("民营企业", "民营", "私营企业", "私营")),
]

INDUSTRY_RULES = [
    ("制造", ("制造", "机械", "装备", "汽车", "电子器件", "电气机械", "金属制品", "机床")),
    ("医药", ("医药", "制药", "医疗器械", "生物医药", "化学药")),
    ("IT", ("软件", "信息技术", "互联网", "通信", "计算机", "半导体", "集成电路")),
    ("金融", ("银行", "证券", "保险", "基金", "金融", "信托")),
    ("建筑", ("建筑", "施工", "工程建设", "土木", "房地产开发")),
    ("能源", ("电力", "石油", "天然气", "煤炭", "新能源", "化工", "能源")),
    ("消费", ("食品", "饮料", "家电", "零售", "服装", "日化", "批发和零售")),
    ("物流", ("物流", "快递", "运输", "仓储", "供应链管理")),
    ("教育", ("教育", "培训", "学校")),
]

CITY_RE = re.compile(
    r"(?:总部(?:位于|设在|坐落于)|公司位于|位于|地处|地址[：: ])"
    r"([\u4e00-\u9fa5]{2,6}?(?:市|自治州|地区))")
CITY_FALLBACK = re.compile(r"([\u4e00-\u9fa5]{2,5}市)")


def fetch(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def strip_html(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", html).strip()


def bing(q, n=5):
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(q)
    try:
        html = fetch(url)
    except Exception:
        return []
    out = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.S):
        block = m.group(0)
        link = re.search(r'<h2>\s*<a[^>]+href="([^"]+)"', block)
        title = re.sub(r"<[^>]+>", "", re.search(r"<h2>(.*?)</h2>", block, re.S).group(1)) \
            if re.search(r"<h2>(.*?)</h2>", block, re.S) else ""
        body = re.sub(r"<[^>]+>", " ", block)
        body = re.sub(r"\s+", " ", body).strip()
        if link:
            out.append({"url": link.group(1), "title": title.strip(), "snippet": body})
        if len(out) >= n:
            break
    return out


def pick_pages(results, name):
    """按优先级返回候选页面：官网 > 非聚合站 > 爱企查。百科直连会被 403，跳过。"""
    def score(u):
        if "baike.baidu.com" in u:
            return 99
        if any(s in u for s in ("qcc.com", "tianyancha")):
            return 80
        if "aiqicha.baidu.com" in u:
            return 60
        if any(s in u for s in ("zhihu.com", "weibo.com", "gov.cn", "map.baidu")):
            return 90
        return 10

    cands = sorted(results, key=lambda r: score(r["url"]))
    return [r["url"] for r in cands if score(r["url"]) < 90][:4]


def extract(text):
    nature = "未知"
    for value, keys in NATURE_RULES:
        if any(k in text for k in keys):
            nature = value
            break
    industry = "未知"
    for value, keys in INDUSTRY_RULES:
        if any(k in text for k in keys):
            industry = value
            break
    city = "未知"
    m = CITY_RE.search(text) or CITY_FALLBACK.search(text[:600])
    if m:
        city = m.group(1) if isinstance(m, re.Match) else "未知"
    return nature, city, industry


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="data/company_names.json")
    ap.add_argument("--out", default="data/raw_research.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.6)
    args = ap.parse_args(argv)

    names = json.loads(Path(args.names).read_text(encoding="utf-8-sig"))
    if args.limit:
        names = names[:args.limit]

    out, ok, fail = [], 0, 0
    t0 = time.time()
    for i, name in enumerate(names, 1):
        try:
            results = bing('"%s"' % name)
            body, page = "", None
            for cand in pick_pages(results, name):
                try:
                    text_try = strip_html(fetch(cand))
                except Exception:
                    continue
                if len(text_try) > 200:
                    body, page = text_try, cand
                    break
            text = body[:900] if body else " ".join(r["snippet"] for r in results)[:900]
            nature, city, industry = extract(text) if text else ("未知", "未知", "未知")
            hit = any(v != "未知" for v in (nature, city, industry))
            if hit:
                ok += 1
            else:
                fail += 1
            out.append({
                "name": name,
                "nature": nature,
                "city": city,
                "industry": industry,
                "jobSource": "未知",
                "source": page or "未知",
                "date": time.strftime("%Y-%m-%d"),
                "text": text or "未知",
                "jobs": [],
            })
            print("%3d/%d %-30s %-4s %-6s %-4s %s" % (
                i, len(names), name[:28], nature, city[:6], industry,
                "ok" if hit else "miss"))
        except Exception as exc:
            fail += 1
            print("%3d/%d %-30s ERROR %s" % (i, len(names), name[:28], exc))
            out.append({
                "name": name, "nature": "未知", "city": "未知", "industry": "未知",
                "jobSource": "未知", "source": "未知", "date": time.strftime("%Y-%m-%d"),
                "text": "未知", "jobs": [],
            })
        time.sleep(args.sleep)

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n完成：%d 家，命中 %d，未命中 %d，耗时 %.1f 秒" %
          (len(out), ok, fail, time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
