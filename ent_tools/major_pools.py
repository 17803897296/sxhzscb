# -*- coding: utf-8 -*-
"""专业类岗位池文件（major_job_pools.json）的读取与契约校验。

设计原则：
  1. **默认关闭**：不设 ENT_MAJOR_POOLS=1 时 enabled() 为 False，
     job_recommend 走原规则/联网路径，现有行为与测试一字不变。
  2. **可回退**：文件缺失、版本不符、某类没生成、校验不过 → 一律回退原路径，
     绝不因为一个数据文件把推荐打挂。
  3. **契约**：顶层 catalog_version 必须匹配目录文件；每个类 L1/L2 是
     [{title, why}] 列表；过一遍邻专业闸门（数据文件可能被人工改错）。

状态（2026-11-18）：采集端已接线，但**直接读文件**（job_recommend._class_table），
不走本模块的 enabled 开关——本模块保留为数据契约校验、统计与回退判断工具。
接线口径见 job_recommend.table_l1 / _table_trust：只收证据来自校招公告的 L1。
"""
import io
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CATALOG = os.path.join(_HERE, "data", "major_catalog_2025.json")
_POOLS = os.path.join(_HERE, "data", "major_job_pools.json")


def enabled():
    return os.environ.get("ENT_MAJOR_POOLS", "") == "1"


def _catalog_version():
    try:
        return json.load(io.open(_CATALOG, encoding="utf-8"))["catalog_version"]
    except Exception:
        return None


def _load():
    try:
        return json.load(io.open(_POOLS, encoding="utf-8"))
    except Exception:
        return None


def validate(d, catalog_ver=None):
    """校验池文件结构；返回 (ok, 错误列表)。零依赖，供测试直接调。"""
    errs = []
    if not isinstance(d, dict):
        return False, ["顶层不是对象"]
    cv = _catalog_version() if catalog_ver is None else catalog_ver
    if d.get("catalog_version") != cv:
        errs.append("catalog_version=%r 与目录 %r 不符" % (d.get("catalog_version"), cv))
    classes = d.get("classes")
    if not isinstance(classes, dict):
        return False, errs + ["缺 classes 对象"]
    for name, rec in classes.items():
        if rec.get("_error"):
            continue  # 生成失败的类不算违约，调用方回退原路径
        for tag in ("L1", "L2"):
            v = rec.get(tag)
            if not isinstance(v, list):
                errs.append("%s.%s 不是列表" % (name, tag))
                continue
            for c in v:
                if not isinstance(c, dict) or not isinstance(c.get("title"), str) \
                        or not c.get("title"):
                    errs.append("%s.%s 有非法条目" % (name, tag))
                    break
    return not errs, errs


def has(name):
    d = _load()
    if not d or d.get("catalog_version") != _catalog_version():
        return False
    rec = (d.get("classes") or {}).get(name)
    return bool(rec) and not rec.get("_error")


def pool_for(name, tier):
    """返回 (候选列表[{title,why}], 模式标签) 或 None（回退）。"""
    d = _load()
    if not d or d.get("catalog_version") != _catalog_version():
        return None
    rec = (d.get("classes") or {}).get(name)
    if not rec or rec.get("_error"):
        return None
    import job_recommend as J
    tag = "L1" if tier == 0 else "L2" if tier == 1 else None
    if not tag:
        return None
    items = rec.get(tag) or []
    # 闸门二次校验：数据文件可能被人工改错，不能信它自带干净
    items = [c for c in items if not J._neighbor_core_blocked(c["title"], name)]
    if not items:
        return None
    return [dict(c) for c in items], "pool-%s" % d.get("catalog_version")


def stats():
    d = _load() or {}
    classes = d.get("classes") or {}
    ok = [v for v in classes.values() if not v.get("_error")]
    return {
        "catalog_version": d.get("catalog_version"),
        "generated_at": d.get("generated_at"),
        "classes_total": len(classes),
        "classes_ok": len(ok),
        "classes_failed": len(classes) - len(ok),
        "jobs_L1": sum(len(v.get("L1") or []) for v in ok),
        "jobs_L2": sum(len(v.get("L2") or []) for v in ok),
    }
