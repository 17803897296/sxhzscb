# -*- coding: utf-8 -*-
"""
v6 · 双选会助手同源服务（标准库，无第三方依赖）。

一条命令同时提供：原 fair.html 页面 + 采集端写库 + 在线岗位推荐 + 最新报告数据。

    python -X utf8 fair_server.py --port 8102
    浏览器打开 http://127.0.0.1:8102/fair.html

接口：
    GET  /fair.html                     原页面（形式不变）
    GET  /api/fair/active-profile      当前生效的学生画像
    GET  /api/fair/payload             最新三端 payload（报告实时取它）
    POST /api/fair/profile             写画像进 fair_profiles + 重建 payload
    POST /api/fair/recommend-jobs      在线模型推荐岗位（失败自动规则兜底）
    GET  /api/fair/health

离线双击 file:// 时页面仍走内置垫片，完全不需要本服务；只有同源 http(s)
才启用在线通道。这样「演示离线可用」和「采集端真能联网 / 真能写库」不冲突。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import fair_ingest          # noqa: E402
import fair_bridge          # noqa: E402
import job_recommend        # noqa: E402
import resume_parse         # noqa: E402
import test_sessions        # noqa: E402  v12 测试数据库（只追加的实验日志）
import portal               # noqa: E402  复用企业端搜索/补全接口（/api/filter、/api/lookup、/api/names）

PAGE = os.path.join(ROOT, "demo", "fair.html")
PAYLOAD = os.path.join(HERE, "data", "fair_payload.json")


class Handler(BaseHTTPRequestHandler):
    server_version = "FairBridge/7.0"

    # ------------------------------------------------------------ helpers
    def _json(self, obj, code=200):
        out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _raw(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _json_body(self, raw):
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ routes
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/fair.html", "/index.html"):
                with open(PAGE, "rb") as f:
                    self._bytes(f.read(), "text/html; charset=utf-8")
                return
            if p == "/api/fair/health":
                self._json({"ok": True, "service": "fair_server", "version": 7,
                            "db": fair_ingest.DB,
                            "active": fair_ingest.stats().get("active")})
                return
            if p == "/api/fair/active-profile":
                s = fair_ingest.stats()
                act = s.get("active")
                if not act:
                    self._json({"ok": False, "reason": "尚无画像"})
                    return
                con = fair_ingest.connect()
                try:
                    row = con.execute(
                        "SELECT document_json FROM fair_profiles WHERE fair_id=?",
                        (act["fair_id"],)).fetchone()
                finally:
                    con.close()
                self._json({"ok": True, "fair_id": act["fair_id"],
                            "document": json.loads(row[0]) if row else None})
                return
            if p == "/api/fair/feedback":
                self._json({"ok": True, "items": fair_ingest.read_feedback()})
                return
            if p == "/api/fair/payload":
                if not os.path.exists(PAYLOAD):
                    fair_bridge.build()
                else:
                    # 安全网：生效画像变了（现场重新采集 / 切画像）就重建，
                    # 避免报告停留在上一个画像（缓存不重建老坑）。
                    try:
                        _active = fair_ingest.stats().get("active") or {}
                        with open(PAYLOAD, encoding="utf-8") as _f:
                            _cached = json.load(_f)
                        if _cached.get("active_fair_id") != _active.get("fair_id"):
                            fair_bridge.build()
                    except Exception:
                        fair_bridge.build()
                with open(PAYLOAD, "rb") as f:
                    self._bytes(f.read(), "application/json; charset=utf-8")
                return
            self.send_error(404)
        except Exception as exc:
            self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):
        p = self.path.split("?")[0]
        # 简历上传是 multipart，必须在按 JSON 解析之前单独处理。
        if p == "/api/fair/resume":
            raw = self._raw()
            try:
                out = resume_parse.handle_multipart(
                    self.headers.get("Content-Type", ""), raw,
                    use_llm=("use_llm=true" in (self.path or "")))
                self._json(out)
            except Exception as exc:
                traceback.print_exc()
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return
        raw = self._raw()
        body = self._json_body(raw)
        try:
            # ---- 企业搜索/补全（来自 portal.py，单一来源）----
            if p == "/api/names":
                out = portal.api_names(raw, self.headers.get("X-Filename") or "")
                self._json(out, 200)
                return
            if p == "/api/filter":
                out = portal.api_filter(body)
                self._json(out, 200)
                return
            if p == "/api/lookup":
                out = portal.api_lookup(body)
                self._json(out, 200)
                return
            # ---- 原有 fair 接口 ----
            if p == "/api/fair/profile":
                doc = body.get("document") or body
                out = fair_ingest.save_profile(doc, rebuild=True)
                self._json(out)
                return
            if p == "/api/fair/feedback":
                out = fair_ingest.save_feedback(
                    body.get("name", ""), body.get("result", ""),
                    body.get("note", ""), body.get("fair_id", ""), rebuild=True)
                self._json(out)
                return
            if p == "/api/fair/recommend-jobs":
                out = job_recommend.recommend(
                    body.get("major", ""), body.get("experiences", []),
                    body.get("skills", []), body.get("use_llm", True),
                    body.get("exclude") or [])
                self._json(out)
                return
            if p == "/api/fair/rebuild":
                payload = fair_bridge.build()
                self._json({"ok": True, "stats": payload["stats"]})
                return
            # v12 测试数据库：把「这次填了什么 + 排出了什么」落一条只追加的记录
            if p == "/api/fair/test-save":
                payload = json.load(open(PAYLOAD, encoding="utf-8"))
                out = test_sessions.save(
                    payload, tag=body.get("tag", ""), sid=body.get("sid", ""),
                    note=body.get("note", ""), top_n=int(body.get("top_n") or 12))
                self._json(out)
                return
            self.send_error(404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write("[fair_server] %s\n" % (fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8102)
    a = ap.parse_args()
    if not os.path.exists(PAYLOAD):
        fair_bridge.build()
    # 企业端搜索/初筛接口复用 portal.py，需要先初始化模型；不初始化会静默降级成关键词规则
    try:
        if portal.setup_model():
            print("  模型：已配置（/api/filter 走大模型初筛）")
        else:
            print("  模型：未配置 key，/api/filter 走关键词规则")
    except Exception as exc:
        print("  模型初始化失败：%s（/api/filter 走关键词规则）" % exc)
    print("双选会助手 v6 同源服务  http://%s:%d/fair.html" % (a.host, a.port))
    print("  写库端点 POST /api/fair/profile ｜ 报告数据 GET /api/fair/payload")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
