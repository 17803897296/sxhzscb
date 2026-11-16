# -*- coding: utf-8 -*-
"""`llm.py` 与它取代的那几份 `_call_raw` 是否**逐字等价**。

这个测试存在的唯一理由：合并传输层时，缓存被「改指纹」而不是「重跑模型」，
所以必须证明「同 prompt + 同 model + 同 temperature 发出去的请求一模一样」。
只要有一条不等价，迁移缓存就是在拿旧结论冒充新结论。

参照物是 biz_fit / job_profile / unknown_infer 原来那三份完全相同的内联实现，
这里把它原样重写一份当基准，再和 `llm.chat` 发出的请求逐字段比。
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import llm


class _Recorder:
    """替身 urlopen：记下 Request 的 URL / body / headers，返回合法响应。"""

    def __init__(self, content="ok"):
        self.url = None
        self.body = None
        self.headers = None
        self._content = content

    def __call__(self, req, timeout=None):
        self.url = req.full_url
        self.body = req.data
        self.headers = dict(req.headers)
        body = {"choices": [{"message": {"content": self._content}}]}
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def legacy_call_raw(cfg, prompt, timeout, urlopen):
    """旧实现逐字复制（biz_fit.py / job_profile.py / unknown_infer.py 曾经的那份）。"""
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0}
    import urllib.request
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]


def _clear_env():
    return patch.dict(os.environ, {}, clear=False)


class EquivalenceTests(unittest.TestCase):
    """三种配置来源都要对上：全默认 / .env 给值 / 环境变量覆盖。"""

    def _compare(self, cfg, env):
        prompt = "把这段招聘页抽成 JSON：{...}"
        keep = {k: os.environ.get(k) for k in
                ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")}
        try:
            for k in keep:
                os.environ.pop(k, None)
            os.environ.update(env)

            old = _Recorder("旧")
            legacy_call_raw(cfg, prompt, 33, old)
            new = _Recorder("新")
            with patch("urllib.request.urlopen", new):
                got = llm.chat(prompt, cfg, timeout=33)
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.assertEqual(got, "新")
        self.assertEqual(old.url, new.url, "URL 不一致")
        self.assertEqual(old.body, new.body, "请求体不一致")
        self.assertEqual(old.headers.get("Authorization"),
                         new.headers.get("Authorization"), "鉴权头不一致")
        self.assertEqual(old.headers.get("Content-type") or old.headers.get("Content-Type"),
                         new.headers.get("Content-type") or new.headers.get("Content-Type"),
                         "Content-Type 不一致")

    def test_all_defaults(self):
        self._compare({}, {})

    def test_values_from_dotenv(self):
        self._compare({"DEEPSEEK_API_KEY": "sk-from-file",
                       "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                       "DEEPSEEK_MODEL": "deepseek-chat"}, {})

    def test_env_overrides_dotenv(self):
        self._compare({"DEEPSEEK_API_KEY": "sk-file", "DEEPSEEK_MODEL": "glm-4-flash"},
                      {"DEEPSEEK_API_KEY": "sk-env", "DEEPSEEK_MODEL": "deepseek-chat"})

    def test_trailing_slash_on_base_is_normalized(self):
        self._compare({"DEEPSEEK_BASE_URL": "https://api.deepseek.com/"}, {})


class ResolveTests(unittest.TestCase):
    def _no_env(self):
        return patch.dict(os.environ, {}, clear=False)

    def test_defaults(self):
        with self._no_env():
            for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
                os.environ.pop(k, None)
            key, base, model = llm.resolve({})
            self.assertEqual(key, "")
            self.assertEqual(base, "https://open.bigmodel.cn/api/paas/v4")
            self.assertEqual(model, "glm-4-flash")

    def test_temperature_is_the_only_difference_from_intent_semantics(self):
        """intent_semantics 用 0.2，其余用 0——这里显式钉住，别哪天被顺手改掉。"""
        self.assertEqual(llm.payload_of("x", {}, 0)["temperature"], 0)
        self.assertEqual(llm.payload_of("x", {}, 0.2)["temperature"], 0.2)


class LoadEnvTests(unittest.TestCase):
    def test_parses_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
                f.write('# 注释\nDEEPSEEK_API_KEY="sk-abc"\n\nDEEPSEEK_MODEL=deepseek-chat\n')
            cfg = llm.load_env(d)
        self.assertEqual(cfg["DEEPSEEK_API_KEY"], "sk-abc")
        self.assertEqual(cfg["DEEPSEEK_MODEL"], "deepseek-chat")
        self.assertNotIn("# 注释", cfg)

    def test_missing_file_is_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(llm.load_env(d), {})


if __name__ == "__main__":
    unittest.main()
