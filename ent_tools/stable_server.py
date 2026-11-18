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

网址：http://127.0.0.1:8102/   （四端工作台；采集端单页是 /fair.html）
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


def _port_pids(port):
    """按端口找实际监听进程（state 里的 pid 可能过期，不能只信它）。"""
    pids = set()
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 timeout=8).stdout.decode("gbk", errors="replace")
            for line in out.splitlines():
                up = line.upper()
                if (":%d " % port) in line and "LISTENING" in up:
                    tail = line.split()
                    if tail and tail[-1].isdigit():
                        pids.add(int(tail[-1]))
        else:
            out = subprocess.run(["lsof", "-ti:%d" % port], capture_output=True,
                                 timeout=8).stdout.decode("utf-8", errors="replace")
            for tok in out.split():
                if tok.isdigit():
                    pids.add(int(tok))
    except Exception:
        pass
    return pids


def _wait_port_free(port, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _http_ok(port) and not _port_pids(port):
            return True
        time.sleep(0.2)
    return not _http_ok(port) and not _port_pids(port)


def start(port, force=False):
    # 重启语义：必须先把端口上的旧进程真正停掉，否则新代码永远进不了内存。
    # （旧实现把 force 写在 _http_ok 早返回之后，服务活着时 restart 是空操作。）
    if force:
        stop(port, hard=True)
        if not _wait_port_free(port):
            print("旧进程仍占用端口 %d，重启中止（可稍后重试）" % port)
            return 1
    elif _http_ok(port):
        print("已在运行：http://127.0.0.1:%d/   （四端工作台）" % port)
        return 0

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
            print("已启动 pid=%d：http://127.0.0.1:%d/   （四端工作台）"
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
    real = _port_pids(port)
    up = _http_ok(port)
    hint = ""
    if real and (not pid or pid not in real):
        # state 里记的 pid 和实际监听者不一致——多半是旧记录，提示出来免得又停错进程。
        hint = "（实际监听 pid=%s，state 记录已过期）" % ",".join(
            map(str, sorted(real)))
    print("端口 %d：%s | 记录 pid=%s（%s）%s"
          % (port, "UP" if up else "DOWN", pid,
             "存活" if _alive(pid) else "已退出", hint))
    return 0 if up else 1


def stop(port, hard=False):
    st = _load()
    pid = st.get("pid") if st.get("port") == port else None
    # state 记录的 pid 可能已过期（历史上就停错过进程），按端口再找一遍实际监听者。
    targets = set(_port_pids(port))
    if _alive(pid):
        targets.add(pid)
    if targets:
        for p in targets:
            try:
                os.kill(p, signal.SIGTERM if os.name != "nt"
                        else signal.CTRL_BREAK_EVENT)
            except Exception:
                try:
                    os.kill(p, signal.SIGTERM)
                except Exception:
                    pass
        for _ in range(20):
            if not any(_alive(p) for p in targets):
                break
            time.sleep(0.2)
    # 温和信号对 DETACHED 进程常常无效，重启场景直接强杀，保证新代码能加载。
    if hard:
        for p in targets:
            if _alive(p):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(p), "/F", "/T"],
                                       capture_output=True, timeout=8)
                    else:
                        os.kill(p, signal.SIGKILL)
                except Exception:
                    pass
        for _ in range(25):
            if not _http_ok(port) and not _port_pids(port):
                break
            time.sleep(0.2)
    if _load().get("port") == port:
        _save({})
    left = {p for p in targets if _alive(p)} | _port_pids(port)
    print("已停止" if not left else "进程仍在，可能需要手动结束 pid=%s"
          % ",".join(map(str, sorted(left))))
    return 0


def ensure_task():
    """校正开机自启任务的 python 路径，并让调度器立刻拉起一次服务。

    python 版本升级后路径会变（3.12.3 → 3.13.12），任务还指旧路径就会秒退。
    另外在强 job object 会话里手动 Start-ScheduledTask 会卡 Queued，所以这里
    用「20 秒后的一次性触发器」让系统调度器自己到点拉起——进程归属系统会话，
    不会被工具会话清理（已实测跨调用存活）。
    """
    if os.name != "nt":
        print("仅 Windows 需要计划任务；当前系统用 start 即可")
        return 0
    ps = (
        "$ErrorActionPreference='Stop';"
        "$t=Get-ScheduledTask -TaskName 'FairDemo8102';"
        "$a=$t.Actions[0];$a.Execute='%s';"
        "Set-ScheduledTask -TaskName 'FairDemo8102' -Action $a | Out-Null;"
        "$when=(Get-Date).AddSeconds(20);"
        "$trg=New-ScheduledTaskTrigger -Once -At $when;"
        "Set-ScheduledTask -TaskName 'FairDemo8102' -Trigger $trg | Out-Null;"
        # 关键：不调 Start-ScheduledTask —— 在强 job object 会话里它会卡在 Queued。
        # 改用一次性定时器，让系统调度器自己到点拉起进程，进程归属系统会话、
        # 不受工具会话清理。
        "'scheduled at ' + $when"
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
