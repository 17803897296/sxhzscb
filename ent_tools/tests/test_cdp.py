# -*- coding: utf-8 -*-
"""CDP 渲染层的不变量测试（不需要真的浏览器）。

这里只锁三件事，都是"改坏了会静默降低采集质量"的那种：

1. WebSocket 帧编码正确（自造的 RFC 6455 客户端，写错了根本连不上）
2. CDP 任何失败都返回 None，绝不抛异常给调用方
3. `portal.fetch_js` 在 CDP 返回 None 时必须退回旧的一次性 --dump-dom

第 3 条是这个改动的安全阀：只要它成立，CDP 出再大的问题，
最坏结果也只是退回到改动前的行为，不会让采集结果变空。
"""
import struct
import unittest
from unittest.mock import patch

import cdp
import portal


def decode_client_frame(raw):
    """按 RFC 6455 解析客户端帧，返回 (fin, opcode, payload)。"""
    b1, b2 = raw[0], raw[1]
    fin = b1 >> 7
    opcode = b1 & 0x0F
    masked = b2 >> 7
    n = b2 & 0x7F
    i = 2
    if n == 126:
        n = struct.unpack("!H", raw[i:i + 2])[0]
        i += 2
    elif n == 127:
        n = struct.unpack("!Q", raw[i:i + 8])[0]
        i += 8
    mask = raw[i:i + 4]
    i += 4
    body = raw[i:i + n]
    if masked:
        body = bytes(c ^ mask[k & 3] for k, c in enumerate(body))
    return fin, opcode, body


class FrameTests(unittest.TestCase):
    def _ws(self):
        ws = cdp.WS.__new__(cdp.WS)          # 不连 socket，只测编码
        return ws

    def test_short_payload_is_masked_and_verbatim(self):
        raw = self._ws()._frame(0x1, b'{"id":1}')
        fin, op, body = decode_client_frame(raw)
        self.assertEqual((fin, op), (1, 0x1))
        self.assertEqual(body, b'{"id":1}')

    def test_medium_payload_uses_16bit_length(self):
        payload = b"x" * 300
        raw = self._ws()._frame(0x1, payload)
        self.assertEqual(raw[1] & 0x7F, 126)
        _, _, body = decode_client_frame(raw)
        self.assertEqual(body, payload)

    def test_large_payload_uses_64bit_length(self):
        payload = b"y" * 70000                  # 大页面 outerHTML 会走到这条
        raw = self._ws()._frame(0x1, payload)
        self.assertEqual(raw[1] & 0x7F, 127)
        _, _, body = decode_client_frame(raw)
        self.assertEqual(body, payload)

    def test_mask_is_random_per_frame(self):
        ws = self._ws()
        a = ws._frame(0x1, b"same")
        b = ws._frame(0x1, b"same")
        self.assertNotEqual(a, b, "掩码必须每次随机，否则被缓存/CDN 拦")
        self.assertEqual(decode_client_frame(a)[2], decode_client_frame(b)[2])


class RenderFailureTests(unittest.TestCase):
    def setUp(self):
        cdp._STATE.update({"calls": 0, "ok": 0, "fail": 0,
                           "consecutive_fail": 0, "disabled": False})

    tearDown = setUp

    def test_non_http_url_returns_none_without_touching_browser(self):
        with patch.object(cdp, "_browser") as mk:
            self.assertIsNone(cdp.render("file:///etc/passwd", "x"))
            mk.assert_not_called()

    def test_empty_url_returns_none(self):
        self.assertIsNone(cdp.render("", "x"))

    def test_browser_start_failure_returns_none_and_disables(self):
        class Dead:
            def ensure(self):
                return False

        with patch.object(cdp, "_browser", return_value=Dead()):
            for _ in range(cdp._DEAD_AFTER):
                self.assertIsNone(cdp.render("https://a.example/", "x"))
        self.assertFalse(cdp.enabled(), "连续失败到达阈值后应停用，不再白试")

    def test_tab_failure_does_not_raise(self):
        class Bad:
            def ensure(self):
                return True

            def new_tab(self, timeout=10):
                raise RuntimeError("boom")

            def close_tab(self, tab):
                pass

        with patch.object(cdp, "_browser", return_value=Bad()):
            self.assertIsNone(cdp.render("https://a.example/", "x"))


class FetchJsFallbackTests(unittest.TestCase):
    def setUp(self):
        cdp._STATE.update({"calls": 0, "ok": 0, "fail": 0,
                           "consecutive_fail": 0, "disabled": False})

    tearDown = setUp

    def test_falls_back_to_oneshot_when_cdp_returns_none(self):
        with patch.object(portal, "browser_exe", return_value="fake.exe"), \
             patch.object(cdp, "render", return_value=None), \
             patch.object(portal, "_fetch_js_locked", return_value="<html>旧</html>") as old:
            self.assertEqual(portal.fetch_js("https://a.example/"), "<html>旧</html>")
            old.assert_called_once()

    def test_uses_cdp_result_when_available(self):
        with patch.object(portal, "browser_exe", return_value="fake.exe"), \
             patch.object(cdp, "render", return_value="<html>新</html>"), \
             patch.object(portal, "_fetch_js_locked") as old:
            self.assertEqual(portal.fetch_js("https://a.example/"), "<html>新</html>")
            old.assert_not_called()

    def test_switch_off_goes_straight_to_oneshot(self):
        with patch.object(portal, "browser_exe", return_value="fake.exe"), \
             patch.object(portal, "JS_CDP", False), \
             patch.object(cdp, "render") as new, \
             patch.object(portal, "_fetch_js_locked", return_value="<html>旧</html>"):
            self.assertEqual(portal.fetch_js("https://a.example/"), "<html>旧</html>")
            new.assert_not_called()

    def test_no_browser_returns_empty_string(self):
        with patch.object(portal, "browser_exe", return_value=""):
            self.assertEqual(portal.fetch_js("https://a.example/"), "")


if __name__ == "__main__":
    unittest.main()
