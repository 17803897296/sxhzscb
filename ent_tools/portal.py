"""本地演示服务：静态页面 + 联网搜索接口。

    python -X utf8 portal.py --port 8099

路由：
    GET  /                 -> demo 静态文件
    POST /api/lookup       -> {"names": [...], "url": "..."} 补全企业画像
    GET  /api/health       -> 健康检查

只用标准库。搜索走 Bing，抽取用规则；抓不到一律写「未知」，不编。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp                       # 常驻浏览器 + 多标签页渲染（失败自动退回一次性 --dump-dom）
import enrich_by_llm as enrich   # 复用模型调用与模板拼装
import job_store
from webtext import UA           # noqa: F401  UA 保留在本模块命名空间
from webtext import fetch as _fetch
from webtext import strip_html   # noqa: F401  对外仍叫 portal.strip_html（_js_ab.py 在用）

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


CITIES = ["南京", "苏州", "无锡", "常州", "镇江", "扬州", "南通", "徐州", "盐城", "泰州",
          "淮安", "连云港", "宿迁", "上海", "北京", "杭州", "合肥", "深圳", "广州",
          "武汉", "成都", "西安", "天津", "青岛", "济南", "长沙", "厦门", "郑州", "重庆"]

# 只在原文有明确表述时判性质，宁缺勿错
NATURE_MAP = [
    ("央企", ("中央企业", "国有独资", "国资委履行出资人职责", "国务院国资委")),
    ("国企", ("国有企业", "国有控股", "国资控股", "省属国有企业", "市属国有企业")),
    ("事业单位", ("事业单位", "公益一类", "公益二类")),
]

JOB_WORDS = ("工程师", "专员", "经理", "主管", "助理", "顾问", "分析师", "设计师",
             "研究员", "技术员", "管培生", "实习生", "销售", "会计", "出纳",
             "开发", "运营", "采购", "质检", "法务", "人力", "行政", "翻译", "教师")

JOB_ACCEPT = ("职责", "岗位名称", "专业", "学历", "招聘人数", "工作地点",
              "任职要求", "岗位要求", "岗位描述", "招聘岗位")

CACHE = {}   # 名称 -> 结果，同一进程内不重复抓
CACHE_FILE = ROOT / "ent_tools" / "data" / "lookup_cache.json"


def load_cache():
    """启动时读回上次 lookup 的结果，避免重启后 10 家有岗位的数据又消失。"""
    try:
        items = json.loads(CACHE_FILE.read_text(encoding="utf-8-sig"))
    except OSError:
        return 0
    except Exception as exc:
        sys.stderr.write("  lookup 缓存读取失败：%s\n" % str(exc)[:120])
        return 0
    if isinstance(items, dict):
        for name, item in items.items():
            if isinstance(item, dict) and item.get("name"):
                CACHE[name] = item
    elif isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                CACHE[item["name"]] = item
    return len(CACHE)


def save_cache():
    """原子写回 lookup 缓存；失败不阻塞请求。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(CACHE, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:
        sys.stderr.write("  lookup 缓存写入失败：%s\n" % str(exc)[:120])

# 企业端离线契约数据（company_module.py 的产出）。有岗位的企业直接命中，
# 没有岗位的仍走联网补全；新文件写入后按 mtime 自动重新加载。
COMPANIES_FILE = ROOT / "ent_tools" / "data" / "companies.json"
CONTRACT_FIELDS = ("id", "name", "nature", "city", "industry", "jobs",
                   "jobSource", "jobEvidence", "source", "date")
LIB = {}         # 名称 -> 企业端记录
LIB_MTIME = None


def load_offline_companies(force=False):
    """读取 data/companies.json。只接受锁定契约的 10 个字段。"""
    global LIB_MTIME
    try:
        mtime = COMPANIES_FILE.stat().st_mtime
    except OSError:
        return len(LIB)
    if not force and mtime == LIB_MTIME:
        return len(LIB)
    try:
        items = json.loads(COMPANIES_FILE.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        sys.stderr.write("  离线企业库读取失败：%s\n" % str(exc)[:120])
        return len(LIB)
    if not isinstance(items, list):
        sys.stderr.write("  离线企业库顶层必须是数组，已忽略\n")
        return len(LIB)
    lib = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry = {key: item.get(key, "未知") for key in CONTRACT_FIELDS}
        if not isinstance(entry["jobs"], list):
            entry["jobs"] = []
        lib[name] = entry
    LIB.clear()
    LIB.update(lib)
    LIB_MTIME = mtime
    return len(LIB)


MODEL_ON = {}   # 有 key 就填 {key, base, model}

# 快搜索的初筛表：学生方向 → 企业名里可能出现的行业词
DIRECT_HINTS = {
    "财务": ["银行", "证券", "基金", "保险", "会计", "审计", "投资", "资产", "财务", "信托", "期货"],
    "法务": ["律师事务所", "律所", "法律", "法务"],
    "研发": ["科技", "软件", "信息", "电子", "通信", "网络", "智能", "数据", "自动化",
             "芯片", "半导体", "光电", "机器人", "互联网", "计算"],
    "技术": ["科技", "软件", "信息", "电子", "通信", "网络", "智能", "数据", "自动化",
             "芯片", "半导体", "光电", "机器人", "互联网", "计算"],
    "产品": ["科技", "软件", "信息", "网络", "智能", "文化", "传媒", "设计"],
    "设计": ["设计", "文化", "传媒", "建筑", "装饰"],
    "运营": ["文化", "传媒", "电商", "贸易", "商贸", "零售", "集团", "教育", "网络"],
    "市场": ["文化", "传媒", "贸易", "商贸", "零售", "集团", "销售", "咨询"],
    "销售": ["贸易", "商贸", "销售", "实业", "集团", "零售", "咨询"],
    "商务": ["贸易", "商贸", "实业", "集团", "咨询"],
    "供应链": ["物流", "运输", "供应链", "仓储", "快递", "贸易", "商贸"],
    "物流": ["物流", "运输", "供应链", "仓储", "快递"],
    "生产": ["制造", "机械", "装备", "电气", "材料", "化工", "工业", "能源", "电力", "汽车"],
    "制造": ["制造", "机械", "装备", "电气", "材料", "化工", "工业", "汽车"],
    "工程": ["建筑", "建设", "工程", "设计院", "能源", "电力", "市政", "交通"],
    "医药": ["医药", "药业", "制药", "生物", "医疗", "医院", "健康", "医学"],
    "医护": ["医药", "医院", "医疗", "健康", "护理", "医学"],
    "生物": ["生物", "医药", "基因", "生命"],
    "教育": ["大学", "学院", "学校", "教育", "培训", "中学", "小学"],
    "科研": ["研究院", "研究所", "科学院", "实验室", "研究中心"],
    "人力": ["集团", "人力", "咨询", "管理"],
    "行政": ["集团", "实业", "管理"],
}

MAJOR_HINTS = {
    "管理": ["集团", "实业", "咨询", "贸易", "商贸", "物流", "制造", "银行"],
    "经济": ["银行", "证券", "基金", "保险", "投资", "信托"],
    "金融": ["银行", "证券", "基金", "保险", "投资", "信托", "期货"],
    "会计": ["银行", "会计", "审计", "证券", "基金"],
    "计算机": ["科技", "软件", "信息", "网络", "数据", "智能", "电子", "通信"],
    "软件": ["科技", "软件", "信息", "网络", "数据", "计算"],
    "电子": ["电子", "通信", "半导体", "芯片", "光电", "科技"],
    "机械": ["制造", "机械", "装备", "汽车", "工业", "自动化"],
    "电气": ["电气", "电力", "能源", "电网", "自动化", "制造"],
    "土木": ["建筑", "建设", "工程", "市政", "交通", "设计院"],
    "化工": ["化工", "材料", "能源", "制药", "环境"],
    "医学": ["医院", "医药", "医疗", "生物", "健康"],
    "药学": ["医药", "药业", "制药", "生物"],
    "法学": ["律师事务所", "律所", "法务", "法律"],
    "新闻": ["传媒", "文化", "出版", "广告", "网络"],
    "教育": ["大学", "学院", "学校", "教育", "培训"],
}


PREFILTER_PROMPT = """你在帮一位大四 / 研三学生，从双选会名单里挑出值得提前搜的企业。

【学生】
- 专业：{major}
- 期望方向 / 岗位：{direction}

【企业名单】（共 {n} 家）
{listing}

【任务】
先想清楚：这个专业 + 这个方向，通常对应什么岗位、哪些行业会招。
再据此判断名单里哪些企业**可能**招这类人。

判断规则：
1. 企业名字能看出行业的，按行业判断。
2. 看不出来的（如「某某实业」「某某集团」），按你对这家公司的了解推断；没听过的，**保留**。
3. **不确定的一律保留**，宁可多留，不要误杀。
4. 不要因为企业规模小、名气低就排除。
5. picked 里只能出现名单里的原名，一个字都不能改。

只输出 JSON，不要解释：
{{
  "interpretation": "一句话：这个专业+方向对应的岗位和行业",
  "industries": ["主要对口行业，2-5 个"],
  "roles": ["典型岗位名，2-5 个"],
  "picked": ["企业名1", "企业名2"]
}}"""


def _llm_json(prompt, max_tokens=2000):
    """通用的一次模型调用，返回解析后的 JSON。"""
    import urllib.error
    key, base, model = MODEL_ON["key"], MODEL_ON["base"], MODEL_ON["model"]
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    def post(body):
        req = urllib.request.Request(
            url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = post(payload)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 422):
            payload.pop("response_format", None)
            data = post(payload)
        else:
            raise
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        text = text[i:j + 1]
    return json.loads(text)


def prefilter_llm(names, direction, major):
    """让模型理解专业+方向，再挑企业。分批送，避免超过上下文。"""
    if not MODEL_ON:
        hit, skipped, keys = filter_names(names, direction, major)
        return {"picked": hit, "interpretation": "未配置模型，按名字关键词初筛",
                "industries": [], "roles": [], "keys": keys, "skipped": skipped, "engine": "rules"}

    CHUNK = 60
    picked, seen = [], set()
    interpretation, industries, roles = "", [], []

    for i in range(0, len(names), CHUNK):
        batch = names[i:i + CHUNK]
        listing = "\n".join("%d. %s" % (k + 1, n) for k, n in enumerate(batch))
        prompt = PREFILTER_PROMPT.format(
            major=major or "未填", direction=direction or "未填",
            n=len(batch), listing=listing)
        try:
            out = _llm_json(prompt)
        except Exception as exc:
            sys.stderr.write("  prefilter fail: %s\n" % str(exc)[:80])
            continue

        if not interpretation and out.get("interpretation"):
            interpretation = str(out["interpretation"])[:120]
            industries = [str(x)[:10] for x in (out.get("industries") or [])][:5]
            roles = [str(x)[:10] for x in (out.get("roles") or [])][:5]

        valid = set(batch)
        for name in (out.get("picked") or []):
            name = str(name).strip()
            if name in valid and name not in seen:
                seen.add(name)
                picked.append(name)

    # 模型漏掉的公司不硬塞；保底不超过一半，太窄就退回规则
    if not picked:
        hit, skipped, keys = filter_names(names, direction, major)
        return {"picked": hit, "interpretation": interpretation or "模型未返回可用结果，按关键词初筛",
                "industries": industries, "roles": roles, "keys": keys,
                "skipped": skipped, "engine": "rules"}
    return {"picked": picked, "interpretation": interpretation,
            "industries": industries, "roles": roles,
            "keys": [], "skipped": len(names) - len(picked), "engine": "llm"}
def filter_names(names, direction, major):
    """按方向先筛；方向没命中才退回专业。快搜索的兜底方案（无模型时用）。"""
    def collect(text, table):
        out = []
        for k, words in table.items():
            if text and k in str(text):
                out += words
        return list(dict.fromkeys(out))

    keys = collect(direction, DIRECT_HINTS)
    source = "方向"
    if not keys:
        keys = collect(major, MAJOR_HINTS)
        source = "专业"
    if not keys:
        return list(names), 0, []

    hit, miss = [], []
    for n in names:
        (hit if any(k in n for k in keys) else miss).append(n)
    return hit, len(miss), keys


def setup_model():
    enrich.load_env()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return False
    MODEL_ON.update({
        "key": key,
        "base": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    })
    return True


# 并发度靠「每家一个线程」提升，但真正在飞的 HTTP / 浏览器实例必须有上限：
# 无头浏览器尤其贵，6 个线程各开一个会互相拖死（实测比串行还慢）。
# 限流只挡并发数，不改变任何一家拿到的是哪个页面。
#
# 2026-11-16 改：浏览器改成「一个常驻进程 + N 个标签页」（见 cdp.py），
# 冷启动只付一次，标签页只有几十 ms，所以不再卡在 2 个进程。
# _TAB_SEM 管同时渲染几个页面（实测 6 是甜点：8 页 4.9s，8 个并行反而更慢）；
# _BROWSER_SEM 只在 CDP 不可用的兜底路径上生效，语义与改动前一致。
_HTTP_SEM = threading.Semaphore(12)
_BROWSER_SEM = threading.Semaphore(2)
JS_TABS = max(1, int(os.environ.get("FAIR_JS_TABS", "6")))
_TAB_SEM = threading.Semaphore(JS_TABS)
# FAIR_JS_CDP=0 强制走旧的一次性 --dump-dom：A/B 对照和线上应急都在用这个开关
JS_CDP = os.environ.get("FAIR_JS_CDP", "1") == "1"


def fetch(url, timeout=12):
    """本模块的抓取一律受并发上限约束（12 个 socket），实现见 webtext.fetch。

    留这三行而不是每个调用点传 sem：上限是这一层的政策，钉在边界上比散在
    四个调用点更难漏。默认超时 12 与本模块历史值一致。
    """
    return _fetch(url, timeout=timeout, sem=_HTTP_SEM)


BROWSER_PATHS = [
    os.environ.get("EDGE_PATH", ""),
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    shutil.which("msedge") or "",
    shutil.which("chrome") or "",
]
BROWSER = None
# 默认不在全量搜索里自动开无头浏览器：43 家会跑很久。
# 需要自动渲染时设 FAIR_JS_RENDER=1；手动“补岗位”贴链接时始终会尝试无头渲染。
JS_RENDER = os.environ.get("FAIR_JS_RENDER", "0") == "1"


def browser_exe():
    global BROWSER
    if BROWSER is not None:
        return BROWSER
    for path in BROWSER_PATHS:
        if path and Path(path).exists():
            BROWSER = path
            return BROWSER
    BROWSER = ""
    return BROWSER


def fetch_js(url, timeout=35, budget=8000):
    """用本机 Edge/Chrome 无头模式渲染 JS 页面，返回渲染后的 HTML。

    两条路，优先第一条：

    1. **常驻浏览器 + 多标签页**（`cdp.py`）。一个 Edge 进程从进程生命期开头
       活到结尾，每次调用只开一个标签页（几十 ms），并发靠 `_TAB_SEM` 卡在
       `FAIR_JS_TABS`（默认 6）。这是 2026-11-16 的改动：旧做法每次调用都
       `spawn msedge --dump-dom`，冷启动 1.5~3s 全白付，还只能 2 并发。

    2. **一次性 `--dump-dom`**（`_fetch_js_locked`）。CDP 不可用、连续失败、
       或返回空时自动退回。这条路径的行为与改动前逐字一致，所以最坏情况等于没改。
    """
    exe = browser_exe()
    if not exe:
        return ""
    if JS_CDP and cdp.enabled():
        with _TAB_SEM:
            html = cdp.render(url, exe, timeout=timeout, budget=budget)
        if html is not None:
            return html
    with _BROWSER_SEM:
        return _fetch_js_locked(url, exe, timeout, budget)


def _fetch_js_locked(url, exe, timeout=35, budget=8000):
    profile = tempfile.mkdtemp(prefix="fair-js-")
    try:
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--disable-dev-shm-usage", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            "--user-data-dir=" + profile,
            "--virtual-time-budget=%d" % budget,
            "--dump-dom", url,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return ""
        if proc.returncode != 0 and not out:
            return ""
        return out.decode("utf-8", "ignore")
    except Exception:
        return ""
    finally:
        try:
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass


# Bing 对并发请求很敏感：并发一高就返回「汉字字典」这类降级结果
# （查"科伦药业 招聘"会返回「科（汉语汉字）_百度百科」）。串行 + 最小间隔，
# 并且识别出降级结果后立刻重试一次。
_BING_LOCK = threading.Lock()
_BING_LAST = [0.0]
_BING_GAP = 0.45
_DEGRADED = re.compile(r"汉语|汉典|字典|的意思|_百度百科|百科_")


def _degraded(hits):
    return bool(hits) and all(_DEGRADED.search((h.get("title") or "")) for h in hits)


def bing(q, n=5):
    out = []
    for attempt in (1, 2):
        with _BING_LOCK:
            gap = time.time() - _BING_LAST[0]
            if gap < _BING_GAP:
                time.sleep(_BING_GAP - gap)
            try:
                html = fetch("https://cn.bing.com/search?q=" + urllib.parse.quote(q))
            except Exception:
                html = ""
            _BING_LAST[0] = time.time()
        out = []
        for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.S):
            block = m.group(0)
            link = re.search(r'<h2>\s*<a[^>]+href="([^"]+)"', block)
            title = re.search(r"<h2>(.*?)</h2>", block, re.S)
            if link:
                out.append({"url": link.group(1),
                            "title": re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""})
            if len(out) >= n:
                break
        if out and not _degraded(out):
            return out
    return out


SKIP = ("zhihu.com", "weibo.com", "map.baidu", "baike.baidu.com", "qcc.com",
        "tianyancha", "douyin", "xiaohongshu")

# 招聘聚合站：可以用它们找到线索，但不能只凭品牌名就把岗位算到企业头上
AGG_HOSTS = ("yinhangzhaopin.com", "yingjiesheng.com", "hahazhao.com",
             "shushuqiuzhi.com", "liepin.com", "zhaopin.com", "51job.com",
             "boss.com", "lagou.com", "kanzhun.com", "zhihu.com", "baidu.com")


def is_agg(url):
    return any(h in (url or "") for h in AGG_HOSTS)


AUTH_PREFIXES = ("join.", "careers.", "career.", "hr.", "job.", "jobs.",
                 "zhaopin.", "recruit.", "campus.", "talent.")


def is_auth(url):
    """学校就业网、官方招聘站、北森 zhiye 等，优先于新闻/聚合站。"""
    host = urllib.parse.urlparse(url or "").netloc.lower()
    if not host:
        return False
    if host.endswith(".edu.cn") or "zhiye.com" in host:
        return True
    return any(host.startswith(p) for p in AUTH_PREFIXES)


def short_name(name):
    """全称太长，Bing 匹配不到。去掉括号、分公司、公司后缀取核心词。"""
    s = re.sub(r"[（(].*?[)）]", "", name).strip()
    # 「…有限公司上海分公司」要连城市一起剥掉，否则会剩下「…有限公司上海」这种非企业名。
    s = re.sub(r"(?:股份有限公司|有限责任公司|有限公司|集团)?(?:[^公司]{2,8})?(?:分公司|分行)$", "", s)
    for suf in ("股份有限公司", "有限责任公司", "有限公司", "集团有限公司",
                "股份公司", "集团", "公司"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s.strip()


REGION_PREFIX = ("北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
                 "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
                 "湖北", "湖南", "广东", "广西", "海南", "四川", "贵州", "云南",
                 "陕西", "甘肃", "青海", "宁夏", "新疆", "内蒙古", "西藏",
                 # 地级市前缀同样常见：「南京新东方…」不剥「南京」就搜不到
                 "南京", "苏州", "无锡", "常州", "南通", "徐州", "盐城", "扬州",
                 "镇江", "泰州", "杭州", "宁波", "温州", "嘉兴", "绍兴", "金华",
                 "深圳", "广州", "东莞", "佛山", "珠海", "中山", "武汉", "宜昌",
                 "成都", "绵阳", "西安", "青岛", "济南", "烟台", "潍坊", "合肥",
                 "芜湖", "长沙", "株洲", "郑州", "洛阳", "厦门", "福州", "泉州",
                 "大连", "沈阳", "长春", "哈尔滨", "昆明", "南昌", "贵阳", "太原",
                 "石家庄", "兰州", "乌鲁木齐", "呼和浩特", "海口", "南宁", "柳州")


INDUSTRY_TAIL = re.compile(
    r"(?:文化艺术|文化传播|文化|艺术|教育|科技|信息|技术|网络|集团|股份|控股|实业|"
    r"发展|智能|电子|生物|医药|药业|数据|软件|系统|服务|咨询|管理|贸易|商贸|"
    r"建设|工程|置业|地产|传媒|广告|医疗|健康|药房|连锁|餐饮|酒店|旅游|物流|"
    r"环保|新能源|材料|机械|电气|自动化|仪器|化工|食品|投资|资产)$")


def brand_names(name):
    """企业全称 -> 多个搜索名：短名、去地名、核心品牌、全称。
    例：腾讯科技（上海）有限公司 -> 腾讯科技 / 腾讯
        京东方科技集团股份有限公司 -> 京东方科技集团 / 京东方
        杭州银行股份有限公司上海分行 -> 杭州银行
        四川科伦药业股份有限公司 -> 四川科伦药业 / 科伦药业
        南京新东方文化艺术发展有限公司 -> 南京新东方文化艺术 / 新东方
    后两个很关键：「四川科伦药业」在 Bing 上召回不到，去掉「四川」就命中官网校招系统；
        「南京新东方文化艺术发展」召回不到，退到品牌词「新东方」才有招聘结果。
    """
    short = short_name(name)
    out = []

    def push(v):
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)

    push(short)
    # 先去地名前缀，再剥行业词：顺序反了会停在「南京新东方」而不是「新东方」
    base = short
    for r in REGION_PREFIX:
        if base.startswith(r) and len(base) - len(r) >= 3:
            base = base[len(r):]
            push(base)
            break
    core = base
    for _ in range(4):
        m = INDUSTRY_TAIL.search(core)
        if not m or len(core) - len(m.group(0)) < 3:
            break
        core = core[: m.start()]
    push(core)
    push(name)
    return out


JOB_HINT = ("招聘", "校招", "校园招聘", "岗位", "职位", "人才", "加入",
            "recruit", "zhaopin", "campus", "job", "career", "hr")


PAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "_pages")


