# -*- coding: utf-8 -*-
"""`webtext.py` 从三份拷贝里搬出来之后，行为是否逐字未变。

## 为什么专门写这个测试

搬家时**没有**重跑任何模型（fetch / strip_html 都不在 LLM 缓存链上），
所以不需要「等价性只是口头说的」——可以直接比。
基准是搬家前 collect.py / enrich_by_llm.py / portal.py 里那三份逐字相同的实现，
这里把它们原样重写一份当参照物。

## 最后两条为什么断言「是同一个函数对象」

`strip_html` 曾经在三个文件里各躺一份。谁在 portal.py 里再写一遍本地定义，
功能上完全没问题、CI 也不会红 —— 直到哪天要改「标签替换成空格」的规则，
改了 webtext 却漏了 portal，两路抓取就开始产出不同的正文。
所以这里用 `is` 钉住身份，而不是只比输出。
这条测试**只在有人重新制造重复时失败**，不会因为你写新功能而失败。
"""
import io
import unittest
from unittest.mock import patch

import webtext


OLD_STRIP_HTML = """\
def strip_html(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\\s+", " ", html).strip()
"""


def _old_strip_html():
    """把搬家前那份实现原样装出来当参照物（不依赖任何残留文件）。"""
    import re
    ns = {"re": re}
    exec(compile(OLD_STRIP_HTML, "<old>", "exec"), ns)   # noqa: S102 自己写的字符串
    return ns["strip_html"]


class TestStripHtmlEquivalence(unittest.TestCase):
    def test_same_as_before_on_real_pages(self):
        import pathlib
        old = _old_strip_html()
        root = pathlib.Path(__file__).resolve().parent.parent
        pages = [root.parent / "demo" / n
                 for n in ("fair.html", "test_users.html", "index.html")]
        seen = 0
        for p in pages:
            if not p.exists():
                continue
            html = p.read_text(encoding="utf-8", errors="ignore")
            self.assertEqual(old(html), webtext.strip_html(html), "差异在 %s" % p.name)
            seen += 1
        self.assertGreater(seen, 0, "一个真实页面都没读到，这条测试等于没跑")

    def test_same_on_edge_cases(self):
        old = _old_strip_html()
        cases = ["", "   ", "<p>hi</p>", "没有任何标签",
                 "<style>a{b:c}</style><script>var x=1</script><p>x&nbsp;y &amp; z</p>",
                 "<SCRIPT>x</SCRIPT><b>粗</b><i>斜</i>"]
        for s in cases:
            self.assertEqual(old(s), webtext.strip_html(s), repr(s))

    def test_script_style_removed_entities_decoded(self):
        out = webtext.strip_html(
            "<style>.a{color:red}</style><script>var a=1</script>"
            "<p>南京&nbsp;大学 &amp; 东南&nbsp;大学</p>")
        self.assertEqual(out, "南京 大学 & 东南 大学")


class TestFetch(unittest.TestCase):
    def test_sends_ua_and_reads_body(self):
        req = {}

        def fake(req_obj, timeout=None):
            req["headers"] = dict(req_obj.headers)
            req["timeout"] = timeout
            req["url"] = req_obj.full_url

            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            return R("正文".encode("utf-8"))

        with patch("urllib.request.urlopen", fake):
            self.assertEqual(webtext.fetch("http://x/", timeout=7), "正文")
        self.assertEqual(req["timeout"], 7)
        self.assertEqual(req["headers"].get("User-agent"), webtext.UA["User-Agent"])

    def test_gbk_fallback(self):
        """中文站常见 gbk，直接 decode('utf-8') 会抛，抛了整页就废了。"""

        def fake(req_obj, timeout=None):
            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            return R("南京大学".encode("gbk"))

        with patch("urllib.request.urlopen", fake):
            self.assertEqual(webtext.fetch("http://x/"), "南京大学")

    def test_sem_limits_concurrency(self):
        """portal 的 12 并发上限没在搬家时被抹掉。"""
        import threading
        sem = threading.Semaphore(1)
        entered = []

        def fake(req_obj, timeout=None):
            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            entered.append(sem._value)
            return R(b"ok")

        with patch("urllib.request.urlopen", fake):
            webtext.fetch("http://x/", sem=sem)
        self.assertEqual(entered, [0], "sem 没被持有就发了请求")


class TestNoMoreCopies(unittest.TestCase):
    """三份拷贝必须指向同一个函数对象 —— 再抄一遍就会红。"""

    def test_all_modules_share_one_implementation(self):
        import collect
        import enrich_by_llm
        import portal
        for mod in (collect, enrich_by_llm, portal):
            self.assertIs(mod.strip_html, webtext.strip_html, mod.__name__)
            self.assertIs(mod.UA, webtext.UA, mod.__name__)

    def test_portal_fetch_keeps_its_semaphore(self):
        import inspect
        import portal
        sig = inspect.signature(portal.fetch)
        self.assertEqual(str(sig), "(url, timeout=12)")


if __name__ == "__main__":
    unittest.main()
