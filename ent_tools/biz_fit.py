# -*- coding: utf-8 -*-
"""匹配端 · 按**主营业务**判断这家单位与学生的方向是否交叉（大模型）。

为什么单独做这一层：
    旧版对「没判出对口岗位」的企业只拼模板句 ——

        "因为来源不保证齐全，且模型推断可能招%s" % 学生自己勾的前两个意向

    这句话里的 X 就是学生自己的意向，等于把学生的话写成企业的推断结论，
    而且完全没读企业主营。房建总包于是被写成「可能招产品工程师」，
    学校、财务公司、商业综合体也被写成「可能招机械设计工程师」。
    用户的原话是「这种形式上的有什么用」，这个模块就是替换它的。

三档而不是两档：
    yes  主营业务能推出具体环节（电解铝 → 设备维护；汽车内外饰 → 结构设计）
    ask  主营沾边但方向不确定，或只有轮岗/储备类岗位能沾上
    no   主营业务与学生方向明显不交叉（中小学、党政机关、财务公司、
         商业运营、跨境电商零售、课外辅导）

纪律（与既有红线一致）：
    - no 只表示「这个展位这次别排队」，不是「人家不招人」；0 岗 ≠ 不招。
    - 拿不准一律 ask，不判 no。错杀一家真招人的企业，比多问一句更糟。
    - 判定结果按 (企业字段 + 意向 + 提示词 + 代码指纹 + 模型) 缓存，逐家判
      （同批塞进不同行业的企业会互相污染，与 unknown_infer 同因，见其注释）。
    - 模型不可用 / 超时 / 格式异常 → ask（中性），绝不默认 no。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from intent_semantics import _load_env
import cache_guard

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "biz_fit_cache.json")
TIMEOUT = 30
MAX_CACHE = 600

PROMPT = """你是双选会会前规划助手。下面是**一家**参会单位，以及一名应届生勾选的意向岗位。
请只回答一个问题：**按这家单位的主营业务，它可能容纳这名学生的方向吗？**

学生意向岗位：{intended}

【输出格式】严格一行，用竖线 | 分隔，不要 JSON、不要表头、不要解释、不要空行：
档位 | 可能对口的意向岗位 | 一句话依据

档位只能是下面三个词之一：
- yes：主营业务与该方向有明确交叉，且你能指出具体环节
- ask：主营沾边但方向不确定，或只有轮岗/储备/管培类岗位能沾上
- no：主营业务与该方向明显不交叉

可能对口的意向岗位：从上面意向里挑**原词**，最多 2 个，用顿号分隔；没有就写「无」。

【要求】
1. 岗位名只能是上面的意向**原词**，不许造新岗位名、不许写岗位族。
2. 依据必须落到这家单位的主营产品 / 生产环节上，例如
   「电解铝与铝深加工，生产线需要大量设备维护与机械检修」、
   「汽车内外饰件，需要结构设计与工艺」。
   不许写「行业相关」「可能有用」「发展前景好」这类空话；
   不许提模型、推断、数据来源；一句话，不超过 40 字。
3. 「主营」一栏为空、或只有单位名称时，按名称能看出的业务判断
   （「XX 铝业」= 铝加工，「XX 汽车部件」= 汽车零部件）。
   **名称已经能看出单位是干什么的，就直接判，不要因为「主营」空着就写「方向不确定」**：
   名称含 中学/小学/大学/学校/职业技术学院 → 教育教学；
   含 组织部/人民政府/管理局 → 党政机关；
   含 财务/银行/保险/证券/基金 → 金融；
   含 商业管理/广场/商管/物业 → 商业综合体运营；
   含 升学/辅导/培训/教育科技 → 课外辅导。
   只有**名称本身也看不出业务**（如「XX 新区高新技术产业」「XX 实业集团」）时才判 ask。
   名称与行业对不上时（名称是「新材料」、行业标「IT」），以名称能看出的实际业务为准。
4. 判 no 要克制。只有主营明显在另一个领域才判 no：中小学与高校教学、
   党政机关、银行/财务/保险、商业综合体运营、跨境电商零售、课外辅导。
   判 no 时依据写清主营是什么（如「主营商业综合体招商与物业运营」）。
5. 单位性质（民企/国企/事业单位）本身不是理由。事业单位的院所、调查大队、
   设计院、检测中心可能大量招技术岗，要看主营，不要看性质。
6. 别把「没有岗位公告」当成「不招」——这里判的是业务是否交叉，不是有没有在招。