def _page_cache_path(name):
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return os.path.join(PAGE_DIR, h + ".json")


def pages_for(name, use_cache=True):
    """多查询、多页面找招聘页（页面缓存 + 搜索并发）。

    为什么要缓存「最终选中的正文」：补主营、补漏、换规则重采，需要的都只是这段
    正文，而拿到它要付三笔钱——多次搜索、多次抓页、若干次无头渲染。缓存后这类
    二次任务从「重抓一遍」变成「读文件 + 一次模型调用」。
    TTL 默认 12 小时（会前数据一天内不变）；FAIR_PAGE_CACHE_TTL=0 可关闭。
    """
    ttl = int(os.environ.get("FAIR_PAGE_CACHE_TTL", "43200"))
    path = _page_cache_path(name)
    if use_cache and ttl > 0:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("name") == name and time.time() - float(d.get("ts") or 0) < ttl:
                return d.get("text") or "", d.get("url") or "未知"
        except Exception:
            pass
    text, url = _pages_for_uncached(name)
    if use_cache and ttl > 0 and text:
        try:
            os.makedirs(PAGE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": name, "text": text, "url": url,
                           "ts": time.time()}, f, ensure_ascii=False)
        except Exception:
            pass
    return text, url


def _pages_for_uncached(name):
    """多查询、多页面找招聘页；优先学校就业网/招聘站点，正文取招聘段落。"""
    variants = brand_names(name)
    # 搜索可以用品牌名，但页面必须能对上全称/短名，避免把“腾讯音乐”“徽商银行”当成这家
    strong = []
    for v in (name, short_name(name)):
        v = re.sub(r"\s+", "", v)
        if len(v) >= 4 and v not in strong:
            strong.append(v)

    queries = []
    for v in variants[:3]:
        for tail in ("校园招聘", "招聘"):
            q = '"%s" %s' % (v, tail)
            if q not in queries:
                queries.append(q)

    # 中文长名在 Bing 上经常零召回（返回字词解释），此时 zh_search 会自动补 360：
    # 360 的结果块自带 data-mdurl，能直接拿到智联/猎聘/BOSS 的真实地址。
    import zh_search

    keys = [name, short_name(name)] + variants
    seen_urls = set()
    results = []

    def _search(q):
        try:
            return zh_search.search(q, n=6, keys=keys, want=2)
        except Exception:
            return []

    # 最多 6 个 query 串行 = 6 次往返，是这条链路里第二贵的一步（仅次于无头渲染）。
    # ex.map 保序，候选集的合并顺序与串行完全相同——只省时间，不改结果。
    batches = []
    if queries:
        with ThreadPoolExecutor(max_workers=min(6, len(queries))) as ex:
            batches = list(ex.map(_search, queries))
    for found in batches:
        for r in found:
            url = r.get("url") or ""
            if not url or url in seen_urls:
                continue
            if any(s in url for s in SKIP):
                continue
            seen_urls.add(url)
            blob = (url + " " + (r.get("title") or "")).lower()
            r["_hint"] = sum(1 for k in JOB_HINT if k.lower() in blob or k in blob)
            # 岗位列表页通常带 post/jobs/position/list/search，优先于校招落地页
            r["_hint"] += sum(1 for k in ("post", "jobs", "job", "position", "list", "search")
                              if k in url.lower())
            results.append(r)

    # 招聘/职位/校招字样越多的结果越先试
    results.sort(key=lambda r: -r.get("_hint", 0))
    results = results[:12]

    general = []
    general_jobs = []
    js_tries = 0
    for r in results:
        url = r["url"]
        try:
            text = strip_html(fetch(url))
        except Exception:
            text = ""
        title = r.get("title", "")
        job_score = sum(1 for k in JOB_ACCEPT if k in text)
        # 普通 HTTP 拿不到正文时，用本机 Edge/Chrome 无头渲染 JS（每家最多试 3 个候选）
        # 招聘聚合站（智联/猎聘/BOSS）也走渲染：它们服务端能给一部分正文，
        # 剩下的岗位列表在 JS 里，不渲染就只能看到公司简介。
        url_hint = any(h in url.lower() for h in
                       ("job", "career", "zhaopin", "recruit", "hr", "campus", "join", "zhiye")) \
            or is_agg(url)
        if JS_RENDER and url_hint and (len(text) < 300 or job_score < 2) and js_tries < 3:
            js_tries += 1
            js_text = strip_html(fetch_js(url, timeout=18, budget=6000))
            if len(js_text) > len(text):
                text = js_text
                job_score = sum(1 for k in JOB_ACCEPT if k in text)
        if len(text) < 300:
            continue
        if not page_matches(text, title, url, strong, variants):
            continue
        if job_score >= 2 and is_auth(url):
            return enrich.focus(text, 4000), url
        # 没有明显岗位字段的页面先记下来；如果里面能抽出岗位标题，按“官方优先、标题数量”挑最好的
        preview, _ = extract_jobs(text, guess_city(text, name))
        if preview:
            general_jobs.append((len(preview), 1 if is_auth(url) else 0, text, url))
        else:
            general.append((text, url))

    if general_jobs:
        _, _, text, url = max(general_jobs, key=lambda x: (x[1], x[0]))
        return enrich.focus(text, 4000), url
    for text, url in general:
        if page_matches(text, "", url, strong, variants):
            return enrich.focus(text, 4000), url
    return "", "未知"


