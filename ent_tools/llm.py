# -*- coding: utf-8 -*-
"""共用的大模型调用层：读配置、发请求、解析。

**为什么要有这个文件**

同一段「从 env / .env 取 key、base、model，POST 到 /chat/completions，
取 choices[0].message.content」原来在 5 个模块里各抄了一遍
（`biz_fit` / `job_profile` / `unknown_infer` / `intent_semantics` / `_is_old_tmp`），
加起来 200 多行。改一个超时策略要改五处，漏一处就变成「某个模块悄悄不走新逻辑」。

`job_profile._call_raw` 的注释里写过原因：**不动 biz_fit 是为了别让它的缓存指纹失效**。
那个约束是真的（`cache_guard.file_fp` 按语法树算，改实现就作废 169 条画像缓存），
但它换来的是「永远不敢改」。2026-11-16 的处理办法是：

1. 传输层搬到这里，三个模块改成一行调用；
2. `cache_guard.REGISTRY` 里给这三个缓存加上 `llm.py` 依赖——以后改传输层
   该失效的照样失效，只是不用再改五个文件；
3. 搬运本身是一次性动作，指纹变了但**行为逐字未变**（同 prompt、同 model、
   同 temperature），所以缓存用 `_cache_retag.py` 改指纹而不是重跑模型。
   `tests/test_llm.py` 锁住「请求体与旧实现逐字相同」这件事。

`enrich_by_llm.call_model` 不在这里：它自带 `response_format` 降级重试、
120 秒超时和模板拼装，语义与其他几个不同，强并会把两套重试逻辑拧在一起。
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"


def load_env(base_dir=None):
    """读 base_dir/.env 成 dict。只认 `KEY=VALUE`，忽略注释与空行。"""
    cfg, path = {}, os.path.join(base_dir or os.path.dirname(os.path.abspath(__file__)),
                                 ".env")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            return {}
    return cfg


def resolve(cfg=None):
    """(key, base, model)。优先级：环境变量 > 传入 cfg > 内置默认。

    `cfg=None` 时自动读同目录 `.env`（2026-11-17 修）。不传 cfg 原来会落到
    「内置默认 base（智谱）+ 空 key」，打出去是 401 Unauthorized，看起来像
    key 失效，实际只是调用方忘了 `load_env()`——新写的脚本一不留神就中招，
    而且报错信息完全指错方向。生产四个模块都自己 load_env 后显式传 cfg，
    所以这个坑一直没暴露；现在兜住它。

    显式传 `{}` 仍是「什么都没配」（测试靠它钉内置默认值），语义不变。
    """
    cfg = load_env() if cfg is None else cfg
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or DEFAULT_BASE)
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    return key, base, model


def payload_of(prompt, cfg=None, temperature=0):
    """请求体。抽出来是为了让测试能直接比对「和旧实现逐字相同」，不必打网络。"""
    _, _, model = resolve(cfg)
    return {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature}


def chat(prompt, cfg=None, timeout=60, temperature=0):
    """发一次对话，返回模型原始文本（不解析）。

    调用方自己决定要不要 `parse_json`：`intent_semantics` 要 JSON、
    `biz_fit` 要文本自己再截，解析失败的处理各家不同，不在这一层做。

    `cfg=None` 时先读一次 `.env` 再算 key/base/model（见 `resolve`），读完
    缓存进局部变量传下去，避免 payload_of 二次读盘。
    """
    if cfg is None:
        cfg = load_env()
    key, base, _ = resolve(cfg)
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload_of(prompt, cfg, temperature),
                        ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]
