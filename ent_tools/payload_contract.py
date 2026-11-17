# -*- coding: utf-8 -*-
"""payload 契约校验（评审 2026-11-18 Step 1）：零依赖的轻量 schema 校验。

契约文件 contract/payload.schema.json 是前后端、离线变体、测试、CI 共同
遵守的唯一接口描述。之前 payload 是「共同习惯」：前端 report.js 直接读、
assemble() 直接写、中间没有 schema 没有版本号，字段一改只能靠人记得两边
一起改。本模块把它变成显式契约：

- assemble() 写盘前校验，违约即炸（fail fast，不让坏 payload 出门）；
- GET /api/fair/payload 返回前校验（防盘上文件被手改/旧版本残留）；
- tests/test_payload_contract.py 把「少字段/改类型」钉进 CI。

兼容规则：加字段不违约（前端忽略未知键）；删字段、改类型违约；
违约时递增 contract/payload.schema.json 的 version 并同步前端。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCHEMA_PATH = os.path.join(ROOT, "contract", "payload.schema.json")

# Python 类型 -> schema 里的类型名
_TYPE_NAMES = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
    type(None): "null",
}

SCHEMA_VERSION = 1


class PayloadContractError(Exception):
    """payload 违反契约。message 里带全部违约点，方便一次改齐。"""


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _check_type(value, decl, where, errors):
    """decl 形如 "string" / "object|null"；bool 是 int 的子类，先判。"""
    allowed = [s.strip() for s in decl.split("|")]
    t = _TYPE_NAMES[type(value)]
    if t == "boolean" and "boolean" not in allowed:
        t = "integer" if isinstance(value, int) else t
    if t == "integer" and "integer" not in allowed and "number" in allowed:
        t = "number"   # int 是 number 的合法子集
    if t not in allowed:
        errors.append("%s: 期望 %s，实际 %s(%r)" % (where, decl, t, str(value)[:30]))


def validate(payload):
    """按契约校验 payload，返回错误清单（空列表 = 通过）。"""
    schema = load_schema()
    errors = []

    sv = payload.get("schema_version")
    if sv is None:
        errors.append("顶层缺 schema_version（本契约要求 = %d）" % schema["version"])
    elif sv != schema["version"]:
        errors.append("schema_version=%r 与契约 version=%d 不一致；"
                      "契约演进时两边要同步递增" % (sv, schema["version"]))

    for key, decl in sorted(schema["top_level"]["required"].items()):
        if key == "schema_version":
            continue  # 上面已按版本号语义校验
        if key not in payload:
            errors.append("顶层缺必填键: %s" % key)
        else:
            _check_type(payload[key], decl, "顶层.%s" % key, errors)

    rows = payload.get("rows") or []
    row_schema = schema["row"]["required"]
    for i, row in enumerate(rows):
        for key, decl in sorted(row_schema.items()):
            if key not in row:
                errors.append("rows[%d](%s) 缺必填键: %s" % (i, row.get("name", "?"), key))
            else:
                _check_type(row[key], decl, "rows[%d].%s" % (i, key), errors)
        # 结构不变式：state 的取值域（三态分级是匹配端的基本约定）
        if row.get("state") not in ("hit", "inferred", "miss"):
            errors.append("rows[%d].state=%r 不在 {hit,inferred,miss}" % (i, row.get("state")))
    return errors


def ensure_valid(payload):
    """违约即抛 PayloadContractError（带全部违约点，方便一次改齐）。"""
    errors = validate(payload)
    if errors:
        raise PayloadContractError(
            "payload 违反契约 v%d（%d 处）:\n  %s"
            % (SCHEMA_VERSION, len(errors), "\n  ".join(errors[:20])))