def page_matches(text, title, url, strong, variants):
    """强匹配：全称/短名；品牌匹配：只允许非招聘聚合站。

    招聘站的页面标题常只写品牌名（「华海药业招聘信息-2026年最新-智联招聘」），
    只认全称/短名会把整批招聘站页面拒掉。所以聚合站放宽到品牌词，
    但要求页面确实是招聘页（出现 招聘/职位/岗位/薪资），避免误认。
    """
    if any(sn in text or sn in title for sn in strong):
        return True
    hit = any(v in text or v in title for v in variants)
    if is_agg(url):
        return hit and any(k in (text[:2000] + " " + title)
                           for k in ("招聘", "职位", "岗位", "薪资"))
    return hit


def guess_nature(text):
    head = text[:3000]
    for label, keys in NATURE_MAP:
        if any(k in head for k in keys):
            return label
    return "未知"


def guess_city(text, name):
    head = text[:800] + name
    for c in CITIES:
        if c in head:
            return c
    return "未知"


def guess_industry(text):
    table = [("金融", ("银行", "证券", "基金", "保险", "金融")),
             ("IT", ("软件", "信息技术", "互联网", "数据", "算法", "计算机")),
             ("医药", ("医药", "生物", "制药", "医疗")),
             ("建筑", ("建筑", "工程", "施工", "设计院", "土木")),
             ("能源", ("电力", "能源", "电气", "新能源", "核电", "风电")),
             ("物流", ("物流", "运输", "快递", "供应链")),
             ("教育", ("大学", "学院", "教育", "学校")),
             ("制造", ("制造", "装备", "机械", "电子", "材料", "生产"))]
    head = text[:2500]
    best, score = "未知", 0
    for label, keys in table:
        s = sum(head.count(k) for k in keys)
        if s > score:
            best, score = label, s
    return best if score >= 2 else "未知"


