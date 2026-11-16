#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓网页用的三样公共零件：UA / fetch / strip_html。

## 为什么单独一个文件

这三样原来是三份拷贝，各躺在 collect.py、enrich_by_llm.py、portal.py 里：

    UA          148 字符，三份逐字相同
    strip_html  6 行，三份逐字相同
    fetch       13 行，只差默认超时（10 / 12）和 portal 那份额外拿了并发信号灯

拷第二份的时候没人管，到第三份就没人敢动了 —— 想换个 UA 得改三处，
漏一处就有一路抓取还带着旧 UA，而这件事不会报错，只会「某个来源抓得不好」。

## 搬过来的时候没有改行为

    · UA 逐字相同
    · strip_html 逐字相同
    · fetch 默认超时统一取 12 秒。**collect.py 原来用的是 10 秒，
      调用点显式传 timeout=10 保持原样** —— 网络超时是可以悄悄改变
      抓取成功率的东西，重构里顺手「统一一下」是最容易埋雷的做法。

## 为什么 fetch 要一个 sem 参数

portal.py 的抓取有并发上限 12（`_HTTP_SEM`），collect / enrich 没有。
合并时不能把这个上限抹掉：它挡的是「同一台机器十二个 socket 同时打出去」。
所以 sem 可选，谁原来有限制谁传进来 —— portal 里保留了一个三行的
`fetch()` 包装，把信号灯钉在自己的调用点上，对外行为一字未变。
"""
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def fetch(url, timeout=12, sem=None):
    """GET 一个 URL，返回解码后的文本。

    编码按 utf-8 → gbk → gb18030 依次试，全失败就 utf-8 + ignore。
    中文站点常见 gbk，直接 decode('utf-8') 会抛，抛了整页就废了。
    """
    req = urllib.request.Request(url, headers=UA)
    if sem is None:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    else:
        with sem:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def strip_html(html):
    """HTML → 一行纯文本。script/style 整块丢掉，标签换空格，空白折叠。"""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", html).strip()
