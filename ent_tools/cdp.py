# -*- coding: utf-8 -*-
"""极简 CDP 客户端：一个 Edge 进程复用多个标签页渲染 JS 页面。

**为什么要它**：旧做法每次 `fetch_js` 都 spawn 一个 `msedge --dump-dom`，
进程冷启动就要 1.5~3s，实测单次 5~18s，全量补采时这部分是绝对大头。

现在改成：
    一个常驻 Edge（`--remote-debugging-port=0`，端口写进 DevToolsActivePort）
    每次调用 → PUT /json/new 开一个标签页 → Page.navigate → 等正文稳定
              → Runtime.evaluate 取 outerHTML → 关标签页

标签页是廉价的（几十 ms），冷启动只在第一次付一次，所以并发不必再卡在 2。

**质量约定（最重要的一条）**：只要 CDP 任一步失败就返回 None，
调用方自动退回旧的 `--dump-dom` 一次性路径。最坏情况等于没改，不会更差。

等待策略也对齐旧行为：旧参数 `--virtual-time-budget=N` 本质是"等网络空闲
再跑 N 毫秒虚拟时间"。这里的等价物是：等 loadEventFired → 每 250ms 探一次
「readyState + outerHTML 长度」，连续两次不变才算稳定，下限 0.6s、上限 budget。
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

# 连续失败这么多次就判定 CDP 在本机不可用，之后直接走旧路径，不再白试
_DEAD_AFTER = 3
_POLL_GAP = 0.25
_MIN_SETTLE = 0.6

_STATE = {
    "calls": 0,
    "ok": 0,
    "fail": 0,
    "consecutive_fail": 0,
    "disabled": False,
    "start_ms": None,
}


def stats():
    """给 refresh_all / 诊断脚本看的运行时计数。"""
    out = dict(_STATE)
    b = _BROWSER
    out["browser_alive"] = bool(b and b.proc and b.proc.poll() is None)
    return out


def enabled():
    """CDP 是否还值得一试。连续失败到阈值后为 False，调用方直接走旧路径。"""
    return not _STATE["disabled"]


# ---------------------------------------------------------------- WebSocket

class WsError(OSError):
    pass


class WS:
    """够用就好的 RFC 6455 客户端：文本帧 + 续帧 + ping/pong + close。

    CDP 的消息是「一条 WS message = 一条 JSON」，所以不做流式分帧解析，
    只把分片拼完整再解码。
    """

    def __init__(self, url, timeout=10):
        u = urllib.parse.urlsplit(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, host, port, key)
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WsError("handshake closed")
            buf += chunk
            if len(buf) > 65536:
                raise WsError("handshake too large")
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        first = head.split(b"\r\n", 1)[0]
        if b" 101 " not in first:
            raise WsError("handshake failed: %r" % first[:120])

    def _exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise WsError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _frame(self, opcode, payload):
        hdr = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack("!H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack("!Q", n)
        mask = os.urandom(4)
        hdr += mask
        return bytes(hdr) + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))

    def send(self, text):
        self.sock.sendall(self._frame(0x1, text.encode("utf-8")))

    def recv(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(max(0.1, timeout))
        parts = []
        while True:
            b1, b2 = self._exact(2)
            fin = b1 & 0x80
            op = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            if b2 & 0x80:
                mask = self._exact(4)
                payload = bytes(c ^ mask[i & 3] for i, c in enumerate(self._exact(n)))
            else:
                payload = self._exact(n)
            if op == 0x8:
                raise WsError("close frame")
            if op == 0x9:
                self.sock.sendall(self._frame(0xA, payload))
                continue
            if op == 0xA:
                continue
            parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "ignore")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 标签页

class Tab:
    """一个标签页 == 一条 WS 连接。命令串行发，事件边收边记。"""

    def __init__(self, ws, target_id=""):
        self.ws = ws
        self.target_id = target_id
        self._seq = 0

    def _send(self, method, params=None):
        self._seq += 1
        self.ws.send(json.dumps({"id": self._seq, "method": method,
                                 "params": params or {}}))
        return self._seq

    def call(self, method, params=None, timeout=10):
        mid = self._send(method, params)
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(method)
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") != mid:
                continue                      # 事件，跳过
            if "error" in msg:
                err = msg["error"] or {}
                raise RuntimeError("%s -> %s" % (method, err.get("message", err)))
            return msg.get("result") or {}

    def navigate(self, url, timeout):
        """导航并等 loadEventFired；两个都可能先到，所以一起等。"""
        mid = self._send("Page.navigate", {"url": url})
        deadline = time.time() + timeout
        replied = loaded = False
        while not (replied and loaded):
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("navigate")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") == mid:
                replied = True
                err = (msg.get("error") or {}).get("message")
                if err:
                    raise RuntimeError("navigate -> %s" % err)
            elif msg.get("method") in ("Page.loadEventFired", "Page.frameStoppedLoading"):
                loaded = True

    def probe(self, timeout=10):
        """(readyState, outerHTML 长度)。用来判断正文是否还在长。"""
        r = self.call("Runtime.evaluate", {
            "expression": "document.readyState+'|'+(document.documentElement?"
                          "document.documentElement.outerHTML.length:0)",
            "returnByValue": True,
        }, timeout=timeout)
        val = str(((r.get("result") or {}).get("value")) or "|0")
        ready, _, size = val.partition("|")
        try:
            return ready, int(size)
        except ValueError:
            return ready, 0

    def html(self, timeout=10):
        r = self.call("Runtime.evaluate", {
            "expression": "document.documentElement?document.documentElement.outerHTML:''",
            "returnByValue": True,
        }, timeout=timeout)
        return ((r.get("result") or {}).get("value")) or ""

    def close(self):
        self.ws.close()


# ---------------------------------------------------------------- 浏览器进程

class Browser:
    def __init__(self, exe):
        self.exe = exe
        self.proc = None
        self.port = 0
        self.profile = ""
        self._lock = threading.Lock()

    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def ensure(self):
        with self._lock:
            if self.alive():
                return True
            self._kill_locked()
            t0 = time.time()
            profile = tempfile.mkdtemp(prefix="fair-cdp-")
            flags = os.path.join(profile, "DevToolsActivePort")
            cmd = [
                self.exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--disable-dev-shm-usage", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--mute-audio", "--hide-scrollbars",
                "--remote-debugging-port=0",
                "--remote-allow-origins=*",
                "--user-data-dir=" + profile,
                "about:blank",
            ]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
            except Exception:
                self.proc = None
                shutil.rmtree(profile, ignore_errors=True)
                return False
            deadline = time.time() + 20
            port = 0
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    break
                try:
                    with open(flags, "r", encoding="utf-8", errors="ignore") as f:
                        lines = [x.strip() for x in f.read().splitlines() if x.strip()]
                    if len(lines) >= 2:
                        port = int(lines[0])
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            if not port:
                self._kill_locked()
                shutil.rmtree(profile, ignore_errors=True)
                return False
            self.profile = profile
            self.port = port
            _STATE["start_ms"] = int((time.time() - t0) * 1000)
            return True

    def _kill_locked(self):
        if self.proc:
            try:
                if self.proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                       capture_output=True)
                    else:
                        self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self.proc = None
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = ""

    def stop(self):
        with self._lock:
            self._kill_locked()

    def _http(self, path, method="GET", timeout=10):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
        return json.loads(body) if body.strip() else {}

    def new_tab(self, timeout=10):
        """开一个空白标签页。新版 Chromium 要求 PUT，老版 GET，两个都试。"""
        q = urllib.parse.quote("about:blank", safe="")
        last = None
        for method in ("PUT", "GET"):
            try:
                info = self._http("/json/new?" + q, method=method, timeout=timeout)
                if info.get("webSocketDebuggerUrl"):
                    ws = WS(info["webSocketDebuggerUrl"], timeout=timeout)
                    return Tab(ws, info.get("id") or "")
                last = "no ws url"
            except Exception as e:                  # noqa: BLE001
                last = str(e)
        raise RuntimeError("new_tab failed: %s" % last)

    def close_tab(self, tab):
        try:
            tab.close()
        except Exception:
            pass
        tid = tab.target_id
        if not tid:
            return
        for method in ("PUT", "GET"):
            try:
                self._http("/json/close/" + tid, method=method, timeout=5)
                return
            except Exception:
                continue


_BROWSER = None
_BROWSER_LOCK = threading.Lock()


def _browser(exe):
    global _BROWSER
    with _BROWSER_LOCK:
        if _BROWSER is None or _BROWSER.exe != exe:
            if _BROWSER is not None:
                _BROWSER.stop()
            _BROWSER = Browser(exe)
        return _BROWSER


def _shutdown():
    global _BROWSER
    with _BROWSER_LOCK:
        if _BROWSER is not None:
            _BROWSER.stop()
            _BROWSER = None


atexit.register(_shutdown)


# ---------------------------------------------------------------- 对外入口

def render(url, exe, timeout=35, budget=8000):
    """渲染一个 URL，返回 outerHTML；CDP 不可用/失败时返回 None（调用方退回旧路径）。"""
    if _STATE["disabled"] or not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    b = _browser(exe)
    if not b.ensure():
        _fail()
        return None
    tab = None
    try:
        tab = b.new_tab(timeout=min(15, timeout))
        tab.call("Page.enable", timeout=10)
        tab.navigate(url, timeout=timeout)

        # 等正文稳定：readyState 到 complete 且 outerHTML 长度连续两次不变
        t0 = time.time()
        last, stable = -1, 0
        while True:
            ready, size = tab.probe(timeout=10)
            elapsed = time.time() - t0
            if size == last and ready == "complete":
                stable += 1
            else:
                stable = 0
                last = size
            if stable >= 2 and elapsed >= _MIN_SETTLE:
                break
            if elapsed * 1000 >= budget:
                break
            time.sleep(_POLL_GAP)

        html = tab.html(timeout=15)
        if not html:
            _fail()
            return None
        _STATE["ok"] += 1
        _STATE["consecutive_fail"] = 0
        return html
    except Exception:
        _fail()
        return None
    finally:
        _STATE["calls"] += 1
        if tab is not None:
            b.close_tab(tab)


def _fail():
    _STATE["fail"] += 1
    _STATE["consecutive_fail"] += 1
    if _STATE["consecutive_fail"] >= _DEAD_AFTER:
        _STATE["disabled"] = True