单位信息：
名称 | 行业 | 性质 | 主营范围/主要产品 | 岗位公告情况 | 已知岗位名
{row}
"""

FIT_WORDS = ("yes", "ask", "no")


def _model_tag():
    try:
        cfg = _load_env()
    except Exception:
        cfg = {}
    return "%s|%s|bizfit-v1" % (
        os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL") or "",
        os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "")


def _row_text(name, industry, nature, segment, scope, job_src, titles):
    """主营取 segment + biz_scope 两份。旧版只喂 biz_scope，
    于是 segment 有内容、biz_scope 为空的 4 家（曙光/泛成/美华/通达）
    被模型判成「主营范围未查到」，理由和页面展示的字段对不上。"""
    main = "；".join(x for x in (
        str(segment or "").strip(), str(scope or "").strip()) if x)
    main = re.sub(r"\s+", " ", main)[:260] or "（未查到主营）"
    src = str(job_src or "").strip() or "未知"
    js = "有岗位公告" if (src not in ("未知", "未知1", "未知2", "")) else "无岗位公告"
    ts = "、".join([t for t in (titles or []) if t][:10]) or "—"
    return "%s | %s | %s | %s | %s | %s" % (
        name, industry or "未知", nature or "未知", main, js, ts)


def _key(payload):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:20]


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {}
    cur = cache_guard.code_fp(name="biz_fit")
    maps = obj.get("maps") or {}
    kept = {k: v for k, v in maps.items() if (v.get("_code") == cur)}
    if len(kept) != len(maps):
        print("[biz_fit] 丢弃 %d 条旧版缓存（代码指纹不符）" % (len(maps) - len(kept)))
    return kept


def _save_cache(maps):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"maps": maps, "model": _model_tag(),
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "_cg": cache_guard.stamp("biz_fit", model=_model_tag())},
                      f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _call_raw(cfg, prompt, timeout):
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]


def _parse(text, intended):
    """行格式优先：`档位 | 岗位 | 依据`。解析不出来返回 None（=判不了，退回 ask）。"""
    for line in (text or "").replace("｜", "|").splitlines():
        line = line.strip().strip("`").strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        head = re.sub(r"[^a-zA-Z]", "", parts[0]).lower()
        if head not in FIT_WORDS:
            continue
        jobs = [x.strip() for x in re.split(r"[、,，;；/]", parts[1] if len(parts) > 1 else "")
                if x.strip() in intended][:2]
        basis = (parts[2] if len(parts) > 2 else "").strip()[:80]
        return {"fit": head, "jobs": list(dict.fromkeys(jobs)), "basis": basis}
    return None


ASK = {"fit": "ask", "jobs": [], "basis": "", "via": "fallback"}
MAX_WORKERS = max(1, int(os.environ.get("FAIR_LLM_WORKERS") or 8))


def _norm(one):
    """把一条输入归一成 (name, row, intended, key)；无意向返回 None。"""
    name = str(one.get("name") or "")
    intended = [str(x).strip() for x in (one.get("intended") or []) if str(x).strip()]
    if not intended:
        return None
    row = _row_text(name, one.get("industry", ""), one.get("nature", ""),
                    one.get("segment", ""), one.get("scope", ""),
                    one.get("job_src", ""), one.get("titles"))
    key = _key({"row": row, "intended": intended, "prompt": PROMPT,
                "model": _model_tag()})
    return (name, row, intended, key)


def _compute(row, intended, cfg, timeout):
    """纯计算，**不碰磁盘** —— 只有这样才能并发。"""
    text = _call_raw(cfg, PROMPT.format(intended="、".join(intended), row=row), timeout)
    res = _parse(text, intended)
    if not res:
        return None
    return {"fit": res["fit"], "jobs": res["jobs"], "basis": res["basis"], "via": "llm"}


def judge_many(ones, timeout=TIMEOUT):
    """并发判多家，返回 {企业名: 判定}。

    为什么要并发：31 家 × 0.7~1.0s 串行约 30s，全压在一次构建里。
    **只并发算、不并发写**：旧版 judge() 是「算一条立刻 _save_cache 一条」，
    多线程直接跑会同时对同一个 JSON 读-改-写，后写的覆盖先写的，结论静默丢失。
    所以这里先批量算完，再由主线程一次性落盘，并打印「新增条数」供对账。

    判定结果与串行版逐条一致：每个 key 只取决于该企业自身的输入，与同批
    其他企业无关（不像 unknown_infer 走批量提示词，会有串味问题）。
    """
    out = {}
    maps = _load_cache()
    todo = []
    for one in (ones or []):
        name = str(one.get("name") or "")
        n = _norm(one)
        if not n:
            out[name] = dict(ASK)
            continue
        _name, row, intended, key = n
        if key in maps:
            out[name] = {k: maps[key].get(k, ASK[k])
                         for k in ("fit", "jobs", "basis", "via")}
        else:
            todo.append((name, row, intended, key))
    if not todo:
        return out

    try:
        cfg = _load_env()
    except Exception:
        cfg = {}
    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY")):
        for name, _r, _i, _k in todo:
            out[name] = dict(ASK)
        return out

    t0 = time.time()
    added = 0
    workers = min(MAX_WORKERS, len(todo))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_compute, row, intended, cfg, timeout): (name, key)
                for name, row, intended, key in todo}
        for fut in as_completed(futs):
            name, key = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                print("[biz_fit] 判定失败（%s）：%s" % (name[:12], str(exc)[:60]))
                res = None
            if res is None:
                out[name] = dict(ASK)
                continue
            out[name] = res
            maps[key] = dict(res, _code=cache_guard.code_fp(name="biz_fit"))
            added += 1

    while len(maps) > MAX_CACHE:
        maps.pop(next(iter(maps)))
    _save_cache(maps)
    print("[biz_fit] %d 家待判 → %d 线程，新增缓存 %d 条，用时 %.1fs"
          % (len(todo), workers, added, time.time() - t0))
    return out


def judge(name, industry="", nature="", segment="", scope="", job_src="",
          titles=None, intended=None, timeout=TIMEOUT):
    """判一家。返回 {fit:'yes'|'ask'|'no', jobs:[意向原词], basis:str, via:'llm'|'fallback'}。

    失败/无 key 一律 ask —— 中性档，不替用户做「跳过这家」的决定。
    单条接口，实际走 judge_many（同一套缓存与并发逻辑，避免两份实现）。
    """
    one = {"name": name, "industry": industry, "nature": nature,
           "segment": segment, "scope": scope, "job_src": job_src,
           "titles": titles, "intended": intended}
    return judge_many([one], timeout=timeout).get(name) or dict(ASK)