SCOPE_ANCHORS = ("主营业务", "经营范围", "主营", "主要从事", "专注于", "致力于",
                 "专业从事", "业务涵盖", "核心业务")

# 只有「导航栏目」才会成串出现的词。一行里命中两个以上，这段就是导航条不是业务描述。
NAV_NOISE = ("产品中心", "联系我们", "网站地图", "关于我们", "加入我们", "人才招聘",
             "新闻中心", "客户服务", "首页", "登录", "注册", "English",
             "集团简介", "发展历程", "全球布局", "企业文化", "奖项荣誉", "解决方案",
             "投资者关系", "新闻动态", "研发智造", "研发成果", "专利标准", "可持续发展")

# 业务描述是用来说「干什么」的，必然带这些动词。导航条只有名词堆叠。
SCOPE_VERBS = ("从事", "生产", "研发", "制造", "销售", "提供", "服务", "设计",
               "加工", "开发", "经营", "集成", "承接", "生产销售", "研制")


def _scope_ok(seg):
    """这段像不像业务描述（而不是导航条 / 页脚）。

    实测出来的两个坑：官网首页的「集团简介 发展历程 全球布局 企业文化…」会命中
    「集团简介」锚点；招聘页的「产品中心 联系我们 网站地图」会被当成经营范围。
    这两类垃圾一旦进了主营，下游就会拿它判岗位方向，比留空危险得多。
    """
    # 8 字下限：「有色金属压延加工」按分号截断后就 8 个字，它足够判方向。
    # 更短的基本是栏目名，交给下面的 NAV_NOISE / 平均词长两道判定兜。
    if len(seg) < 8:
        return False
    if sum(1 for w in NAV_NOISE if w in seg) >= 2:
        return False
    if not any(v in seg for v in SCOPE_VERBS):
        return False
    toks = [t for t in seg.split() if t]
    if len(toks) >= 4 and sum(len(t) for t in toks) / float(len(toks)) < 4.0:
        return False
    return True


