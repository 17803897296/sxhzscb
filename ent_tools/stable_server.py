# -*- coding: utf-8 -*-
"""稳定启动本地服务：脱离调用会话，不会随终端/工具退出而被杀。

背景：Windows 上从 CI/工具会话里 Start-Process 或 nohup 起的进程，会被
job object 连同会话一起终止（表现为「刚起时 200，下一条命令就拒绝连接」）。
本脚本用 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + BREAKAWAY_FROM_JOB
把服务进程从会话里摘出去，并用就绪探针确认它真的在监听才返回。

用法：
    python -X utf8 stable_server.py start [--port 8102]   启动（幂等，已在跑就不重复起）
    python -X utf8 stable_server.py status [--port 8102]  查状态（pid + HTTP 探针）
    python -X utf8 stable_server.py stop  [--port 8102]   停掉（只停本脚本起的）
    python -X utf8 stable_server.py restart [--port 8102] 重启
    python -X utf8 stable_server.py ensure-task             校正开机自启任务的
                                                            python 路径（版本升级后跑一次）

非 Windows 上退化为 start_new_session=True（同样脱离会话）。

跨会话常驻的两种方式（按优先级）：
  1. 开机自启计划任务 FairDemo8102（由系统调度器托管，最稳）。python 解释器
     版本升级后路径会变（3.12.3 → 3.13.12），任务还指旧路径就会秒退；
     跑一次 `ensure-task` 校正。若当前账户无注册权限，需手动在「任务计划程序」
     里更新操作路径为 sys.executable。
  2. 本脚本 start：DETACHED + BREAKAWAY，适合普通桌面会话；若宿主环境用了
     强 job object（部分 CI/工具宿主），进程仍可能随会话被清理 —— 那种环境
     请用方式 1。

网址：http://127.0.0.1:8102/enterprises.html
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "stable_server.json")


def _http_ok(port):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/fair/health" % port, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _alive(pid):
    """Windows 上 os.kill(pid, 0) 对脱离会话的进程会误报，用 tasklist 查。"""
    if not pid:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5).stdout.decode(
                    "gbk", errors="replace")
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _start_flags():
    if os.name != "nt":
        return {}, {"start_new_session": True}
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    return {"creationflags": flags, "close_fds": True}, {}


def start(port, force=False):
    if _http_ok(port):
        print("已在运行：http://127.0.0.1:%d/enterprises.html" % port)
        return 0
    if force:
        stop(port)

    log_path = os.path.join(HERE, "data", "fair_server_%d.log" % port)
    log = open(log_path, "a", encoding="utf-8")
    kwargs, extra = _start_flags()
    try:
        p = subprocess.Popen(
            [sys.executable, "-X", "utf8", "fair_server.py", "--port", str(port)],
            cwd=HERE, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, **kwargs, **extra)
    except OSError as exc:
        # BREAKAWAY_FROM_JOB 在个别环境不被允许，去掉它再试一次
        if os.name == "nt" and "creationflags" in kwargs:
            kwargs["creationflags"] &= ~getattr(
                subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            try:
                p = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "fair_server.py",
                     "--port", str(port)],
                    cwd=HERE, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, **kwargs, **extra)
            except OSError as exc2:
                log.close()
                print("启动失败：%s" % exc2)
                return 1
        else:
            log.close()
            print("启动失败：%s" % exc)
            return 1
    finally:
        log.close()

    _save({"pid": p.pid, "port": port, "log": log_path})
    for _ in range(40):
        if _http_ok(port):
            print("已启动 pid=%d：http://127.0.0.1:%d/enterprises.html"
                  % (p.pid, port))
            return 0
        if not _alive(p.pid):
            break
        time.sleep(0.25)
    print("进程退出或就绪超时，看日志：%s" % log_path)
    return 1


def status(port):
    st = _load()
    pid = st.get("pid") if st.get("port") == port else None
    print("端口 %d：%s | 记录 pid=%s（%s）"
          % (port, "UP" if _http_ok(port) else "DOWN", pid,
             "存活" if _alive(pid) else "已退出"))
    return 0 if _http_ok(port) else 1


def stop(port):
    st = _load()
    pid = st.get("pid") if st.get("port") == port else None
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM if os.name != "nt" else signal.CTRL_BREAK_EVENT)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        for _ in range(20):
            if not _alive(pid):
                break
            time.sleep(0.2)
    if _load().get("port") == port:
        _save({})
    print("已停止" if not _alive(pid) else "进程仍在，可能需要手动结束 pid=%s" % pid)
    return 0


def ensure_task():
    """校正开机自启计划任务的 python 路径（版本升级后必须跑一次）。

    用 PowerShell 的 Set-ScheduledTask 更新现有任务的操作路径为当前解释器。
    任务不存在或权限不足时给出明确指引，不静默失败。
    """
    if os.name != "nt":
        print("仅 Windows 需要计划任务；当前系统用 start 即可")
        return 0
    ps = (
        "$ErrorActionPreference='Stop';"
        "$t=Get-ScheduledTask -TaskName 'FairDemo8102';"
        "$a=$t.Actions[0];$a.Execute='%s';"
        "Set-ScheduledTask -TaskName 'FairDemo8102' -Action $a | Out-Null;"
        "Start-ScheduledTask -TaskName 'FairDemo8102';"
        "'OK path=' + (Get-ScheduledTask -TaskName FairDemo8102).Actions[0].Execute"
        % sys.executable.replace("'", "''")
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=30)
        out = r.stdout.decode("gbk", errors="replace").strip()
        err = r.stderr.decode("gbk", errors="replace").strip()
        if r.returncode == 0:
            print(out or "OK")
            return 0
        print("校正失败：%s\n%s\n请手动在任务计划程序里把 FairDemo8102 的操作路径改为 %s"
              % (out, err, sys.executable))
        return 1
    except Exception as exc:
        print("无法执行 PowerShell：%s" % exc)
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("start", "status", "stop", "restart", "ensure-task"))
    ap.add_argument("--port", type=int, default=8102)
    a = ap.parse_args()
    if a.action == "start":
        return start(a.port)
    if a.action == "status":
        return status(a.port)
    if a.action == "stop":
        return stop(a.port)
    if a.action == "ensure-task":
        return ensure_task()
    start(a.port, force=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
