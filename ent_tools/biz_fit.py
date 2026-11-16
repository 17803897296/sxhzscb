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
    yes  主营业务能推出具体环节，且写得出该行业里真实存在的对口岗位名
    ask  主营沾边但方向不确定，或只有轮岗/储备类岗位能沾上
    no   说得出这家做什么，且在自己的业务链条上指认不出交叉环节

两处判定口径（2026-11-16 用户拍板）：
    - **no 不设行业白名单**。旧版只允许中小学/党政/金融/商管/跨境电商/课外辅导判 no，
      制造业一律默认沾边，于是「造纸、水泥、纺织、饲料」这类纯生产型主营也被套上
      「生产环节需要数据分析与算法」——21 家 yes 里 20 家是同一个句式，是不顾事实的
      八股文。现在把判断权交回模型：只要它能说清主营是什么、又指认不出数据/开发/算法
      环节，判 no 是好判断；证据不足（主营未查到、只有名称、跨不相干板块的集团）
      才判 ask。
    - **yes 必须给岗位级证据**。只写「需要数据分析与算法」不算数，要写出这个行业里
      真实存在、且**与学生意向同方向**的岗位名（意向是数据分析 → 「数据分析师、BI 工程师」；
      意向是机械设计 → 「机械设计工程师、结构设计工程师」）；写不出或方向对不上就降为 ask。
      这一条挡的是「拿环节词硬凑 yes」，落地判据见 _settle()。

纪律（与既有红线一致）：
    - no 只表示「这个展位这次别排队」，不是「人家不招人」；0 岗 ≠ 不招。
    - 判 no 要证据充足；证据不足一律 ask。错杀一家真招人的企业，比多问一句更糟。
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
from concurrent.futures import ThreadPoolExecutor, as_completed

import intent_semantics
import cache_guard
import llm
from llm import load_env as _load_env

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "biz_fit_cache.json")
TIMEOUT = 30
MAX_CACHE = 600

