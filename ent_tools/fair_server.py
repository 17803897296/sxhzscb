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
import threading
import time
import traceback
import urllib.parse
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
import match_fill           # noqa: E402  提交画像后按新画像重算 llm_match（hit/inferred/miss）
import intent_semantics     # noqa: E402  按需补判意向岗位语义（换画像 / 现场新勾岗位）

PAGE = os.path.join(ROOT, "demo", "fair.html")
PAYLOAD = os.path.join(HERE, "data", "fair_payload.json")


def _payload_titles():
    """payload 里出现过的全部企业岗位名，给按需语义补判当候选池用。"""
    try:
        with open(PAYLOAD, encoding="utf-8") as f:
            p = json.load(f)
    except Exception:
        return []
    out = set()
    for r in p.get("rows") or []:
        for t in (r.get("job_titles") or []):
            if t:
                out.add(str(t).strip())
        for t in (r.get("matched_jobs") or []):
            if t:
                out.add(str(t).strip())
    return sorted(out)


# ---- 提交画像后按新画像重算 llm_match（hit/inferred/miss）----
# 这是「画像影响命中/排除判定」的关键闭环：之前 llm_match 是某次全量重算冻结的
# 旧画像结论，换学生后只重算分数、不重算命中，导致报告里出现上一个学生的意向岗位。
# 现在提交画像即后台跑 match_fill --force，跑完再 build()，前端轮询拿到新报告。
_RECOMPUTE = {"busy": False, "started": None, "done": None,
              "error": None, "profile": None, "lock": threading.Lock()}
_SEM_LOCK = threading.Lock()      # 同一时刻只跑一次语义补判，避免并发重复调模型


