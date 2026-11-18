# -*- coding: utf-8 -*-
"""
v6 · 双选会助手同源服务（标准库，无第三方依赖）。

一条命令同时提供：四端工作台 + 采集端单页 + 采集端写库 + 在线岗位推荐 + 最新报告数据。

    python -X utf8 fair_server.py --port 8102
    浏览器打开 http://127.0.0.1:8102/          （四端工作台：采集 / 企业 / 匹配 / 测试）
    单独进采集端 http://127.0.0.1:8102/fair.html

接口：
    GET  /                              四端工作台（portal.html，默认入口）
    GET  /fair.html                     采集端单页（形式不变）
    GET  /api/fair/active-profile      当前生效的学生画像
    GET  /api/fair/payload             最新三端 payload（报告实时取它）
    POST /api/fair/profile             写画像进 fair_profiles + 重建 payload
    POST /api/fair/recommend-jobs      在线模型推荐岗位（失败自动规则兜底）
    POST /api/jobs-upload              企业端岗位上传（multipart，进隐藏台账）
    GET  /api/fair/health

离线双击 file:// 时页面仍走内置垫片，完全不需要本服务；只有同源 http(s)
才启用在线通道。这样「演示离线可用」和「采集端真能联网 / 真能写库」不冲突。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import fair_ingest          # noqa: E402
import fair_bridge          # noqa: E402
import payload_contract
import cache_guard           # noqa: E402  代码指纹：判断盘上 payload 是否由当前代码生成
import job_recommend        # noqa: E402
import resume_parse         # noqa: E402
import test_sessions        # noqa: E402  v12 测试数据库（只追加的实验日志）
import portal               # noqa: E402  复用企业端搜索/补全接口（/api/filter、/api/lookup、/api/names）
import match_fill           # noqa: E402  提交画像后按新画像重算 llm_match（hit/inferred/miss）
import intent_semantics     # noqa: E402  按需补判意向岗位语义（换画像 / 现场新勾岗位）
import hit_verify           # noqa: E402  按需批量复核命中（现场新意向）

PAGE = os.path.join(ROOT, "demo", "fair.html")
PAYLOAD = os.path.join(HERE, "data", "fair_payload.json")

# 企业端岗位上传的隐藏台账：只收不展 —— 页面上永远不列库内企业/岗位清单。
JOBS_UPLOAD_LOG = os.path.join(HERE, "data", "job_uploads.json")
JOBS_UPLOAD_FILES = os.path.join(HERE, "data", "job_uploads_files")


def _parse_multipart(content_type: str, raw: bytes):
    """从 multipart/form-data 里取文本字段与文件（Python 3.13 无 cgi 模块）。

    返回 (fields: dict[str, str], files: list[(filename, bytes)])。
    """
    fields, files = {}, []
    header = ("Content-Type: " + (content_type or "") + "\r\n"
              "MIME-Version: 1.0\r\n\r\n").encode("utf-8")
    msg = BytesParser(policy=policy.default).parsebytes(header + raw)
    parts = (list(msg.iter_parts()) if hasattr(msg, "iter_parts")
             else [p for p in msg.walk() if not p.is_multipart()])
    for part in parts:
        cd = part.get("Content-Disposition", "")
        name_m = re.search(r'name="([^"]+)"', cd)
        if not name_m:
            continue
        name = name_m.group(1)
        filename = part.get_filename()
        if filename:
            files.append((filename, part.get_payload(decode=True) or b""))
        else:
            fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return fields, files


def _save_job_upload(text: str, url: str, files) -> int:
    """岗位上传只进隐藏台账：正文截断存档、附件落盘，返回记录条数。

    页面只收到条数确认；库内清单、台账内容都不通过任何接口回显给企业端。
    """
    os.makedirs(JOBS_UPLOAD_FILES, exist_ok=True)
    try:
        with open(JOBS_UPLOAD_LOG, encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:
        entries = []

    ts = time.strftime("%Y%m%d-%H%M%S")
    saved = []
    for i, (fname, data) in enumerate(files or [], 1):
        safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", os.path.basename(fname)) or "file"
        path = os.path.join(JOBS_UPLOAD_FILES, "%s_%d_%s" % (ts, i, safe))
        try:
            with open(path, "wb") as f:
                f.write(data)
            saved.append({"name": fname, "size": len(data), "saved": True})
        except Exception:
            saved.append({"name": fname, "size": len(data), "saved": False})

    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    entries.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": url or "",
        "line_count": len(lines),
        "text_head": (text or "")[:400],
        "files": saved,
    })
    with open(JOBS_UPLOAD_LOG, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    return len(lines) + len(saved)


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


# ---------------------------------------------------------------- 响应压缩缓存
# payload（696 KB）和页面（1.2 MB）每次请求都重压一遍太浪费：20 并发就是 20 次
# gzip。按「ETag + 类型」缓存压好的字节，文件改了 ETag 就变，缓存自然失效。
_GZ = OrderedDict()
_GZ_LOCK = threading.Lock()
_GZ_MAX = 16


def _gz_cached(key, data):
    with _GZ_LOCK:
        hit = _GZ.get(key)
        if hit is not None:
            _GZ.move_to_end(key)
            return hit
    out = gzip.compress(data, 5)
    with _GZ_LOCK:
        _GZ[key] = out
        while len(_GZ) > _GZ_MAX:
            _GZ.popitem(last=False)
    return out


def _wants_gzip(header_value):
    """只认明确的 gzip；q=0 视为拒绝。"""
    if not header_value:
        return False
    for part in header_value.lower().split(","):
        bits = part.strip().split(";")
        if bits[0].strip() in ("gzip", "*"):
            if len(bits) > 1 and "q=0" in bits[1].replace(" ", ""):
                continue
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "FairBridge/7.0"
    # HTTP/1.1：开 keep-alive，浏览器一次连接复用多个请求（首屏 4 个请求只剩 1 次握手）。
    # 前提是每个响应都必须有准确的 Content-Length——本文件所有分支都走 _send()。
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ helpers
    def handle(self):
        # 开了 keep-alive 之后，浏览器/curl 关闭空闲连接时服务器可能正好在
        # readline()，标准库会把 ConnectionResetError 打成整段 traceback。
        # 这不是错误，静默收尾即可，否则日志会被噪声淹没。
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _send(self, data, ctype, status=200, etag=None, cacheable=None):
        """统一出口：ETag 校验（304）+ gzip + keep-alive 所需的 Content-Length。"""
        if cacheable is None:
            cacheable = (self.command == "GET" and status == 200)
        if etag is None:
            etag = '"%s"' % hashlib.blake2b(data, digest_size=8).hexdigest()

        if cacheable:
            inm = self.headers.get("If-None-Match") or ""
            if etag in [t.strip() for t in inm.split(",")]:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return

        body = data
        gz = False
        if len(data) >= 1024 and _wants_gzip(self.headers.get("Accept-Encoding")):
            body = _gz_cached(etag + "|" + ctype, data)
            gz = True

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache" if cacheable else "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                # 客户端中途断开：别让异常冒到路由里再试一次写响应
                self.close_connection = True

    def _json(self, obj, code=200):
        out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(out, "application/json; charset=utf-8", status=code)

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
        self._send(data, ctype)

    def _bytes_file(self, path, ctype):
        """按 (mtime, size) 生成 ETag——大文件不用算哈希，改版后自动失效。"""
        st = os.stat(path)
        with open(path, "rb") as f:
            data = f.read()
        self._send(data, ctype, etag='"%x-%x"' % (st.st_mtime_ns, st.st_size))

    # ------------------------------------------------------------ routes
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            # 统一入口：一个网址里用三个标签页串起问卷 / 报告 / 测试记录
            if p in ("/", "/portal.html", "/index.html"):
                self._bytes_file(os.path.join(ROOT, "demo", "portal.html"),
                                 "text/html; charset=utf-8")
                return
            # demo 目录下的其它静态页（展位排行快照 / 样例 / 演练页）：
            # 只放行 demo/ 下的单层 .html 文件，不接受带目录的路径，防穿越。
            if p.endswith(".html") and p.count("/") == 1:
                cand = os.path.join(ROOT, "demo", p.lstrip("/"))
                if os.path.isfile(cand):
                    self._bytes_file(cand, "text/html; charset=utf-8")
                    return
            if p == "/js/match.js":
                # 前端 match 模块源码（企业端名单导入块 fetch 注入用）。
                # 占位符在这边替换：COMPANIES 给空池（名单导入不依赖内置档案），
                # BUILTIN_NAMES 用库里 company_jobs 的真实参会名单。
                _msrc = open(os.path.join(ROOT, "ent_tools", "js", "match.js"),
                             encoding="utf-8").read()
                try:
                    _con = fair_ingest.connect()
                    _names = [r[0] for r in _con.execute(
                        "SELECT name FROM company_jobs ORDER BY name")]
                    _con.close()
                except Exception:
                    _names = []
                _msrc = (_msrc.replace("__COMPANIES__", "[]")
                              .replace("__NAMES__", json.dumps(_names, ensure_ascii=False)))
                _raw = _msrc.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(_raw)))
                self.end_headers()
                self.wfile.write(_raw)
                return
            if p == "/fair.html":
                self._bytes_file(PAGE, "text/html; charset=utf-8")
                return
            # 独立的测试记录管理页：查看 / 删除 / 批量清理测试脏数据
            if p in ("/tests", "/tests.html"):
                self._bytes_file(os.path.join(ROOT, "demo", "tests.html"),
                                 "text/html; charset=utf-8")
                return
            if p == "/api/fair/health":
                # code_fp 让「跑的是哪版代码」可被外部看见；version 保留给旧探测兼容。
                self._json({"ok": True, "service": "fair_server", "version": 7,
                            "code_fp": cache_guard.build_fp(),
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
                        # 两道闸：画像换了，或生成 payload 的代码不是当前代码。
                        # 只比 fair_id 会漏掉「代码改了但画像没变」这种最难察觉的陈旧
                        # （历史事故：改了推荐/复检逻辑，页面还是旧结论）。
                        if (_cached.get("active_fair_id") != _active.get("fair_id")
                                or _cached.get("code_fp") != cache_guard.build_fp()):
                            fair_bridge.build()
                    except Exception:
                        fair_bridge.build()
                # payload 是首屏最大的一块：走文件 ETag，配合 gzip 后只传 77 KB
                # 出门前过一遍契约（评审 Step 1）：盘上文件被手改/旧版本残留
                # 时在这里拦下，不给前端一份违约 JSON。
                try:
                    with open(PAYLOAD, encoding="utf-8") as _f:
                        payload_contract.ensure_valid(json.load(_f))
                except payload_contract.PayloadContractError as _pce:
                    fair_bridge.build()   # 违约 → 按当前画像重建一份合法的
                self._bytes_file(PAYLOAD, "application/json; charset=utf-8")
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
        # 企业端岗位上传：multipart，进隐藏台账（页面不回显库内清单）。
        if p == "/api/jobs-upload":
            raw = self._raw()
            try:
                fields, files = _parse_multipart(
                    self.headers.get("Content-Type", ""), raw)
                text = (fields.get("text", "") or "").strip()
                url = (fields.get("url", "") or "").strip()
                if not text and not url and not files:
                    # 空提交不进台账：前端本有拦截，这里是防垃圾记录的最后闸
                    self._json({"ok": False,
                                "detail": "空提交：请粘贴岗位信息、招聘页链接或添加附件"}, 400)
                    return
                count = _save_job_upload(text, url, files)
                self._json({"ok": True, "count": count})
            except Exception as exc:
                traceback.print_exc()
                self._json({"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return
        raw = self._raw()
        body = self._json_body(raw)
        try:
            # ---- 企业搜索/补全（来自 portal.py，单一来源）----
            if p == "/api/names":
                # 文件名走查询串：HTTP 头只能是 ISO-8859-1，中文文件名塞进
                # X-Filename 会让浏览器直接抛 TypeError（页面显示「本地服务没起来」，
                # 服务端连请求都收不到）。头里的旧写法保留做兼容。
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                fname = (q.get("f") or [""])[0] or self.headers.get("X-Filename") or ""
                out = portal.api_names(raw, urllib.parse.unquote(fname))
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
            # ---- 按需批量复核命中 ----
            # payload 的 hit_verdicts 只覆盖构建时那份画像 + 内置测试画像的意向。
            # 学生现场新填一个意向时，本地规则仍可能被共享字骗过（食品研发 × 药品
            # 研发 只共享「研发」）。这里把页面判成规则命中的对子成批送判一次，
            # 结果进同一个 pair 缓存；判不动的对子不返回，前端继续标「未复核」。
            if p == "/api/fair/verify":
                pairs = []
                for x in (body.get("pairs") or []):
                    if isinstance(x, (list, tuple)) and len(x) >= 2:
                        it, t = str(x[0]).strip(), str(x[1]).strip()
                        if it and t:
                            pairs.append((t, it))
                with _SEM_LOCK:
                    verdicts, meta = hit_verify.verify_pairs(pairs)
                self._json({"ok": True, "verdicts": verdicts, "meta": meta}, 200)
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
                    allow_relax=bool(body.get("allow_relax")),
                    experiences=body.get("experiences") or [])
                self._json(out)
                return
            if p == "/api/fair/rebuild":
                payload = fair_bridge.build()
                self._json({"ok": True, "stats": payload["stats"]})
                return
            if p == "/api/fair/preview":
                # 薄页面「换画像预览」：按请求里的画像跑一遍 装载→匹配→组装，
                # 返回与 payload 同形状的结果。不动 fair_active、不写 fair_profiles、
                # 不覆盖 data/fair_payload.json —— 生效画像仍是当前学生那份。
                doc = body.get("document") or body.get("profile") or body
                try:
                    payload = fair_bridge.build_preview(doc)
                except Exception as exc:
                    traceback.print_exc()
                    self._json({"ok": False,
                                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
                    return
                self._json({"ok": True, "rows": payload.get("rows") or [],
                            "weights": payload.get("weights") or {},
                            "profile": payload.get("profile") or {},
                            "intent_semantics": payload.get("intent_semantics") or {},
                            "campus_no_fit": payload.get("campus_no_fit") or {},
                            "biz_fit_no": payload.get("biz_fit_no") or {},
                            "stats": payload.get("stats") or {}})
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


def _already_serving(host: str, port: int, timeout: float = 0.5) -> bool:
    """启动前探测该端口是否已有服务在响应。

    Windows 的 SO_REUSEADDR 允许两个进程同时 LISTENING 同一端口
    （2026-11-15 实测：旧进程没死、新进程照常启动成功，请求随机打到旧代码上，
    表现成「改了代码不生效」「缓存指纹是新的但结果是旧的」）。光靠 bind 不报错
    发现不了，必须主动 connect 一次：连得上就说明已经有服务在跑。
    """
    import socket
    probe = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((probe, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8102)
    a = ap.parse_args()
    # 单实例纪律：同端口只允许一个服务。多实例请换端口，不要重复绑定。
    if _already_serving(a.host, a.port):
        print("✗ %s:%d 已有服务在响应，拒绝启动第二个实例。" % (a.host, a.port))
        print("  Windows 允许两个进程绑定同一端口，同时跑会让请求随机打到旧代码上。")
        print("  查旧进程： netstat -ano | findstr :%d" % a.port)
        print("  停掉旧进程： taskkill /F /PID <PID>")
        print("  确认没有 LISTENING 后重新启动；确实要并行请改用另一个端口。")
        raise SystemExit(2)
    # 运行时活库不入库：仓库只放只读种子 careersafari.db.seed，
    # 首次启动复制出本地库。git pull / stash / checkout 永远不会再替换
    # 一个正在被 SQLite 写入的文件（2026-11-15 实测会写成 disk image is malformed）。
    # 复制的实现在 repository.ensure_live_db（连接层也调它），这里只负责提示。
    import repository
    repository.ensure_live_db(quiet=False)
    # 缓存同理：活缓存服务一跑就会被改写，跟踪它等于让 clone 出来的 git status
    # 永久是脏的。仓库只放 *.seed，缺活文件时在这里复制出来。
    try:
        import cache_guard
        _restored = cache_guard.restore_seeds()
        if _restored:
            print("  已从只读种子恢复缓存：%s" % "、".join(_restored))
    except Exception as exc:
        print("  缓存种子恢复失败：%s（首屏仍可读 payload，重建会重算）" % exc)
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
    print("双选会助手 v6 同源服务  http://%s:%d/   （四端工作台：采集 / 企业 / 匹配）"
          % (a.host, a.port))
    print("  采集端单页 /fair.html ｜ 报告数据 GET /api/fair/payload")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
