# -*- coding: utf-8 -*-
"""
sync_repo.py · 架构纪律工具：把「开发正本」同步到「发布仓库 / 克隆验收目录」。

背景（2026-11-15）：过去靠手写 cp 同步，漏一项就出现「克隆页是旧副本」
「改了代码没生效」「测试记录写进另一个实例的库」。这份清单是唯一的同步规则，
以后不要再手写 cp。

    python -X utf8 ent_tools/sync_repo.py --check          # 干跑：列出所有不一致
    python -X utf8 ent_tools/sync_repo.py --apply          # 重建页面 + 同步到发布仓库
    python -X utf8 ent_tools/sync_repo.py --apply --target <目录>   # 追加目标
    python -X utf8 ent_tools/sync_repo.py --apply --include-payload # 显式带上报告数据准据

约定见仓库根 ARCHITECTURE.md。绝不自动 git commit / push，那一步由人决定。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../default-workspace/ent_tools
DEV = HERE.parent                              # .../default-workspace  ← 唯一开发正本
REPO = DEV / "careersafari-fair"               # 发布仓库（GitHub 的工作副本）
CLONE = DEV.parent / "sxhzscb-deploy"          # 从 GitHub 克隆的验收实例（无常驻服务）

# 需要同步的「活源文件」。改了这些文件，发布/验收两边必须跟上。
GLOBS = [
    ("ent_tools", "*.py"),
    ("ent_tools", "*.md"),
    ("ent_tools", ".env.example"),
    ("ent_tools/data/test_users", "*.json"),
    ("demo", "*.js"),
    ("demo", "questionnaire.json"),
]

# demo 下的 HTML 用白名单：目录里混着「排行_李炳成」「采集数据_周航」这类
# 含真实姓名/个人信息的页面，靠黑名单排不干净，绝不能自动同步进公开仓库。
DEMO_HTML = [
    "collect.html", "fair.html", "index.html", "portal.html",
    "start.html", "test_users.html", "tests.html",
]

# 永不复制：密钥、本机运行时数据、一次性调试脚本、含个人信息的报告页。
def _blocked(rel: Path) -> bool:
    s = str(rel).replace("\\", "/")
    name = rel.name
    return (
        name == ".env"
        or name.endswith(".db")
        or name.startswith("_")
        or name.startswith("排行_")
        or "排行_" in name
        or "__pycache__" in s
        or "_stale_cache" in s
        or name.endswith(".log")
    )


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_files(include_payload=False, include_db=False):
    out = []
    for sub, pat in GLOBS:
        d = DEV / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob(pat)):
            if not p.is_file():
                continue
            rel = p.relative_to(DEV)
            if _blocked(rel):
                continue
            out.append(rel)
    for name in DEMO_HTML:
        p = DEV / "demo" / name
        if p.is_file():
            out.append(p.relative_to(DEV))
    if include_payload:
        p = DEV / "ent_tools/data/fair_payload.json"
        if p.is_file():
            out.append(p.relative_to(DEV))
    if include_db:
        p = DEV / "ent_tools/data/careersafari.db"
        if p.is_file():
            out.append(p.relative_to(DEV))
    return sorted(set(out))


def rebuild(enabled=True):
    if not enabled:
        print("  （跳过重建）")
        return
    # 问卷常量 → 页面。纯本地生成，不联网、不碰数据库、不会触发大模型重判。
    for script in ("extract_questionnaire.py", "build_fair_page.py"):
        cmd = [sys.executable, "-X", "utf8", str(HERE / script)]
        # 这两个生成器内部用「相对 cwd 的路径」读 _handoff/ 下的问卷源文件，
        # 必须在开发根目录跑，不能设在 ent_tools/ 下。
        r = subprocess.run(cmd, cwd=str(DEV), capture_output=True, text=True)
        if r.returncode != 0:
            print("  ✗ 重建失败：%s\n%s" % (script, (r.stderr or r.stdout)[-800:]))
            raise SystemExit(1)
        print("  重建 %s ✓" % script)


def compare(target: Path, rels):
    same, diff, missing = [], [], []
    for rel in rels:
        src, dst = DEV / rel, target / rel
        if not dst.is_file():
            missing.append(rel)
        elif _sha(src) == _sha(dst):
            same.append(rel)
        else:
            diff.append(rel)
    return same, diff, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="开发正本 → 发布仓库 / 克隆验收目录")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件（默认）")
    ap.add_argument("--apply", action="store_true", help="重建页面并同步不一致的文件")
    ap.add_argument("--no-rebuild", action="store_true", help="--apply 时不重建页面")
    ap.add_argument("--target", action="append", default=[], help="追加目标目录（可多次）")
    ap.add_argument("--include-clone", action="store_true",
                    help="把克隆验收目录也当同步目标（默认不写它，保持干净 clone）")
    ap.add_argument("--include-payload", action="store_true",
                    help="显式同步 fair_payload.json（含当前画像快照，发布前先自检）")
    ap.add_argument("--include-db", action="store_true",
                    help="显式同步 careersafari.db（含本机测试记录，默认不带）")
    a = ap.parse_args(argv)

    if not DEV.is_dir() or not REPO.is_dir():
        print("✗ 目录布局不对：开发正本应在 %s，其下应有 careersafari-fair/" % DEV)
        raise SystemExit(1)

    targets = [REPO]
    if a.include_clone and CLONE.is_dir():
        targets.append(CLONE)
    for t in a.target:
        p = Path(t).resolve()
        if p.is_dir() and p not in targets:
            targets.append(p)

    if a.apply:
        print("== 重建构建产物 ==")
        rebuild(not a.no_rebuild)

    rels = source_files(a.include_payload, a.include_db)
    print("\n== 源文件清单：%d 个（密钥 / 本机库 / 调试脚本 / 个人报告页已排除）==" % len(rels))
    total_todo = 0
    for target in targets:
        same, diff, missing = compare(target, rels)
        todo = diff + missing
        total_todo += len(todo)
        print("\n[%s]" % target)
        print("  已一致 %d ｜ 内容不同 %d ｜ 目标缺失 %d" % (len(same), len(diff), len(missing)))
        for rel in todo[:30]:
            print("    %s  %s" % ("≠" if rel in diff else "+", rel))
        if len(todo) > 30:
            print("    … 另有 %d 个" % (len(todo) - 30))
        if a.apply and todo:
            for rel in todo:
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(DEV / rel, dst)
            print("  → 已复制 %d 个文件" % len(todo))
            same2, diff2, missing2 = compare(target, rels)
            print("  → 复核：已一致 %d ｜ 仍不一致 %d" % (len(same2), len(diff2) + len(missing2)))

    print("\n== 结果 ==")
    if a.apply:
        print("  同步完成。不要忘了发布：")
        print("    git -C %s status --short" % REPO)
        print("    git -C %s add -A && git -C %s commit -m '...' && git -C %s push origin main"
              % (REPO, REPO, REPO))
        print("  改动规则/词表后请另跑一次 refresh_all.py（体检 + parity + 三套测试）。")
        if CLONE.is_dir() and not a.include_clone:
            print("  验收实例保持干净：cd %s && git pull（不要往里复制文件）" % CLONE)
    else:
        print("  干跑：%d 处待同步。确认无误后加 --apply 执行。" % total_todo)
        if total_todo:
            print("  提示：--apply 会先重建 demo/fair.html，保证页面由当前生成器产出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
