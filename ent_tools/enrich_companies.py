# -*- coding: utf-8 -*-
"""
用大模型（智谱 GLM-4-flash）逐家补全 118 家企业的客观属性，写回 careersafari.db。

为什么要做（用户拍板）：源数据 city 64% 脏/缺、nature 74% 缺、scale 100% 缺，
不补齐城市 7 类排名 / 企业类型排名 / 规模客观分档全是排空气。解开大模型限制后用模型
对企业知识的理解逐家补全；并以「公司名内地理信息优先」+ 城市白名单做防错。

补全字段（写进 item_json，保留 *_raw）：
  city  province  nature(央企/国企/民企/外企/事业单位/未知)
  industry(9类)  scale_tier(龙头特大/大型/中型/中小型/未知)  is_listed  confidence  note

用法：
  python -X utf8 enrich_companies.py --test       # 8 家脏数据验证
  python -X utf8 enrich_companies.py --all         # 全量 118（已补全跳过，--force 重跑）
  python -X utf8 enrich_companies.py --limit 20 --force
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "careersafari.db")
REPORT = os.path.join(DATA, "enrich_report.json")

sys.path.insert(0, os.path.join(HERE, "demo_v2"))
import rules as R  # 复用 CITY_ALL / CITY_PROVINCE

NATURE_SET = {"央企", "国企", "民企", "外企", "事业单位", "未知"}
INDUSTRY_SET = {"制造", "IT", "金融", "建筑", "教育", "医药", "能源", "消费", "未知"}
SCALE_SET = {"龙头特大", "大型", "中型", "中小型", "未知"}

# 在 rules.CITY_ALL 基础上扩充，覆盖更多地级市（用于「名称内城市」提取）
EXT_CITIES = ["德阳", "绍兴", "常州", "南通", "徐州", "温州", "嘉兴", "金华", "台州",
              "扬州", "泰州", "镇江", "盐城", "连云港", "洛阳", "南阳", "襄阳", "宜昌",
              "岳阳", "衡阳", "芜湖", "蚌埠", "马鞍山", "赣州", "九江", "潍坊", "临沂",
              "烟台", "泉州", "绵阳", "宜宾", "惠州", "中山", "江门", "汕头", "保定",
              "唐山", "廊坊", "嘉兴", "绍兴", "嘉兴", "株洲", "湘潭", "柳州", "桂林",
              "兰州", "西宁", "银川", "乌鲁木齐", "呼和浩特", "拉萨", "海口", "三亚",
              "吉林", "大庆", "鞍山", "本溪", "丹东", "锦州", "营口", "盘锦", "辽阳",
              "朝阳", "葫芦岛", "四平", "辽源", "通化", "白山", "松原", "白城", "鸡西",
              "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河",
              "绥化", "南通", "泰州", "宿迁", "淮安", "连云港", "盐城", "扬州", "镇江"]
CANON = set(R.CITY_ALL) | set(EXT_CITIES)
PROV = dict(R.CITY_PROVINCE)
PROV.update({"德阳": "四川", "绍兴": "浙江", "常州": "江苏", "南通": "江苏", "徐州": "江苏",
             "温州": "浙江", "嘉兴": "浙江", "金华": "浙江", "台州": "浙江", "扬州": "江苏",
             "泰州": "江苏", "镇江": "江苏", "盐城": "江苏", "连云港": "江苏", "洛阳": "河南",
             "南阳": "河南", "襄阳": "湖北", "宜昌": "湖北", "岳阳": "湖南", "衡阳": "湖南",
             "芜湖": "安徽", "蚌埠": "安徽", "马鞍山": "安徽", "赣州": "江西", "九江": "江西",
             "潍坊": "山东", "临沂": "山东", "烟台": "山东", "泉州": "福建", "绵阳": "四川",
             "宜宾": "四川", "惠州": "广东", "中山": "广东", "江门": "广东", "汕头": "广东",
             "保定": "河北", "唐山": "河北", "廊坊": "河北", "株洲": "湖南", "湘潭": "湖南",
             "柳州": "广西", "桂林": "广西", "三亚": "海南", "吉林": "吉林", "大庆": "黑龙江",
             "鞍山": "辽宁", "本溪": "辽宁", "丹东": "辽宁", "锦州": "辽宁", "营口": "辽宁",
             "盘锦": "辽宁", "辽阳": "辽宁", "朝阳": "辽宁", "葫芦岛": "辽宁", "四平": "吉林",
             "辽源": "吉林", "通化": "吉林", "白山": "吉林", "松原": "吉林", "白城": "吉林",
             "鸡西": "黑龙江", "鹤岗": "黑龙江", "双鸭山": "黑龙江", "伊春": "黑龙江",
             "佳木斯": "黑龙江", "七台河": "黑龙江", "牡丹江": "黑龙江", "黑河": "黑龙江",
             "绥化": "黑龙江", "宿迁": "江苏", "淮安": "江苏"})

PROMPT = """你是「中国企业资料补全」助手。根据公司名称，凭借你对中文企业（总部所在地、性质、行业、规模）的既有知识补全字段。