PROMPT = """你是双选会会前规划助手。下面是**一家**参会单位，以及一名应届生勾选的意向岗位。
请只回答一个问题：**按这家单位的主营业务，它可能容纳这名学生的方向吗？**

学生意向岗位：{intended}

【输出格式】严格一行，用竖线 | 分隔，不要 JSON、不要表头、不要解释、不要空行：
档位 | 可能对口的意向岗位 | 行业侧岗位名 | 一句话依据

档位只能是下面三个词之一：
- yes：主营业务与该方向有明确交叉，你能指出具体环节，**并且**写得出这个行业里真实存在的对口岗位名
- ask：主营沾边但方向不确定，或只有轮岗/储备/管培类岗位能沾上
- no：你说得出这家到底做什么，且在自己的业务链条上指认不出交叉环节

可能对口的意向岗位：从上面意向里挑**原词**，最多 2 个，用顿号分隔；没有就写「无」。

行业侧岗位名：这家单位所在行业里**真实存在**的、与上面意向同方向的岗位名，最多 2 个，用顿号分隔。
学生要数据分析，就写这个行业里做数据的岗（数据分析师、BI 工程师）；
学生要机械设计，就写这个行业的机械/结构岗（机械设计工程师、结构设计工程师）。
优先写招聘网站上实际在用的原名，**不要为了显得具体而拼造**——像
「配方研发数据分析师」「分离纯化工艺数据分析师」这种把业务词和岗位词粘起来的名，不算证据。
想不出任何真实存在的岗位名就写「无」——写「无」时档位只能是 ask。

【要求】
1. 第 2 栏只能是上面的意向**原词**，不许造新岗位名、不许写岗位族。
2. 依据必须落到这家单位的主营产品 / 生产环节上，要能指认到具体业务动作
   （「电解铝与铝深加工，槽控与设备点检每天产生工艺数据，需数据分析做参数寻优」、
   「汽车内外饰件，结构设计与模流分析需机械与 CAE 岗位」）。
   不许写「行业相关」「可能有用」「发展前景好」这类空话；
   **也不许把同一句式套到所有企业上**：像「主营 X，〈生产/工艺/质量〉需要数据分析与算法」
   这种模板，如果没有说清哪个环节、谁在做、为什么需要，等于没写。
   不许提模型、推断、数据来源；一句话，不超过 45 字。
3. 「主营」一栏为空、或只有单位名称时，按名称能看出的业务判断
   （「XX 铝业」= 铝加工，「XX 汽车部件」= 汽车零部件）。
   **名称已经能看出单位是干什么的，就直接判，不要因为「主营」空着就写「方向不确定」**：
   名称含 中学/小学/大学/学校/职业技术学院 → 教育教学；
   含 组织部/人民政府/管理局 → 党政机关；
   含 财务/银行/保险/证券/基金 → 金融；
   含 商业管理/广场/商管/物业 → 商业综合体运营；
   含 升学/辅导/培训/教育科技 → 课外辅导与升学服务。
   名称与行业标签对不上时，以名称和主营写出的实际业务为准，不要跟着错的标签走。
4. **判 no 没有行业禁区，但必须证据充足**：
   - 前提是你能一句话说清这家做什么（如「主营商业综合体招商与物业运营」「主营中小学学历教育」
     「主营跨境电商 B2C 零售」），并且在它的业务链条上指认不出任何与**上面意向方向**相关的环节。
   - 制造业同样适用：造纸、水泥、纺织、饲料、化肥这类纯生产型主营，如果确实说不出可指认的
     对口环节，判 no 是好判断，不要为了显得保守而写成 ask。
   - 反过来，证据不足就别判 no：主营写着「（未查到主营）」、只有单位名称、
     跨多个不相干板块的集团、连你自己都说不清它靠什么挣钱 —— 一律 ask。
   - 判 no 时依据写清主营是什么，不写「行业不相关」这种空话。
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
    return "%s|%s|bizfit-v2" % (
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


_NONE_WORDS = ("无", "—", "-", "－", "暂无", "空", "null", "none", "n/a")


def _ev_list(text):
    """行业侧岗位名一列：切分 + 去空 + 去「无」占位，最多 2 个。"""
    items = [x.strip() for x in re.split(r"[、,，;；/]", text or "")]
    out = [x for x in items
           if x and x.lower() not in _NONE_WORDS and x not in ("无",)]
    return list(dict.fromkeys(out))[:2]


def _parse(text, intended):
    """行格式优先：`档位 | 意向原词 | 行业侧岗位名 | 依据`。

    解析不出来返回 None（=判不了，退回 ask）。第三栏缺失或写「无」都当空，
    由 _settle() 决定 yes 是否降档 —— 模板少写一列本身不能算证据。
    """
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
        ev = _ev_list(parts[2]) if len(parts) > 2 else []
        basis = (parts[-1] if len(parts) > 2 else "").strip()[:80]
        return {"fit": head, "jobs": list(dict.fromkeys(jobs)),
                "evidence": ev, "basis": basis}
    return None


def _settle(res, intended):
    """yes 的硬门槛：第三栏必须写出**与学生意向同方向**的行业侧岗位名，否则降为 ask。

    两条都挡「拿环节词硬凑 yes」：
      ① 写不出岗位名（第三栏空/写「无」）——模型只是顺着主营造句；
      ② 写出的岗位名与意向不同方向（意向是机械，evidence 却写「数据分析师」；
         或意向是数据分析，evidence 只写「工艺工程师、质量工程师」）——
         这些岗确实存在，但不是学生要找的方向，按「岗位级证据」的口径不能算 yes。

    「同方向」用 intent_semantics.same_dir 判（同族 + 方向核互相包含，意向核不是
    「测试/开发/设计」这类泛动词），不用写死的字面词表，也不只比岗位族：
      · 第一版把「数据/算法/开发」写死 → 换成机械专业画像后 26 家全被误降 ask，
        证据写「机械设计工程师、结构工程师」明明是学生方向，却因不含「数据」被否掉；
      · 只比 job_family 又太松 → 「设备工程师」「封装设备工程师」与「机械设计工程师」
        同属 mech 族，制造业的设备维护岗于是全成了 yes，而学生要的是设计岗，不是修设备。
    same_dir 恰好把这两头都分开：结构设计/机械设计/工艺员算同方向，设备维护不算。
    """
    if res.get("fit") != "yes":
        return res
    ev = res.get("evidence") or []
    ints = [it for it in (intended or []) if it]
    if not ev:
        why = "未写出行业侧岗位名"
    elif not any(e == it or intent_semantics.same_dir(e, it)
                 for e in ev for it in ints):
        why = "写出的岗位名（%s）与你的意向不是同一方向" % "、".join(ev)
    else:
        return res
    out = dict(res)
    out["fit"] = "ask"
    out["note"] = why + "，由 yes 降为待问"
    out["basis"] = (out.get("basis") or "").rstrip("。") + "；" + why + "，方向待现场确认"
    return out


ASK = {"fit": "ask", "jobs": [], "evidence": [], "basis": "", "note": "", "via": "fallback"}
MAX_WORKERS = max(1, int(os.environ.get("FAIR_LLM_WORKERS") or 8))

# 岗位名里的通用构件。判断「这名字是不是把业务词和岗位词粘出来的」时，先把它们
# 剥掉，剩下的就是业务前缀。
_EV_GENERIC = ("工程师", "设备", "自动化", "控制", "电气", "仪表", "机械", "工艺",
               "维护", "维修", "管理", "技术", "运行", "调试", "设计", "研发", "工程")


def _plain_evidence(ev, intended):
    """把「业务词 + 岗位词」拼出来的岗位名退回学生自己的意向原词。

    为什么要有这一步：模型很听话地写「这个行业里真实存在的岗位名」，但它对
    「真实存在」的理解是「把两个词拼起来读得通」——
        电解铝设备工程师 / 钻探设备维护工程师 / 地质装备自动化工程师 / 锅炉控制工程师
    这些名字招聘网站上搜不到，是天山铝业、水文队、菱日锅炉的业务词加上学生的
    意向词拼的。旧版把它们原样写进报告，还加了个「（模型据此认为对口的是：X）」，
    标注了来源却仍然把模型的字摆成企业的话——用户的原话是「还是推断太多，不真实」。

    判据：剥掉通用构件后还剩 ≥2 字，说明带了业务前缀 → 是拼接名 → 退回同方向的
    意向原词；找不到同方向的意向（说明它本来就与学生的方向对不上，是 _settle 之外
    的漏网项）→ 直接丢掉，只留真正对得上的那条。
    """
    out = []
    for e in (ev or []):
        core = str(e or "")
        for w in _EV_GENERIC:
            core = core.replace(w, "")
        if len(core) < 2:
            if e not in out:
                out.append(e)
            continue
        alt = next((it for it in (intended or [])
                    if it and intent_semantics.same_dir(e, it)), "")
        if not alt:
            alt = next((it for it in (intended or [])
                        if it and e.endswith(it)), "")
        if alt and alt not in out:
            out.append(alt)
    return out[:2]



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
    text = llm.chat(PROMPT.format(intended="、".join(intended), row=row),
                    cfg, timeout=timeout)
    res = _parse(text, intended)
    if not res:
        return None
    res = _settle(res, intended)
    # 展示用的岗位名统一在这里收口：判定用原始 evidence（_settle 已用它判过 yes），
    # 写进报告的只留「不是拼出来的」那些，见 _plain_evidence。
    return {"fit": res["fit"], "jobs": res["jobs"],
            "evidence": _plain_evidence(res["evidence"], intended),
            "basis": res["basis"], "note": res.get("note", ""), "via": "llm"}


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
                         for k in ("fit", "jobs", "evidence", "basis", "note", "via")}
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
    """判一家。返回 {fit:'yes'|'ask'|'no', jobs:[意向原词], evidence:[行业侧岗位名],
    basis:str, note:str, via:'llm'|'fallback'}。

    失败/无 key 一律 ask —— 中性档，不替用户做「跳过这家」的决定。
    单条接口，实际走 judge_many（同一套缓存与并发逻辑，避免两份实现）。
    """
    one = {"name": name, "industry": industry, "nature": nature,
           "segment": segment, "scope": scope, "job_src": job_src,
           "titles": titles, "intended": intended}
    return judge_many([one], timeout=timeout).get(name) or dict(ASK)
