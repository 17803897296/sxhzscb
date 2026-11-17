"""把正本镜像到「双选会助手离线副本」，供没网/断网时直接演示。

两个目录的关系是**单向**的：
    正本  ../careersafari-fair/         （开发、改代码、git 版本管理都在这里）
    副本  ../双选会助手离线副本/         （只读快照，不改代码、不提交）

为什么要有副本：现场断网、机房网络抽风、或对方只肯插 U 盘拷一个文件夹时，
副本双击就能跑，不依赖 git、不依赖联网（采集端调大模型那一步除外）。

用法：
    python -X utf8 ent_tools/sync_offline.py --init     # 第一次：建副本 + 写启动脚本
    python -X utf8 ent_tools/sync_offline.py            # 之后：增量同步（只拷变了的）
    python -X utf8 ent_tools/sync_offline.py --check    # 只比对，不动文件
    python -X utf8 ent_tools/sync_offline.py --diff     # 列出差异明细

约定：
- 副本里的 `离线版说明.md` / `启动离线版.bat` / `启动离线版.sh` / `同步记录.json`
  由本脚本生成并保护；正本没有同名文件，镜像时不会被删。
- `.git` 不进副本：副本是给人用的，不是第二个仓库。正本的 commit 号写进
  `同步记录.json`，出问题按这个号回正本查。
- `_bak_*` / `__pycache__` / `*.pyc` 不进副本：历史备份和字节码对演示没用，
  反而让「哪个文件是当前版本」变含糊。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT.parent / "双选会助手离线副本"

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    "_bak_careersafari_20261116", "_backup_before_infer_fill_20261116",
    "node_modules", ".workbuddy", ".idea", ".vscode",
}
EXCLUDE_FILES = {"_page_check.js", ".DS_Store", "Thumbs.db",
                 ".env", ".env.local", ".env.production"}   # 密钥文件绝不进副本
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".tmp", ".swp", ".pem", ".key")

PROTECT = {"离线版说明.md", "启动离线版.bat", "启动离线版.sh", "同步记录.json"}

MUST_HAVE = [
    "demo/fair.html",
    "demo/match.html",
    "demo/enterprises.html",
    "demo/portal.html",
    "ent_tools/fair_server.py",
    "ent_tools/data/fair_payload.json",
    "ent_tools/data/careersafari.db",
]


def _git(*args):
    try:
        p = subprocess.run(["git", "-C", str(ROOT)] + list(args),
                           capture_output=True, text=True, timeout=15)
        return (p.stdout or "").strip()
    except Exception:
        return ""


def _walk(src):
    for base, dirs, files in os.walk(src):
        b = Path(base)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            p = b / f
            rel = p.relative_to(src)
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                continue
            yield rel, p


def _same(a, b):
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def plan():
    add, upd, same = [], [], []
    src_files = {}
    for rel, p in _walk(ROOT):
        key = rel.as_posix()
        src_files[key] = p
        dst = DEST / rel
        if not dst.exists():
            add.append(rel)
        elif _same(p, dst):
            same.append(rel)
        else:
            upd.append(rel)
    dele = []
    if DEST.exists():
        for rel, p in _walk(DEST):
            key = rel.as_posix()
            if key in PROTECT or key in src_files:
                continue
            dele.append(rel)
    return add, upd, same, dele


def _md5(p):
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


OFFLINE_MD = """# 双选会助手 · 离线副本

这是正本 `{root_name}` 的**只读快照**，生成于 {when}。

## 怎么跑

- Windows：双击 `启动离线版.bat`
- macOS / Linux：`bash 启动离线版.sh`
- 只是想看报告：直接双击 `demo/match.html`（报告数据已内联进页面）

启动后浏览器会打开 <http://127.0.0.1:{port}/portal.html>，三个入口：

| 入口 | 断网能不能用 | 说明 |
|---|---|---|
| 企业端 `enterprises.html` | 能 | 粘贴岗位、导入名单、生成画像，全在本地 |
| 匹配端 `match.html` | 能 | 排行与单家报告，数据在 `ent_tools/data/fair_payload.json` |
| 采集端 `fair.html` | **部分** | 问卷、报告能跑；「岗位推荐」调大模型那一步需要联网 |

## 别在这里改代码

副本是 snapshot，改了不会回流到正本，下次同步会被覆盖。

- 正本：`{root}`
- 同步：在正本里跑 `python -X utf8 ent_tools/sync_offline.py`
- 只比对不写：加 `--check`；看差异明细：加 `--diff`

## 这份快照的出处

