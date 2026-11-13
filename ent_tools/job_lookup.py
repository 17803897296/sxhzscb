"""企业岗位查询链：数据库优先，未命中再走在线搜索。

用户搜索企业时的顺序：
    1. job_store.lookup(company)
    2. 命中 → 直接返回，data_source="db"
    3. 未命中 → portal.build(company) 在线搜索
    4. 在线结果 save 回数据库（Null 实现时是 no-op）
    5. 返回，data_source="online"

数据库 schema、TTL、公司别名、负缓存都由组员在 JobStore 里实现；
这里不决定数据库内容。
"""
from __future__ import annotations

import portal
from job_store import get_store

# 在线搜索允许无头 Edge 渲染 JS 招聘页；不登录、不带 Cookie。
portal.JS_RENDER = True


def _payload(company, item, data_source, db_hit, city="", limit=20):
    jobs = list(item.get("jobs") or [])
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    limit = max(1, min(50, limit))
    return {
        "company": company,
        "requested_city": city or "",
        "city": item.get("city", "未知"),
        "nature": item.get("nature", "未知"),
        "industry": item.get("industry", "未知"),
        "jobSource": item.get("jobSource", "未知"),
        "source": item.get("source", "未知"),
        "note": item.get("note", ""),
        "total": len(jobs),
        "jobs": jobs[:limit],
        "data_source": data_source,
        "db_hit": db_hit,
    }


def lookup_company(company, city="", limit=20):
    company = str(company or "").strip()
    if not company:
        raise ValueError("company 不能为空")

    store = get_store()

    # 1) 数据库优先
    hit = store.lookup(company)
    if hit is not None:
        return _payload(company, hit, "db", True, city, limit)

    # 2) 在线搜索
    item = portal.build(company)

    # 3) 写回数据库（占位实现 no-op；组员接真库后自动生效）
    try:
        store.save(company, item)
    except Exception:
        # 写库失败不阻塞用户查询
        pass

    return _payload(company, item, "online", False, city, limit)
