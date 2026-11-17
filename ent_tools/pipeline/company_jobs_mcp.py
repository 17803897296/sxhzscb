#!/usr/bin/env python3
"""company-jobs-mcp: 无登录的公司岗位 MCP。

只读公开网页：Bing 搜索 + 企业官网/学校就业网 + 可选无头 Edge 渲染。
不使用 BOSS/Cookie/登录态。

MCP stdio 传输：一行一个 JSON-RPC 消息。
"""
import json
import sys
import traceback
from pathlib import Path

# 脚本已移入 pipeline/：把 ent_tools 挂上 sys.path，兄弟模块照常导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_lookup

NAME = "company-jobs-mcp"
VERSION = "0.1.0"
PROTOCOL = "2024-11-05"


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


def search_company_jobs(company, city="", limit=20):
    company = str(company or "").strip()
    if not company:
        raise ValueError("company 不能为空")
    log("search_company_jobs: %s" % company)
    return job_lookup.lookup_company(company, city, limit)


TOOLS = [
    {
        "name": "search_company_jobs",
        "description": "搜索一家公司的公开岗位（不登录）。来源：企业官网、学校就业网、公开招聘页；JS 页面用无头 Edge 渲染。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "企业全称，例如 国元证券股份有限公司"},
                "city": {"type": "string", "description": "可选，期望城市，仅作为提示，不会过滤掉多城市岗位"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "最多返回多少条岗位，默认 20"},
            },
            "required": ["company"],
        },
    }
]


def call_tool(name, args):
    if name == "search_company_jobs":
        return search_company_jobs(
            args.get("company"),
            args.get("city", ""),
            args.get("limit", 20),
        )
    raise ValueError("未知工具: %s" % name)


def handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "version": VERSION},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = call_tool(name, args)
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            log("tool error: %s" % traceback.format_exc())
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": "错误: %s" % str(exc)}],
                    "isError": True,
                },
            }

    if msg_id is not None:
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": "Method not found: %s" % method},
        }
    return None


def main():
    log("%s %s started" % (NAME, VERSION))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log("bad json: %s" % line[:200])
            continue
        try:
            out = handle(msg)
        except Exception:
            log("fatal handler error: %s" % traceback.format_exc())
            continue
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