- 正本分支：`{branch}`
- 正本提交：`{commit}`{dirty}
- 文件数：{nfiles}
- 同步时间：{when}
"""

BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动双选会助手离线版...
start "" http://127.0.0.1:{port}/portal.html
python -X utf8 ent_tools\\fair_server.py --port {port}
if errorlevel 1 (
  echo.
  echo 启动失败：没找到 python，或端口被占用。
  echo 也可以直接双击 demo\\match.html 看报告。
  pause
)
"""

SH = """#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
echo "正在启动双选会助手离线版..."
( sleep 2; open "http://127.0.0.1:{port}/portal.html" 2>/dev/null || \\
  xdg-open "http://127.0.0.1:{port}/portal.html" 2>/dev/null ) &
python3 -X utf8 ent_tools/fair_server.py --port {port}
"""


def write_offline_scaffold(port=8103, nfiles=0):
    """写副本自己的说明与启动脚本（只在 --init 时调用，之后不动它们）。"""
    commit = _git("rev-parse", "--short", "HEAD") or "(无 git)"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "(无 git)"
    dirty = ""
    if _git("status", "--porcelain"):
        dirty = " · ⚠ 同步时正本有未提交改动"
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    md = OFFLINE_MD.format(root=ROOT, root_name=ROOT.name, when=when, port=port,
                           branch=branch, commit=commit, dirty=dirty, nfiles=nfiles)
    (DEST / "离线版说明.md").write_text(md, encoding="utf-8")
    # bat 必须 CRLF，否则部分 cmd 会把 \n 当成命令未结束
    (DEST / "启动离线版.bat").write_bytes(
        BAT.format(port=port).replace("\n", "\r\n").encode("utf-8"))
    sh = DEST / "启动离线版.sh"
    sh.write_text(SH.format(port=port), encoding="utf-8")
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass


def sync(init=False, check=False, diff=False, port=8103):
    t0 = time.time()
    add, upd, same, dele = plan()
    total = len(add) + len(upd) + len(same)
    print("正本 %s" % ROOT)
    print("副本 %s" % DEST)
    print("  新增 %d / 更新 %d / 未变 %d / 待删 %d（共 %d 个文件）"
          % (len(add), len(upd), len(same), len(dele), total))
    if check or diff:
        for tag, items in (("+", add), ("~", upd), ("-", dele)):
            for rel in items[:40]:
                print("   %s %s" % (tag, rel.as_posix()))
            if len(items) > 40:
                print("   %s …还有 %d 个" % (tag, len(items) - 40))
        if check:
            return 0

    DEST.mkdir(parents=True, exist_ok=True)
    if init:
        write_offline_scaffold(port=port, nfiles=total)
        print("  已写 离线版说明.md / 启动离线版.bat / 启动离线版.sh")

    n_copy = 0
    for rel in add + upd:
        src, dst = ROOT / rel, DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)          # 保留 mtime，下次比对才能判「没变」
        n_copy += 1
    n_del = 0
    for rel in dele:
        try:
            (DEST / rel).unlink()
            n_del += 1
        except OSError:
            pass

    missing = [m for m in MUST_HAVE if not (DEST / m).exists() or (DEST / m).stat().st_size == 0]
    if missing:
        print("  [失败] 副本缺关键产物：" + "、".join(missing))
        return 1

    # 关键产物记 md5：以后「页面上看到的东西和正本不一致」时，先比这几个数
    key_md5 = {m: _md5(DEST / m) for m in MUST_HAVE}
    record = {
        "_note": "由 ent_tools/sync_offline.py 生成；副本只读，改在正本改。",
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(ROOT),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "files": total,
        "copied": n_copy,
        "deleted": n_del,
        "md5": key_md5,
    }
    (DEST / "同步记录.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("  拷贝 %d 个、删除 %d 个，用时 %.1fs" % (n_copy, n_del, time.time() - t0))
    print("  关键产物 md5：")
    for k, v in key_md5.items():
        print("    %-42s %s" % (k, v[:12]))
    print("  副本就绪：双击「启动离线版.bat」→ http://127.0.0.1:%d/portal.html" % port)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="正本 -> 双选会助手离线副本 单向镜像")
    ap.add_argument("--init", action="store_true", help="首次建副本（同时写启动脚本与说明）")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    ap.add_argument("--diff", action="store_true", help="列出差异明细（不写文件）")
    ap.add_argument("--port", type=int, default=8103, help="副本服务端口（默认 8103，避让正本 8102）")
    a = ap.parse_args(argv)
    return sync(init=a.init, check=a.check, diff=a.diff, port=a.port)


if __name__ == "__main__":
    sys.exit(main())
