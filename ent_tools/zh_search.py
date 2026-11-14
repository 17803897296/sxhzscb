# -*- coding: utf-8 -*-
"""中文企业招聘信息检索：Bing + 360 双引擎。

为什么需要：Bing 对中小企业/研究院所的中文长名经常「查无此企」，会返回
「国（汉语文字）_百度百科」这类字词解释，导致 8 秒空跑后判定 0 岗。
360 对同一查询能稳定召回智联/猎聘/BOSS 的招聘页（结果块里带 data-mdurl
真实地址，不需要解跳转链）。

对外只有一个函数：
    search(q, n=6) -> [{"url","title","engine"}]

只在 Bing 结果与企业名对不上时才补 360，避免每家都发两倍的搜索请求。
"""
from __future__ import annotations

import html as htmllib
import re
import threading
import time
import urllib.parse

import portal

# 360 有 IP 级频控：连着查十几次就返回「访问异常页面」。全局排队 + 最小间隔，
# 连续失败 5 次直接熔断（本进程不再用 360），避免采集时把时间耗在验证页上。
_LOCK = threading.Lock()
_LAST = [0.0]
_FAIL = [0]
_COOL = [0.0]
MIN_GAP = 2.5


def _clean(text):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def bing(q, n=6):
    out = []
    for r in portal.bing(q, n=n):
        title = _clean(r.get("title", ""))
        out.append({"url": r.get("url", ""), "title": title, "engine": "bing"})
    return out


def search360(q, n=6):
    """360 搜索结果页：h3 里的 data-mdurl 就是目标站真实地址。"""
    # 被封后不永久放弃：冷却 30 秒再来，长任务里能自然恢复。
    if time.time() < _COOL[0]:
        return []
    with _LOCK:
        gap = time.time() - _LAST[0]
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        try:
            page = portal.fetch("https://www.so.com/s?q=" + urllib.parse.quote(q), timeout=12)
        except Exception:
            _FAIL[0] += 1
            if _FAIL[0] >= 3:
                _COOL[0] = time.time() + 30
                _FAIL[0] = 0
            return []
        _LAST[0] = time.time()
    if not page or "访问异常" in page or "验证" in page[:3000]:
        _FAIL[0] += 1
        if _FAIL[0] >= 3:
            _COOL[0] = time.time() + 30
            _FAIL[0] = 0
        return []
    _FAIL[0] = 0
    out = []
    for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", page, re.S):
        blk = m.group(1)
        real = re.search(r'data-mdurl="([^"]+)"', blk)
        if not real:
            continue
        title = _clean(blk)
        if not title or len(title) < 4:
            continue
        out.append({"url": htmllib.unescape(real.group(1)), "title": title, "engine": "360"})
        if len(out) >= n:
            break
    return out


def relevant(item, keys):
    """结果是否真的指向这家企业：标题或地址里出现全称/短名/品牌词。"""
    blob = (item.get("title", "") + " " + item.get("url", "")).replace(" ", "")
    return any(k and k.replace(" ", "") in blob for k in keys)


def search(q, n=6, keys=None, want=3):
    """先 Bing；相关结果不足 want 条时用 360 补。keys 为空则不做相关度过滤。"""
    got = bing(q, n=n)
    if keys:
        got = [r for r in got if relevant(r, keys)] + [r for r in got if not relevant(r, keys)]
        strong = sum(1 for r in got if relevant(r, keys))
    else:
        strong = len(got)
    if strong < want:
        extra = search360(q, n=n)
        if keys:
            extra = [r for r in extra if relevant(r, keys)]
        seen = {r["url"] for r in got}
        for r in extra:
            if r["url"] not in seen:
                got.append(r)
                seen.add(r["url"])
    return got[:n]
