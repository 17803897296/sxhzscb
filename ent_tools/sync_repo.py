# -*- coding: utf-8 -*-
"""
sync_repo.py · 架构纪律工具：把「开发正本」同步到「发布仓库 / 克隆验收目录」。

背景（2026-11-15）：过去靠手写 cp 同步，漏一项就出现「克隆页是旧副本」
「改了代码没生效」「测试记录写进另一个实例的库」。这份清单是唯一的同步规则，
以后不要再手写 cp。

    python -X utf8 ent_tools/sync_repo.py --check          # 干跑：列出所有不一致
    python -X utf8 ent_tools/sync_repo.py --apply          # 重建页面 + 同步（含 payload 隐私自检）
    python -X utf8 ent_tools/sync_repo.py --apply --target <目录>   # 追加目标
    python -X utf8 ent_tools/sync_repo.py --apply --no-payload      # 显式不带报告数据

约定见仓库根 ARCHITECTURE.md。绝不自动 git commit / push，那一步由人决定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../default-workspace/ent_tools
DEV = HERE.parent                              # .../default-workspace  ← 唯一开发正本
REPO = DEV / "careersafari-fair"               # 发布仓库（GitHub 的工作副本）
CLONE = DEV.parent / "sxhzscb-deploy"          # 从 GitHub 克隆的验收实例（无常驻服务）

# 2026-11-17 起的新布局：ent_tools 已并入发布仓库本体（careersafari-fair 既是正本也是
# git 仓库）。旧布局（正本与仓库分居两个目录）下 REPO 才单独存在；布局一变就自适应。
if not (REPO / ".git").is_dir() and (DEV / ".git").is_dir():
    REPO = DEV

# 需要同步的「活源文件」。改了这些文件，发布/验收两边必须跟上。
GLOBS = [
    ("ent_tools", "*.py"),
    ("ent_tools", "*.md"),
    ("ent_tools", ".env.example"),
    # 页面里内联的三段前端源码（2026-11-16 从 build_fair_page.py 抽出来的）。
    # 漏了这里，clone 出来的实例跑 build_fair_page.py 会直接报文件不存在。
    ("ent_tools/js", "*.js"),
    ("ent_tools/data/test_users", "*.json"),
    # 只读种子：活缓存（*_cache.json / *_live.json / hit_verdicts.json 等）不入库，
    # clone 后由 cache_guard.restore_seeds() 从这些 .seed 复制出活文件。
    # 忘了同步种子 → clone 出来的实例首屏仍能读 payload，但一重建就退化成规则匹配。
    ("ent_tools/data", "*.seed"),
    ("demo", "*.js"),
    ("demo", "questionnaire.json"),
]

# demo 下的 HTML 用白名单：目录里混着「排行_李炳成」「采集数据_周航」这类
# 含真实姓名/个人信息的页面，靠黑名单排不干净，绝不能自动同步进公开仓库。
DEMO_HTML = [
    "collect.html", "fair.html", "index.html", "match.html", "portal.html",
    "start.html", "test_users.html", "tests.html",
]

# 测试资产：这些 _ 开头的文件不是一次性调试脚本，而是 refresh_all 第 7 步
# 要用的 parity / 回归测试与夹具，必须在仓库里且随改动同步，否则 clone 跑门禁会用到旧版。
TEST_ASSETS = [
    "ent_tools/_js_matchintent.js",
    "ent_tools/_rule_regress.py",
    "ent_tools/_schema_consts.json",
    "ent_tools/_test_match.js",
    "ent_tools/_test_parity.js",
    "ent_tools/data/_parity.json",   # _test_parity.js 直接读它，缺了 clone 跑不了门禁
    "ent_tools/_is_old_tmp.py",      # _rule_regress.py 的旧规则基线模块
    "ent_tools/_js_ab.py",           # 无头渲染 A/B（耗时 + 正文长度 + 岗位词命中）
    "ent_tools/data/_v5_verify.json",  # _rule_regress.py 的验证夹具
]

PAYLOAD_REL = "ent_tools/data/fair_payload.json"
SENSITIVE_KEYS = ("name", "school", "phone", "email", "mobile", "id_card", "address")

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


TEXT_EXT = {".py", ".md", ".html", ".js", ".json", ".css", ".txt"}


def _sha(p: Path) -> str:
    """文本按 LF 归一后哈希：git 的 CRLF/LF 转换不算内容差异，
    否则仓库/clone 每次都会显示「不一致」并反复复制同一份内容。"""
    h = hashlib.sha256()
    if p.suffix.lower() in TEXT_EXT:
        data = p.read_bytes().replace(b"\r\n", b"\n")
        h.update(data)
        return h.hexdigest()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_files(include_payload=True):
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
    for name in TEST_ASSETS:
        p = DEV / name
        if p.is_file():
            out.append(p.relative_to(DEV))
    if include_payload:
        p = DEV / PAYLOAD_REL
        if p.is_file():
            out.append(p.relative_to(DEV))
    # careersafari.db 是运行时活库，永不入库；仓库只放只读 .seed 供首次启动初始化。
    return sorted(set(out))


def payload_privacy():
    """发布前自检：payload 含当前生效画像，不能把姓名/学校/电话/邮箱带进公开仓库。

    返回 (ok, reasons)。不通过时该文件会被跳过，并在输出里明确提示两端将不同源。
    """
    try:
        d = json.loads((DEV / PAYLOAD_REL).read_text(encoding="utf-8"))
    except Exception as exc:
        return False, ["无法解析 (%s)" % exc]
    prof = d.get("profile") or {}
    why = [k for k in SENSITIVE_KEYS if k in prof]
    text = json.dumps(prof, ensure_ascii=False)
    if re.search(r"1[3-9]\d{9}", text):
        why.append("疑似手机号")
    if re.search(r"[\w.+-]+@[\w.-]+\.\w+", text):
        why.append("疑似邮箱")
    return (not why), why


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


def sync_profile_seed():
    """让仓库的只读种子库带上与发布 payload 相同的生效画像。

    fair_server 的 /api/fair/payload 有安全网：payload.active_fair_id 与本地库
    生效画像不一致就立刻 fair_bridge.build() 重建。只同步 payload、不配套种子库，
    clone 一请求就会把发布快照覆盖掉（2026-11-15 实测 79 家 -> 61 家）。
    种子库同时清空历史测试画像、脱敏 identity，避免把测试记录带进公开仓库。
    """
    import sqlite3
    src = DEV / "ent_tools/data/careersafari.db"
    seed = REPO / "ent_tools/data/careersafari.db.seed"
    if not (src.is_file() and seed.is_file()):
        return "跳过（活库或种子缺失）"
    try:
        ro = sqlite3.connect(str(src))
        fid = (ro.execute(
            "select fair_id from fair_active where id='current'").fetchone() or [None])[0]
        row = ro.execute(
            "select fair_id,created_at,persona,completeness,document_json "
            "from fair_profiles where fair_id=?", (fid,)).fetchone()
        ro.close()
        if not row:
            return "跳过（找不到生效画像 %s）" % fid
        doc = json.loads(row[4])
        doc["identity"] = {k: ("" if k in SENSITIVE_KEYS else v)
                           for k, v in (doc.get("identity") or {}).items()}
        clean_json = json.dumps(doc, ensure_ascii=False)
        db = sqlite3.connect(str(seed))
        cur_active = (db.execute(
            "select fair_id from fair_active where id='current'").fetchone() or [None])[0]
        cur = db.execute(
            "select document_json from fair_profiles where fair_id=?", (fid,)).fetchone()
        n_profiles = db.execute("select count(*) from fair_profiles").fetchone()[0]
        # 幂等：已配套就不重写二进制，避免每次发布都产生无意义的 diff
        if cur_active == fid and cur and cur[0] == clean_json and n_profiles == 1:
            db.close()
            return "生效画像 %s 已配套，无需改动" % fid
        db.execute("delete from fair_profiles")
        db.execute("insert into fair_profiles"
                   "(fair_id,created_at,persona,completeness,document_json) "
                   "values (?,?,?,?,?)",
                   (row[0], row[1], row[2], row[3], clean_json))
        db.execute("update fair_active set fair_id=?, updated_at=? where id='current'",
                   (fid, row[1]))
        db.commit()
        db.execute("vacuum")
        db.close()
        for suffix in ("-shm", "-wal"):
            f = Path(str(seed) + suffix)
            if f.exists():
                f.unlink()
        return "生效画像 %s 已配套（identity 脱敏，历史测试画像清空）" % fid
    except Exception as exc:
        return "失败：%s: %s" % (type(exc).__name__, exc)


def sync_ind_codes_to_seed():
    """把活库的行业码抄进只读种子库。

    为什么必须做：种子库原来是 145 家企业 0 个行业码，clone 下来跑 step_ind_code
    只能靠规则兜底 —— 同一家企业在开发机上判 chem、在新同学机器上判 metal，
    匹配结果就漂了，而且没人会想到是「码的来源不同」。码一旦定了就是建库资产，
    要跟库一起走。

    只抄 ind_code / ind_conf / ind_why 三个字段，不动其他列（别的列涉及脱敏，
    由 sync_profile_seed 负责）。幂等：全一致就不写，避免每次发布都产生 diff。
    """
    import sqlite3
    src = DEV / "ent_tools/data/careersafari.db"
    seed = REPO / "ent_tools/data/careersafari.db.seed"
    if not (src.is_file() and seed.is_file()):
        return "跳过（活库或种子缺失）"
    try:
        ro = sqlite3.connect(str(src))
        rows = {}
        for name, ij in ro.execute("select name, item_json from company_jobs"):
            try:
                it = json.loads(ij or "{}")
            except Exception:
                continue
            if it.get("ind_code"):
                rows[name] = (it["ind_code"], it.get("ind_conf") or "",
                              it.get("ind_why") or "")
        ro.close()
        if not rows:
            return "跳过（活库还没有行业码）"
        db = sqlite3.connect(str(seed))
        changed = 0
        for name, (code, conf, why) in rows.items():
            row = db.execute("select item_json from company_jobs where name=?",
                             (name,)).fetchone()
            if not row:
                continue
            try:
                it = json.loads(row[0] or "{}")
            except Exception:
                it = {}
            if (it.get("ind_code"), it.get("ind_conf"), it.get("ind_why")) == (code, conf, why):
                continue
            it["ind_code"], it["ind_conf"], it["ind_why"] = code, conf, why
            db.execute("update company_jobs set item_json=? where name=?",
                       (json.dumps(it, ensure_ascii=False), name))
            changed += 1
        if not changed:
            db.close()
            return "行业码已配套（%d 家），无需改动" % len(rows)
        db.commit()
        db.execute("vacuum")
        db.close()
        for suffix in ("-shm", "-wal"):
            f = Path(str(seed) + suffix)
            if f.exists():
                f.unlink()
        return "行业码已抄入种子：%d 家（共 %d 家有码）" % (changed, len(rows))
    except Exception as exc:
        return "失败：%s: %s" % (type(exc).__name__, exc)


def sync_jobs_seed():
    """把活库 company_jobs 整表同步进只读种子库。

    为什么必须做：build_fair_page 的内置企业数组直接读 company_jobs；
    种子是 clone/CI 里唯一的库。种子落后（比如岗位富化后没同步），
    冷克隆构建出的页面就和正本对不上 —— CI 的 git diff --exit-code 会红，
    本地 build 与发布 build 字节不一致。2026-11-17 实测：活库 1956 岗 /
    72.7k 要求字，种子 1108 岗 / 31.0k，正本页 1204.6 KB vs 克隆页 984.8 KB。

    只动 company_jobs 一张表（企业 + 岗位的公开业务数据，无 PII）；
    画像/命中的脱敏由 sync_profile_seed 负责。幂等：逐行比对，全一致不写。
    走 repository.connect 出口，不新增裸连接（sqlite 预算守卫盯着）。
    """
    from repository import connect as db_connect
    src = DEV / "ent_tools/data/careersafari.db"
    seed = REPO / "ent_tools/data/careersafari.db.seed"
    if not (src.is_file() and seed.is_file()):
        return "跳过（活库或种子缺失）"
    try:
        with db_connect(src, row_factory=False) as ro:
            rows = ro.execute(
                "select name, item_json, updated_at from company_jobs order by name"
            ).fetchall()
        if not rows:
            return "跳过（活库 company_jobs 为空）"
        with db_connect(seed, row_factory=False) as db:
            changed = 0
            for name, ij, upd in rows:
                row = db.execute("select item_json from company_jobs where name=?",
                                 (name,)).fetchone()
                if row is None:
                    db.execute("insert into company_jobs(name, item_json, updated_at) "
                               "values(?,?,?)", (name, ij, upd))
                    changed += 1
                elif row[0] != ij:
                    db.execute("update company_jobs set item_json=?, updated_at=? "
                               "where name=?", (ij, upd, name))
                    changed += 1
            # 活库里删掉的公司从种子一并删除，保持同构
            names = [r[0] for r in rows]
            q = ",".join("?" * len(names))
            changed += db.execute(
                "delete from company_jobs where name not in (%s)" % q, names).rowcount
            if changed:
                # vacuum 不能在事务里跑：先提交，再整理。零变更不 vacuum，
                # 保证字节级幂等（发布流程反复跑不产生假 diff）。
                db.commit()
                db.execute("vacuum")
        if not changed:
            return "company_jobs 已配套（%d 家），无需改动" % len(rows)
        for suffix in ("-shm", "-wal"):
            f = Path(str(seed) + suffix)
            if f.exists():
                f.unlink()
        return "company_jobs 已同步进种子：%d 行变更（共 %d 家）" % (changed, len(rows))
    except Exception as exc:
        return "失败：%s: %s" % (type(exc).__name__, exc)


def sync_llm_match_seed():
    """把活库 llm_match 整表同步进只读种子库。

    为什么必须做：llm_match 是「企业 × 匹配结论」（state/matched_jobs/reason），
    匹配段的原始判定层。种子落后一期，克隆重建的企业集合就漂移 ——
    2026-11-18 实测：活库与种子 73 家 state 不同（55 所活库 hit / 种子 miss），
    克隆首开重建 34 行 vs 正本 42 行，缺 20 家多 12 家。

    表内容是企业公开业务匹配数据，无 PII；画像脱敏仍由 sync_profile_seed
    负责。幂等：逐行比对，全一致不写、不 vacuum（字节级幂等）。
    走 repository.connect 出口，不新增裸连接（sqlite 预算守卫盯着）。
    """
    from repository import connect as db_connect
    src = DEV / "ent_tools/data/careersafari.db"
    seed = REPO / "ent_tools/data/careersafari.db.seed"
    if not (src.is_file() and seed.is_file()):
        return "跳过（活库或种子缺失）"
    cols = ("name, state, priority, matched_jobs, reason, gaps, "
            "verify_question, has_jd, generated_at")
    try:
        with db_connect(src, row_factory=False) as ro:
            rows = ro.execute("select %s from llm_match order by name" % cols).fetchall()
        if not rows:
            return "跳过（活库 llm_match 为空）"
        with db_connect(seed, row_factory=False) as db:
            changed = 0
            for row in rows:
                old = db.execute(
                    "select state, priority, matched_jobs, reason, gaps, "
                    "verify_question, has_jd, generated_at from llm_match "
                    "where name=?", (row[0],)).fetchone()
                if old is None:
                    db.execute("insert into llm_match(%s) values(?,?,?,?,?,?,?,?,?)"
                               % cols, row)
                    changed += 1
                elif tuple(old) != tuple(row[1:]):
                    db.execute("update llm_match set state=?, priority=?, "
                               "matched_jobs=?, reason=?, gaps=?, "
                               "verify_question=?, has_jd=?, generated_at=? "
                               "where name=?", row[1:] + (row[0],))
                    changed += 1
            names = [r[0] for r in rows]
            q = ",".join("?" * len(names))
            changed += db.execute(
                "delete from llm_match where name not in (%s)" % q, names).rowcount
            if changed:
                db.commit()
                db.execute("vacuum")
        if not changed:
            return "llm_match 已配套（%d 家），无需改动" % len(rows)
        for suffix in ("-shm", "-wal"):
            f = Path(str(seed) + suffix)
            if f.exists():
                f.unlink()
        return "llm_match 已同步进种子：%d 行变更（共 %d 家）" % (changed, len(rows))
    except Exception as exc:
        return "失败：%s: %s" % (type(exc).__name__, exc)


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


def _release(ver, apply, no_rebuild, no_payload):
    """按版本号发版：先同步（可选），再在发布仓库 HEAD 打 annotated tag。

    纪律：只创建本地 tag，绝不自动 commit / push——那一步仍由人决定。
    tag 钉在已提交的 HEAD 上；有未提交改动就拒绝打，避免「tag 和仓库内容对不上」。
    """
    if not re.match(r"^v\d+\.\d+(\.\d+)?$", ver):
        print("✗ 版本号格式应为 vX.Y / vX.Y.Z（v3.0 起语义化递增）：%s" % ver)
        raise SystemExit(1)
    if apply:
        main(["--apply"] + (["--no-rebuild"] if no_rebuild else [])
             + (["--no-payload"] if no_payload else []))
    if not (REPO / ".git").is_dir():
        print("✗ %s 不是 git 仓库" % REPO)
        raise SystemExit(1)
    exists = subprocess.run(["git", "-C", str(REPO), "tag", "--list", ver],
                            capture_output=True, text=True).stdout.strip()
    if exists:
        print("⚠ tag %s 已存在，不重复打。" % ver)
        return
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("⚠ 发布仓库有未提交改动，先 commit 再发版（tag 只钉在已提交的 HEAD 上）：")
        for line in dirty.splitlines()[:10]:
            print("   ", line)
        raise SystemExit(1)
    subject = subprocess.run(
        ["git", "-C", str(REPO), "log", "-1", "--format=%s"],
        capture_output=True, text=True).stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["git", "-C", str(REPO), "tag", "-a", ver,
                        "-m", "%s：%s" % (ver, subject)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("✗ 打 tag 失败：%s" % (r.stderr or "").strip())
        raise SystemExit(1)
    print("✓ 已打 tag %s（钉在 %s：%s）" % (ver, head, subject))
    print("  推送（由人决定，本工具绝不自动 push）：")
    print("    git -C %s push origin main" % REPO)
    print("    git -C %s push origin %s" % (REPO, ver))
    print("  旧版本永远留着：GitHub 页面切 Tags 可浏览/下载 zip，"
          "git checkout %s 可回退。" % ver)


def main(argv=None):
    ap = argparse.ArgumentParser(description="开发正本 → 发布仓库 / 克隆验收目录")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件（默认）")
    ap.add_argument("--apply", action="store_true", help="重建页面并同步不一致的文件")
    ap.add_argument("--no-rebuild", action="store_true", help="--apply 时不重建页面")
    ap.add_argument("--target", action="append", default=[], help="追加目标目录（可多次）")
    ap.add_argument("--include-clone", action="store_true",
                    help="把克隆验收目录也当同步目标（默认不写它，保持干净 clone）")
    ap.add_argument("--include-payload", action="store_true",
                    help="兼容旧用法；现在 payload 默认就随发布同步")
    ap.add_argument("--no-payload", action="store_true",
                    help="本次不带 fair_payload.json（两端数据会不同源）")
    ap.add_argument("--release", metavar="V", default=None,
                    help="同步后按版本号打 tag（如 --release v3.1）。"
                         "只创建本地 annotated tag，不自动 push——推送仍由人决定："
                         "git push origin main && git push origin <tag>")
    a = ap.parse_args(argv)

    if a.release:
        _release(a.release, a.apply, a.no_rebuild, a.no_payload)

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
        if not a.no_payload:
            print("== 种子库生效画像配套 ==")
            print("  " + sync_profile_seed())
            print("  " + sync_ind_codes_to_seed())
            print("  " + sync_jobs_seed())
            print("  " + sync_llm_match_seed())

    rels = source_files(not a.no_payload)
    # 数据同源是默认行为，但隐私优先：画像里出现姓名/学校/电话/邮箱就跳过 payload，
    # 绝不静默把个人信息推到公开仓库。
    if not a.no_payload:
        ok, why = payload_privacy()
        if ok:
            print("  fair_payload.json 隐私自检通过（无姓名/学校/电话/邮箱），随发布同步")
        else:
            rels = [r for r in rels
                    if str(r).replace("\\", "/") != PAYLOAD_REL]
            print("  ⚠ fair_payload.json 含敏感信息（%s），本次跳过；两端数据将不同源。"
                  % "、".join(why))
    print("\n== 源文件清单：%d 个（密钥 / 活库 / 调试脚本 / 个人报告页已排除）==" % len(rels))
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