def guess_scope(text):
    """没走模型时，从页面正文里切一句主营业务。

    「锚点 + 截一句」之后再过 _scope_ok 判定；不合格就接着找下一个锚点，
    最后仍没有就返回「未知」。宁可空着让匹配端去问，也不拿导航条充数。
    """
    s = str(text or "")
    if not s:
        return "未知"
    for kw in SCOPE_ANCHORS:
        start = 0
        while True:
            i = s.find(kw, start)
            if i < 0:
                break
            start = i + len(kw)
            seg = s[i:i + 240]
            # 锚点后面通常紧跟冒号/破折号，从冒号后开始截
            m = re.match(r"[^：:—\-]{0,8}[：:—\-]\s*", seg)
            seg = seg[m.end():] if m else seg.lstrip("：:—- 　")
            cut = len(seg)
            for ch in ("。", "；", ";", "！", "\n"):
                j = seg.find(ch)
                if 0 <= j < cut:
                    cut = j
            seg = seg[:cut].strip()[:120]
            if _scope_ok(seg):
                return seg
    return "未知"


def extract_jobs(text, city):
    """从招聘页里抽岗位。只认原文里出现过的词，不编。"""
    found, seen = [], set()
    # 先按「岗位：xxx」这类标签抽
    for m in re.finditer(r"(?:岗位名称|招聘岗位|职位名称|应聘岗位)[:：]\s*([\u4e00-\u9fa5A-Za-z0-9（）()·/＋+\-]{2,24})",
                         text):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            found.append(t)
    # 再按常见职位词兜底：只留干净形态，脏的一律不要
    BAD = ("累计", "集团", "公司", "负责", "协助", "配合", "意向", "现有", "员工",
           "我们", "本公司", "以上", "岗位", "本次", "此次", "各类",
           "求职", "简历", "投递", "关注", "首页", "登录", "注册")
    SUFFIX = ("工程师", "专员", "经理", "主管", "助理", "顾问", "分析师", "设计师",
              "研究员", "技术员", "管培生", "实习生", "会计", "法务", "采购", "销售", "教师")
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,8}(?:" + "|".join(SUFFIX) + r"))", text):
        t = m.group(1).strip()
        if not (3 <= len(t) <= 14):
            continue
        if any(b in t for b in BAD) or t in seen:
            continue
        seen.add(t)
        found.append(t)
        if len(found) >= 12:
            break

    stage = "校招" if ("校园招聘" in text or "校招" in text or "应届" in text
                       or re.search(r"20\d{2}届", text)) else \
            ("社招" if "社会招聘" in text or "社招" in text else "未知")
    jobs = []
    for t in found[:8]:
        i = text.find(t)
        ctx = text[max(0, i - 60):i + 120]
        duty = next((w for w in ("负责", "从事", "参与", "开展") if w in ctx), "")
        jobs.append({
            "title": t,
            "requirements": "职责：%s；工作地点：%s；招聘阶段：%s；要求：%s" % (
                (duty + t) if duty else t, city, stage,
                ("学历专业见原文" if "学历" in ctx or "专业" in ctx else "未知")),
            "headcount": "未知",
            "urgency": "未知",
            "group": "未知",
            "salary": ("面议" if "薪" in ctx else "未知"),
        })
    return jobs, stage