【重要】公司名称里通常带注册地/总部城市，请优先从名称判断城市，例如：
  "北京汽车国际发展有限公司" → 城市=北京；"杭州老板电器" → 杭州；"苏州东山精密" → 苏州；
  "亿嘉和科技股份有限公司" → 南京；"济南二机床集团" → 济南。
若名称无线索且你确实不确定，对应字段填"未知"、confidence 填低值，绝不编造。

公司名称：{name}
已知线索：城市线索={city_raw}；性质线索={nature_raw}；行业线索={industry_raw}

只输出一个 JSON，不要解释：
{{
  "city": "总部或主要办公城市，只写城市短名如 杭州/上海/北京，不写省和区",
  "province": "所属省份短名如 浙江",
  "nature": "央企 / 国企 / 民企 / 外企 / 事业单位 / 未知",
  "industry": "制造 / IT / 金融 / 建筑 / 教育 / 医药 / 能源 / 消费 / 未知",
  "scale_tier": "龙头特大(央企集团/世界500强/大型银行通信) / 大型(上市公司/知名民企集团) / 中型(规模以上民企) / 中小型(小微创业) / 未知",
  "is_listed": false,
  "confidence": 0.5,
  "note": "一句话依据"
}}"""


def load_cfg():
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return {"key": cfg.get("DEEPSEEK_API_KEY", ""),
            "base": (cfg.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
            "model": cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"}


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    s = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if s > 0:
        text = text[s:]
    return json.loads(text)


def call_model(name, city_raw, nature_raw, industry_raw, cfg, retries=4):
    prompt = PROMPT.format(name=name, city_raw=city_raw or "无",
                           nature_raw=nature_raw or "无", industry_raw=industry_raw or "无")
    payload = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.2}
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                cfg["base"] + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = json.loads(r.read().decode("utf-8"))
            return parse_json(raw["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = 2 ** (i + 1)
                print(f"  [429] {name} 退避 {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:
            last = e
            time.sleep(1.5)
    return {"error": str(last)}


def name_city(name):
    """从公司名里提取城市（名称内地理信息优先）。"""
    hit = None
    for c in sorted(CANON, key=len, reverse=True):
        if c in name:
            # 北京/上海等直辖市名也可能是省份前缀，城市优先
            hit = c
            break
    if hit:
        return hit, PROV.get(hit, "")
    return None, ""


def sanitize_city(rec, name):
    mc = (rec.get("city") or "").strip()
    mc = re.sub(r"(省|市|区|县|自治州|特别行政区|维吾尔|壮族|回族|自治区|新区|园区|大学城)$", "", mc)
    mc = mc.replace("浙江省", "").replace("江苏省", "").replace("河南省", "").replace("湖北省", "").replace("贵州省", "")
    mc = mc.strip()
    nc, np_ = name_city(name)
    # 名称里有城市且模型没给出，或模型给的和名称冲突 -> 用名称
    if nc and (mc in ("未知", "", None) or mc != nc):
        return nc, (np_ or rec.get("province") or "")
    if mc in CANON:
        return mc, (PROV.get(mc) or rec.get("province") or "")
    if mc:
        return mc, rec.get("province") or ""  # 非白名单但模型给的，保留（人工复核）
    return "未知", rec.get("province") or ""


def light_nature(rec, name):
    nat = rec.get("nature")
    if nat in NATURE_SET and nat != "未知":
        return nat
    if "银行" in name:
        return "国企"
    if "研究院" in name or "研究所" in name or "设计院" in name:
        return "事业单位"
    return "未知"


def normalize(rec, name):
    rec = {k: (v if k != "is_listed" else bool(v)) for k, v in rec.items()}
    if rec.get("industry") not in INDUSTRY_SET:
        rec["industry"] = "未知"
    if rec.get("scale_tier") not in SCALE_SET:
        rec["scale_tier"] = "未知"
    city, prov = sanitize_city(rec, name)
    rec["city"] = city
    rec["province"] = prov or rec.get("province") or ""
    rec["nature"] = light_nature(rec, name)
    return rec


def pick_sample(rows):
    dirty = [r for r in rows if not r[2] or "未知" in (r[2] or "") or re.search(r"市|省|股|证券|城市|智慧|金融市", r[2] or "")]
    clean = [r for r in rows if r not in dirty]
    out = dirty[:8]
    if len(out) < 8:
        out += clean[: 8 - len(out)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = load_cfg()
    if not cfg["key"]:
        print("缺少 DEEPSEEK_API_KEY，无法调用模型", file=sys.stderr)
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT rowid, name, item_json FROM company_jobs")
    rows = cur.fetchall()

    todo = []
    for rowid, name, ij in rows:
        try:
            d = json.loads(ij)
        except Exception:
            d = {}
        if d.get("enriched") and not args.force and not args.test:
            continue
        todo.append((rowid, name, d))

    if args.test:
        m = {r[0]: r for r in rows}
        todo = [(r[0], r[1], json.loads(r[2]) if r[2] else {}) for r in pick_sample(rows)]
        print(f"=== 验证样本 {len(todo)} 家 ===")
    elif args.limit:
        todo = todo[: args.limit]

    print(f"待补全：{len(todo)} 家；模型 {cfg['model']} @ {cfg['base']}")

    report_rows = []

    def work(item):
        rowid, name, d = item
        raw_city = d.get("city") or d.get("city_raw") or ""
        raw_nature = d.get("nature") or ""
        raw_industry = d.get("industry") or ""
        rec = call_model(name, raw_city, raw_nature, raw_industry, cfg)
        if "error" in rec:
            return item, None, rec
        rec = normalize(rec, name)
        rec["enriched"] = True
        rec["city_raw"] = raw_city
        rec["nature_raw"] = raw_nature
        rec["industry_raw"] = raw_industry
        merged = dict(d)
        merged.update({k: rec[k] for k in ("city", "province", "nature", "industry",
                                           "scale_tier", "is_listed", "confidence",
                                           "note", "enriched", "city_raw",
                                           "nature_raw", "industry_raw")})
        return item, merged, rec

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            item, merged, rec = fut.result()
            rowid, name, _ = item
            if merged is None:
                print(f"  [{i}/{len(todo)}] ✗ {name} 失败：{rec.get('error')}")
                report_rows.append({"name": name, "error": rec.get("error")})
                continue
            cur.execute("UPDATE company_jobs SET item_json=? WHERE rowid=?",
                        (json.dumps(merged, ensure_ascii=False), rowid))
            con.commit()
            print(f"  [{i}/{len(todo)}] ✓ {name} -> 城市={rec['city']} 性质={rec['nature']} "
                  f"行业={rec['industry']} 规模={rec['scale_tier']} 置信={rec.get('confidence')}")
            report_rows.append({"name": name, **rec})

    con.close()
    json.dump(report_rows, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n报告已写 {REPORT}")
    if not args.test:
        con = sqlite3.connect(DB)
        cur = con.cursor()
        cur.execute("SELECT item_json FROM company_jobs")
        kc = kn = ks = 0
        for (ij,) in cur.fetchall():
            d = json.loads(ij)
            if d.get("city") and d["city"] != "未知":
                kc += 1
            if d.get("nature") and d["nature"] != "未知":
                kn += 1
            if d.get("scale_tier") and d["scale_tier"] != "未知":
                ks += 1
        n = 118
        print(f"覆盖率：城市 {kc}/{n}  性质 {kn}/{n}  规模 {ks}/{n}")


if __name__ == "__main__":
    main()