def _recompute_worker():
    with _RECOMPUTE["lock"]:
        _RECOMPUTE["busy"] = True
        try:
            _RECOMPUTE["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _RECOMPUTE["error"] = None
            _companies, _llm, _raw = fair_bridge.read_db()
            _prof = fair_bridge.profile_from(_raw)
            _RECOMPUTE["profile"] = "%s / 意向 %s" % (
                _prof.get("major"), "、".join(_prof.get("intended_jobs") or []))
            match_fill.fill(force=True, batch=8, workers=5, profile=_prof)
            fair_bridge.build()
            _RECOMPUTE["done"] = time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:                       # 失败不写半截结论；llm_match 保持上次良好状态
            _RECOMPUTE["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            traceback.print_exc()
        finally:
            _RECOMPUTE["busy"] = False


def _trigger_recompute():
    """已有重算在跑就跳过，避免并发写 llm_match / payload。"""
    with _RECOMPUTE["lock"]:
        if _RECOMPUTE["busy"]:
            return False
        threading.Thread(target=_recompute_worker, daemon=True).start()
        return True


def _recompute_busy():
    return _RECOMPUTE["busy"]


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
            # 统一入口：一个网址里用三个标签页串起问卷 / 报告 / 测试记录
            if p in ("/", "/portal.html", "/index.html"):
                with open(os.path.join(ROOT, "demo", "portal.html"), "rb") as f:
                    self._bytes(f.read(), "text/html; charset=utf-8")
                return
            # demo 目录下的其它静态页（展位排行快照 / 样例 / 演练页）：
            # 只放行 demo/ 下的单层 .html 文件，不接受带目录的路径，防穿越。
            if p.endswith(".html") and p.count("/") == 1:
                cand = os.path.join(ROOT, "demo", p.lstrip("/"))
                if os.path.isfile(cand):
                    with open(cand, "rb") as f:
                        self._bytes(f.read(), "text/html; charset=utf-8")
                    return
            if p == "/fair.html":
                with open(PAGE, "rb") as f:
                    self._bytes(f.read(), "text/html; charset=utf-8")
                return
            # 独立的测试记录管理页：查看 / 删除 / 批量清理测试脏数据
            if p in ("/tests", "/tests.html"):
                with open(os.path.join(ROOT, "demo", "tests.html"), "rb") as f:
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
            if p == "/api/fair/version":
                # 轻量轮询端点：报告页每 15 秒看一眼 generated_at，
                # 变了就提示刷新，避免「页面还开着、数据已经换了一版」。
                gen = ""
                if os.path.exists(PAYLOAD):
                    try:
                        with open(PAYLOAD, encoding="utf-8") as f:
                            gen = (json.load(f).get("generated_at") or "")
                    except Exception:
                        gen = ""
                self._json({"ok": True, "generated_at": gen})
                return
            if p == "/api/fair/payload":
                # 提交画像后正在按新画像重算命中/排除，先告诉前端「计算中」去轮询，
                # 不要返回旧 payload（否则学生会看到上一个学生的意向岗位结论）。
                if _recompute_busy():
                    self._json({"ok": False, "computing": True,
                                "started": _RECOMPUTE.get("started"),
                                "profile": _RECOMPUTE.get("profile"),
                                "note": "正在按你的画像重算全部企业的岗位匹配（约 1–3 分钟），请稍候……"})
                    return
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
            # v12 测试记录：列表 / 单条详情（供页面查看与清理）
            if p == "/api/fair/test-list":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = int((q.get("limit") or ["50"])[0])
                self._json({"ok": True, "items": test_sessions.listing(limit=limit)})
                return
            if p == "/api/fair/test-detail":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                rid = int((q.get("id") or ["0"])[0])
                self._json({"ok": True, "item": test_sessions.detail(rid)})
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
            # ---- 按需补判意向岗位语义 ----
            # payload 里的 intent_semantics 只覆盖构建时那份生效画像。学生换画像、
            # 现场新勾一个岗位后，前端拿不到映射就只能退回本地字面规则、报告里写
            # 「本地规则判定」。这里按意向补一次大模型判定，判完写独立缓存。
            if p == "/api/fair/semantics":
                intents = body.get("intents")
                if not intents and body.get("intent"):
                    intents = [body.get("intent")]
                with _SEM_LOCK:
                    out = intent_semantics.live_map(
                        intents or [], body.get("titles") or _payload_titles())
                out["ok"] = True
                self._json(out, 200)
                return
            # ---- 原有 fair 接口 ----
            if p == "/api/fair/profile":
                doc = body.get("document") or body
                # rebuild=False：先只写库并置为生效画像，避免用旧 llm_match 生成一份
                # 假报告；真正的重建在后台重算命中后由 _recompute_worker 完成。
                out = fair_ingest.save_profile(doc, rebuild=False)
                _trigger_recompute()
                out["recomputing"] = True
                self._json(out)
                return
            if p == "/api/fair/feedback":
                out = fair_ingest.save_feedback(
                    body.get("name", ""), body.get("result", ""),
                    body.get("note", ""), body.get("fair_id", ""), rebuild=True)
                self._json(out)
                return
            if p == "/api/fair/recommend-jobs":
                # v14：实习 / 竞赛 / 论文分开传，换一批按层级逐步放宽
                out = job_recommend.recommend(
                    body.get("major", ""),
                    internships=body.get("internships") or [],
                    competitions=body.get("competitions") or [],
                    papers=body.get("papers") or [],
                    skills=body.get("skills") or [],
                    use_llm=body.get("use_llm", True),
                    shown=body.get("shown") or body.get("exclude") or [],
                    tier=body.get("tier", 0),
                    accept_no_major=bool(body.get("accept_no_major")),
                    experiences=body.get("experiences") or [])
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
                    note=body.get("note", ""), top_n=int(body.get("top_n") or 12),
                    browser=body.get("browser") or None)
                self._json(out)
                return
            # 按 id 删除单条测试记录；不做批量清空，避免误删真人数据
            if p == "/api/fair/test-delete":
                self._json(test_sessions.delete(body.get("id")))
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