def import_jobs(name, text, source="手动粘贴"):
    """手动补录：从粘贴的招聘页文本/链接正文里抽岗位，写回缓存。"""
    text = strip_html(text) if "<" in text else text
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    text = enrich.focus(text, 12000)
    old = CACHE.get(name, {})
    def known(v):
        return v not in (None, "", "未知")
    # 招聘页只负责岗位；城市/性质/行业沿用已有企业资料，避免被页面里的城市列表带偏
    city = old.get("city") if known(old.get("city")) else guess_city(text, name)
    jobs, stage = extract_jobs(text, city)
    if source and any(k in source.lower() for k in ("social", "professionals", "shezhao", "社招")):
        stage = "社招"
    looks_like_job_page = sum(1 for k in JOB_ACCEPT if k in text) >= 2
    if stage == "校招":
        source_kind = "校招公告"
    elif stage == "社招":
        source_kind = "社招信息"
    elif jobs and looks_like_job_page:
        source_kind = "社招信息"
    else:
        source_kind = "未知"
        jobs = []

    m = re.search(r"[^。；\n]{10,60}(?:校园招聘|招聘|校招)[^。；\n]{0,40}", text)
    evidence = m.group(0).strip()[:120] if m else "未知"
    if jobs:
        title = jobs[0].get("title") or ""
        i = text.find(title) if title else -1
        if i >= 0:
            evidence = text[max(0, i - 60):i + 160].strip()[:160]
    nature = old.get("nature") if known(old.get("nature")) else guess_nature(text)
    industry = old.get("industry") if known(old.get("industry")) else guess_industry(text)

    return {
        "id": old.get("id") or ("web-" + str(abs(hash(name)) % 10 ** 8)),
        "name": name,
        "nature": nature,
        "city": city,
        "industry": industry,
        "jobs": jobs,
        "jobSource": source_kind,
        "jobEvidence": evidence,
        "source": source or "手动粘贴",
        "date": time.strftime("%Y-%m-%d"),
        "note": "手动补录",
    }


def scope_from_about(name):
    """招聘页通常只写岗位、不写业务介绍，主营得单独去官网「关于我们」找一次。

    搜索用**纯企业名**（不加「公司简介」「主营业务」这类修饰词）：实测给中文长名
    加修饰词后，Bing 会退化成按地名召回（搜「"宁波金田铜业" 公司简介」返回的是
    「宁波市」词条、市政府、地图），而只搜 `"宁波金田铜业"` 反而能直接命中官网。
    抽不到就返回空字符串——宁可空着让匹配端去问，也不能拿导航条当主营。
    """
    variants = brand_names(name)
    strong = [re.sub(r"\s+", "", v) for v in (name, short_name(name)) if len(v) >= 4]
    cands, seen = [], set()
    for qn in [short_name(name)] + variants[:2]:
        qn = (qn or "").strip()
        if not qn:
            continue
        for r in enrich.bing('"%s"' % qn, 6):
            url = r.get("url") or ""
            if not url or url in seen or any(k in url for k in SKIP):
                continue
            seen.add(url)
            cands.append(r)
    for r in cands[:6]:
        url = r["url"]
        try:
            text = strip_html(fetch(url))
        except Exception:
            continue
        if len(text) < 200:
            continue
        if not page_matches(text, r.get("title", ""), url, strong, variants):
            continue
        got = guess_scope(text)
        if got != "未知":
            return got
    return ""


def build(name):
    text, source = pages_for(name)
    if not text:
        return {"id": "web-" + str(abs(hash(name)) % 10 ** 8), "name": name,
                "nature": "未知", "city": "未知", "industry": "未知",
                "jobs": [], "jobSource": "未知", "jobEvidence": "未知",
                "source": "未知", "date": "未知", "note": "没抓到页面"}

    # 先走模型；失败或证据对不上就退回规则。免费模型偶尔读超时，
    # 立即重试一次（不 sleep，避免拖慢并行队列），仍失败才退规则。
    if MODEL_ON:
        for attempt in (1, 2):
            try:
                raw = enrich.call_model(name, text, MODEL_ON["key"], MODEL_ON["base"], MODEL_ON["model"])
                entry = enrich.build_entry(name, raw, text, source)
                entry["id"] = "web-" + str(abs(hash(name)) % 10 ** 8)
                entry.pop("text", None)
                entry["note"] = "大模型抽取 · " + MODEL_ON["model"]
                if str(entry.get("biz_scope") or "").strip() in ("", "未知"):
                    # 抓到的多半是招聘页，里面没有业务介绍；再去关于页找一次
                    _s = scope_from_about(name)
                    if _s:
                        entry["biz_scope"] = _s
                return entry
            except Exception as exc:
                sys.stderr.write("  model fail %s(%d/2): %s\n" % (name[:16], attempt, str(exc)[:60]))

    city = guess_city(text, name)
    jobs, stage = extract_jobs(text, city)
    looks_like_job_page = sum(1 for k in JOB_ACCEPT if k in text) >= 2
    if stage == "校招":
        source_kind = "校招公告"
    elif stage == "社招":
        source_kind = "社招信息"
    elif jobs and looks_like_job_page:
        # 页面有真实岗位，只是没写“校招/社招”：保留岗位，别因为标签缺失丢掉
        source_kind = "社招信息"
    else:
        source_kind = "未知"
        jobs = []

    evidence = "未知"
    if source_kind != "未知":
        if jobs:
            title = jobs[0].get("title") or ""
            i = text.find(title) if title else -1
            if i >= 0:
                evidence = text[max(0, i - 60):i + 160].strip()[:160]
        if evidence == "未知":
            m = re.search(r"[^。；\n]{10,60}(?:校园招聘|招聘|校招)[^。；\n]{0,40}", text)
            if m:
                evidence = m.group(0).strip()[:120]

    # 规则版也要给主营：模型不可用时这批企业全靠它判岗位方向
    scope = guess_scope(text)
    if scope == "未知":
        scope = scope_from_about(name) or "未知"
    # 与模型路径一致：岗位来自这一页，就把这一页的地址贴回去；抓不到就留空，不编。
    source_url = str(source or "").strip()
    if not source_url.startswith("http"):
        source_url = ""
    for _job in jobs:
        _job["source_url"] = source_url
    return {"id": "web-" + str(abs(hash(name)) % 10 ** 8), "name": name,
            "nature": guess_nature(text), "city": city,
            "industry": guess_industry(text), "biz_scope": scope, "jobs": jobs,
            "jobSource": source_kind, "jobEvidence": evidence,
            "source": source, "date": time.strftime("%Y-%m-%d"),
            "note": "规则抽取，未用大模型" +
                    ("（招聘页未标注校招/社招）" if stage == "未知" and jobs else "")}


# 表头词：整格等于这些就一定是表头，不是企业名（「所属行业」曾因含「行」被收进名单）
_HEADER_CELLS = {
    "序号", "编号", "企业名称", "公司名称", "单位名称", "企业", "单位", "公司名",
    "所属行业", "行业", "单位性质", "性质", "展位号", "展位", "招聘岗位数", "岗位数",
    "岗位方向", "岗位", "备注", "说明", "填写说明", "地址", "联系人", "联系电话", "电话",
    "人数", "专业", "学历", "要求", "序号（可不填）",
}

# 单字触发词以前是「行 / 局 / 社 / 厂」直接 in 判断，噪声极大：
# 「所属行业」「人事行政、财务」都因一个「行」字被当成企业名，检索时白跑一次。
# 现在这几个只认成词形态（银行 / 财政局 / 出版社 / 印刷厂）。
_COMPANY_RE = re.compile(
    r"公司|集团|银行|支行|分行|营业部|研究院|研究所|设计院|科学院|大学|学院|医院|"
    r"中心|事务所|事业单位|出版社|报社|杂志社|合作社|"
    r"(?:财政|税务|市场监督|生态环境|人力资源|统计|气象|地震|邮政|铁路|海关|药监|烟草)局|"
    r"(?:机械|印刷|纺织|食品|化工|电子|建材|造纸|钢铁|冶炼|加工|制造)厂")


def looks_like_company(v):
    v = str(v or "").strip()
    if not (4 <= len(v) <= 40):
        return False
    if v.isdigit() or v.startswith(("#", "-", "=")):
        return False
    if v in _HEADER_CELLS:            # 表头整格先挡掉，别再依赖「含某个字」的巧合
        return False
    if any(bad in v for bad in ("序号", "岗位", "专业", "人数", "展位", "备注", "要求",
                                "所属行业", "单位性质")):
        return False
    return bool(_COMPANY_RE.search(v))


def _header_col(row):
    """表头行里哪一格是「企业名称」列：返回列下标，认不出返回 None。"""
    for i, cell in enumerate(row or ()):
        s = str(cell or "").strip()
        if not s:
            continue
        if re.search(r"企业名称|公司名称|单位名称|企业全称|公司全称|^企业$|^单位$|^公司$", s):
            return i
    return None


def _skip_cols(row):
    """表头里这些列不是企业名，整列跳过（备注里常有「公司统一安排面试」这类字样）。"""
    out = set()
    for i, cell in enumerate(row or ()):
        s = str(cell or "").strip()
        if re.search(r"备注|说明|岗位|专业|人数|展位|行业|性质|地址|电话|联系|学历|要求|序号", s):
            out.add(i)
    return out


def names_from_xlsx(data):
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        head = None
        for _ in range(5):                 # 表头一般在前 5 行
            r = next(rows, None)
            if r is None:
                break
            if any(str(c or "").strip() for c in r):
                head = r
                if _header_col(r) is not None:
                    break
        col = _header_col(head) if head else None
        skip = _skip_cols(head) if head else set()
        if col is not None:
            # 认出了「企业名称」列：只收这一列，其余列一律不看
            for r in rows:
                if r and col < len(r) and r[col] is not None:
                    v = str(r[col]).strip().replace("\u3000", " ")
                    if looks_like_company(v):
                        out.append(v)
            continue
        # 认不出表头：退回全表扫描，但跳过明显的非名称列
        for r in (([head] if head else []) + list(rows)):
            for i, cell in enumerate(r or ()):
                if i in skip or cell is None:
                    continue
                v = str(cell).strip().replace("\u3000", " ")
                if looks_like_company(v):
                    out.append(v)
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def names_from_plain(data):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    out, uniq = [], []
    for line in re.split(r"[\r\n、,，;；\t]+", text):
        for cell in line.split(","):
            v = re.sub(r'^[\s"\'0-9.、)）]+', "", cell).strip().strip('"')
            if looks_like_company(v):
                out.append(v)
    seen = set()
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(DEMO), **kw)

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            return self._json(200, {"ok": True})
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/filter"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})
            return self._json(200, api_filter(payload))

        if self.path.startswith("/api/jobs/import"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})
            name = str(payload.get("name") or "").strip()
            url = str(payload.get("url") or "").strip()
            text = str(payload.get("text") or "").strip()
            if not name:
                return self._json(400, {"ok": False, "error": "缺企业名"})
            if not text and url:
                try:
                    text = fetch(url, timeout=20)
                except Exception:
                    text = ""
                plain = strip_html(text) if text else ""
                if len(plain) < 300 or sum(1 for k in JOB_ACCEPT if k in plain) < 2:
                    # JS 招聘页：普通 HTTP 只有壳，改用本机 Edge/Chrome 渲染
                    js = fetch_js(url, timeout=30, budget=8000)
                    if js:
                        text = js
                if not text:
                    return self._json(200, {"ok": False,
                                            "error": "页面抓取失败，普通 HTTP 和无头浏览器都没拿到内容"})
            if not text:
                return self._json(200, {"ok": False, "error": "没有可抽取的文本"})
            item = import_jobs(name, text, url or "手动粘贴")
            if not item:
                return self._json(200, {"ok": False, "error": "文本为空"})
            if not item["jobs"]:
                return self._json(200, {"ok": False, "company": item,
                                        "error": "没从这段内容里抽出岗位，请贴岗位列表部分"})
            CACHE[name] = item
            save_cache()
            sys.stderr.write("  manual import %-20s jobs=%d\n"
                             % (name[:18], len(item["jobs"])))
            return self._json(200, {"ok": True, "company": item,
                                    "jobs": len(item["jobs"])})

        if self.path.startswith("/api/names"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length)
                fname = (self.headers.get("X-Filename") or "").lower()
            except Exception:
                return self._json(400, {"ok": False, "error": "bad body"})
            return self._json(200, api_names(data, fname))

        if not self.path.startswith("/api/lookup"):
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        return self._json(200, api_lookup(payload))


# ---------------------------------------------------------------------------
# 纯函数版接口：与下方 Handler.do_POST 的同名分支逻辑一致，作为单一来源，
# 供 fair_server（同源报告服务）直接复用，避免两套服务器各写一遍。
# ---------------------------------------------------------------------------
def api_filter(payload):
    """初筛：按专业/方向挑对口企业。返回 matched/skipped/interpretation 等。"""
    names = [str(n).strip() for n in (payload.get("names") or []) if str(n).strip()]
    if not names:
        return {"ok": True, "matched": [], "skipped": 0, "skipped_names": [],
                "interpretation": "", "industries": [], "roles": []}
    try:
        out = prefilter_llm(names, payload.get("direction"), payload.get("major"))
    except Exception as exc:
        sys.stderr.write("  prefilter error: %s\n" % str(exc)[:100])
        hit, skipped, keys = filter_names(names, payload.get("direction"), payload.get("major"))
        out = {"picked": hit, "interpretation": "模型调用失败，按关键词初筛",
               "industries": [], "roles": [], "skipped": skipped, "engine": "rules"}
    picked = set(out["picked"])
    skipped_names = [name for name in names if name not in picked]
    return {"ok": True, "matched": out["picked"], "skipped": len(skipped_names),
            "skipped_names": skipped_names, "interpretation": out.get("interpretation", ""),
            "industries": out.get("industries", []), "roles": out.get("roles", []),
            "engine": out.get("engine", "llm")}


def api_lookup(payload):
    """补全企业画像：先查岗位库，再离线库，最后联网补。返回 companies/missed。"""
    names = [str(n).strip() for n in (payload.get("names") or []) if str(n).strip()]
    if not names:
        return {"ok": True, "companies": [], "missed": []}
    try:
        load_offline_companies()
    except Exception:
        pass
    store = job_store.get_store()
    out, missed, hit_cache = [], [], 0
    todo = []
    # 库里有企业记录但没岗位的：不算命中，留作联网补时的元数据兜底。
    db_partial = {}
    for n in names:
        try:
            db_item = store.lookup(n)
        except Exception as exc:
            sys.stderr.write("  job_store.lookup fail %s: %s\n" % (n[:16], str(exc)[:80]))
            db_item = None
        if db_item is not None:
            item = dict(db_item)
            item.setdefault("name", n)
            item.setdefault("note", "数据库命中")
            if item.get("jobs"):
                hit_cache += 1
                out.append(item)
                continue
            # 「先查库，没找到再去联网补」：库里有企业档案但岗位为空 = 岗位没找到，
            # 必须继续走联网补全，不能在这里直接短路成 missed。
            # 旧逻辑在此处 continue，导致库里 0 岗的企业永远搜不到岗位。
            db_partial[n] = item
            todo.append(n)
            continue
        if n in CACHE:
            item = CACHE[n]
            hit_cache += 1
            (out if item["jobs"] else missed).append(item)
        elif n in LIB and LIB[n]["jobs"]:
            item = dict(LIB[n])
            item["note"] = "企业端离线数据"
            CACHE[n] = item
            hit_cache += 1
            out.append(item)
        else:
            todo.append(n)
    if todo:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def work(n):
            try:
                item = build(n)
            except Exception as exc:
                item = {"id": "web-x", "name": n, "nature": "未知", "city": "未知",
                        "industry": "未知", "jobs": [], "jobSource": "未知",
                        "jobEvidence": "未知", "source": "未知", "date": "未知",
                        "note": str(exc)[:80]}
            base = LIB.get(n) or db_partial.get(n)
            if base:
                # 主营只补不抹：这次没从页面找到业务介绍，不能把库里已有那句冲掉
                if str(item.get("biz_scope") or "").strip() in ("", "未知") and base.get("biz_scope"):
                    item["biz_scope"] = base["biz_scope"]
            if base and not item.get("jobs"):
                for key in ("nature", "city", "industry", "source", "date",
                            "biz_scope"):
                    if item.get(key) in (None, "", "未知") and base.get(key) not in (None, "", "未知"):
                        item[key] = base[key]
                if item.get("note") in (None, "", "未知"):
                    item["note"] = "企业端离线资料兜底"
            CACHE[n] = item
            try:
                store.save(n, item)
            except Exception as exc:
                sys.stderr.write("  job_store.save fail %s: %s\n" % (n[:16], str(exc)[:80]))
            return item

        workers = min(4, max(1, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, n): n for n in todo}
            for fut in as_completed(futures):
                item = fut.result()
                (out if item["jobs"] else missed).append(item)
                sys.stderr.write("  lookup %-24s -> %s jobs=%d\n"
                                 % (item["name"][:22], item["city"], len(item["jobs"])))
        try:
            save_cache()
        except Exception:
            pass
    return {"ok": True, "companies": out, "missed": missed, "cached": hit_cache}


def api_names(data, fname):
    """从上传的 Excel/CSV/TXT 解析企业名单。"""
    fname = str(fname or "").lower()      # 浏览器传的是原始文件名，.XLSX 大写也要认
    try:
        if fname.endswith((".xlsx", ".xlsm")):
            names = names_from_xlsx(data)
        elif fname.endswith(".xls"):
            return {"ok": False, "names": [], "error": "旧版 .xls 读不了，请另存为 .xlsx 或 .csv"}
        else:
            names = names_from_plain(data)
        return {"ok": True, "names": names, "count": len(names)}
    except Exception as exc:
        return {"ok": False, "names": [], "error": str(exc)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    cache_total = load_cache()
    if cache_total:
        cache_jobs = sum(1 for entry in CACHE.values() if entry.get("jobs"))
        print("岗位缓存：%d 家（%d 家有岗位）" % (cache_total, cache_jobs))
    if setup_model():
        print("模型：%s（%s）" % (MODEL_ON["model"], MODEL_ON["base"]))
    else:
        print("模型：未配置 key，走规则抽取")
    lib_total = load_offline_companies(force=True)
    if lib_total:
        lib_jobs = sum(1 for entry in LIB.values() if entry["jobs"])
        print("企业库：%d 家（%d 家有岗位，搜索时直接离线命中）" % (lib_total, lib_jobs))
    print("服务已起：http://%s:%d/" % (args.host, args.port))
    print("  网页：/fair.html  /index.html  /start.html")
    print("  接口：POST /api/lookup")
    srv.serve_forever()


if __name__ == "__main__":
    main()
