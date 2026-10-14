"""LLM Provider 层单测

覆盖：
  - 配置脱敏（mask_key / public_view）
  - base_url 规整、SSE 解析、请求体构造
  - LLMService 的 provider 选择与「远程失败 → 报错 / 回退本地」语义

全部用假对象 + asyncio.run 驱动，不联网、不落库、不加载模型。
"""
import asyncio

import pytest

from backend.llm_core.providers.local_provider import LocalProvider, parse_sse_event
from backend.llm_core.providers.openai_provider import (
    OpenAICompatibleProvider,
    build_sse,
    iter_openai_sse,
    normalize_base_url,
)
from backend.llm_core.providers.service import LLMService
from backend.llm_core.providers.settings_store import mask_key, public_view


def run(coro):
    return asyncio.run(coro)


async def collect(agen):
    return [item async for item in agen]


# ---------------------------------------------------------------- 配置脱敏

def test_mask_key():
    assert mask_key("") == ""
    assert mask_key("abcd") == "****"
    assert mask_key("sk-1234567890abcdef") == "*" * 15 + "cdef"


def test_public_view_hides_plain_key():
    view = public_view({
        "provider": "openai", "api_key": "sk-secret-1234", "base_url": "https://x/v1",
        "model": "gpt-4o-mini", "temperature": 0.5, "max_tokens": 256,
        "fallback_to_local": True,
    })
    assert "api_key" not in view
    assert view["api_key_masked"].endswith("1234")
    assert "sk-secret" not in view["api_key_masked"]
    assert view["has_api_key"] is True
    assert view["fallback_to_local"] is True


def test_public_view_without_key():
    view = public_view({"provider": "local", "api_key": ""})
    assert view["has_api_key"] is False
    assert view["api_key_masked"] == ""


# ---------------------------------------------------------------- base_url

def test_normalize_base_url():
    assert normalize_base_url("") == "https://api.openai.com/v1"
    assert normalize_base_url("  https://api.deepseek.com/v1/  ") == "https://api.deepseek.com/v1"
    assert normalize_base_url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"
    assert normalize_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"


# ---------------------------------------------------------------- SSE 解析

def test_iter_openai_sse_tokens_and_done():
    lines = [
        "",
        ": keep-alive",
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{}}]}',
        "data: [DONE]",
    ]
    events = run(collect(iter_openai_sse(lines)))
    assert events == [
        {"status": "generating", "token": "你"},
        {"status": "generating", "token": "好"},
    ]


def test_iter_openai_sse_error_object():
    lines = ['data: {"error":{"message":"invalid api key"}}']
    events = run(collect(iter_openai_sse(lines)))
    assert events == [{"status": "error", "message": "invalid api key"}]


def test_parse_sse_event_from_local():
    assert parse_sse_event('data: {"status": "generating", "token": "hi"}\n\n') == {
        "status": "generating", "token": "hi"
    }
    assert parse_sse_event("event: ping\n\n") is None
    assert parse_sse_event("data: not-json\n\n") is None
    assert parse_sse_event(None) is None


def test_build_sse_keeps_chinese_readable():
    raw = build_sse({"status": "generating", "token": "你好"})
    assert raw.startswith("data: ")
    assert raw.endswith("\n\n")
    assert "你好" in raw  # ensure_ascii=False


# ------------------------------------------------------------ 请求体构造

def test_provider_payload():
    p = OpenAICompatibleProvider(api_key="sk-x", base_url="https://api.openai.com/v1/",
                                 model="gpt-4o-mini", temperature=0.3, max_tokens=100)
    assert p.endpoint == "https://api.openai.com/v1/chat/completions"
    payload = p._payload("问题", system_prompt="你是助手", max_tokens=50)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["stream"] is True
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 50        # 入参覆盖默认
    assert payload["messages"] == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "问题"},
    ]
    # 无 system_prompt 时不应出现 system 消息
    assert len(p._payload("q")["messages"]) == 1
    assert p._headers()["Authorization"] == "Bearer sk-x"


def test_provider_error_message_hints():
    assert "401" in OpenAICompatibleProvider._error_message(401, "")
    assert "Base URL" in OpenAICompatibleProvider._error_message(404, "")
    assert "429" in OpenAICompatibleProvider._error_message(429, "")
    assert "500" in OpenAICompatibleProvider._error_message(500, "boom")


# ------------------------------------------------------ LLMService 选择逻辑

def _service(cfg, remote=None):
    """绕过数据库，直接构造用于测试的 service 实例。"""
    svc = LLMService.__new__(LLMService)
    svc._cfg = cfg
    svc._manager = FakeManager()
    svc._local = FakeLocal()
    svc._remote = remote
    svc._active_name = "openai" if remote else "local"
    return svc


def test_build_remote_requires_provider_and_key():
    svc = LLMService.__new__(LLMService)
    svc._cfg = {"provider": "local", "api_key": "sk-x"}
    assert svc._build_remote() is None
    svc._cfg = {"provider": "openai", "api_key": ""}
    assert svc._build_remote() is None
    svc._cfg = {"provider": "openai", "api_key": "sk-x", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini"}
    provider = svc._build_remote()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "gpt-4o-mini"


def test_local_generate_passthrough():
    svc = _service({"provider": "local", "fallback_to_local": False})
    out = run(collect(svc.generate("你好")))
    assert out == [
        build_sse({"status": "generating", "token": "本"}),
        build_sse({"status": "generating", "token": "地"}),
    ]


def test_remote_generate_streams_tokens():
    remote = FakeRemote([{"status": "generating", "token": "远"},
                         {"status": "generating", "token": "程"},
                         {"status": "done"}])
    svc = _service({"provider": "openai", "fallback_to_local": False}, remote=remote)
    out = run(collect(svc.generate("你好")))
    assert out == [
        build_sse({"status": "generating", "token": "远"}),
        build_sse({"status": "generating", "token": "程"}),
        build_sse({"status": "done"}),
    ]


def test_remote_failure_without_fallback_errors():
    remote = FakeRemote([{"status": "error", "message": "远程模型连接失败：timeout"}])
    svc = _service({"provider": "openai", "fallback_to_local": False}, remote=remote)
    out = run(collect(svc.generate("你好")))
    assert len(out) == 1
    assert '"status": "error"' in out[0]
    assert "远程模型连接失败" in out[0]


def test_remote_failure_with_fallback_uses_local():
    remote = FakeRemote([{"status": "error", "message": "boom"}])
    svc = _service({"provider": "openai", "fallback_to_local": True}, remote=remote)
    out = run(collect(svc.generate("你好")))
    # 第一帧为回退提示，其后为本地 token
    assert '"status": "info"' in out[0]
    assert "回退" in out[0]
    assert out[1:] == [
        build_sse({"status": "generating", "token": "本"}),
        build_sse({"status": "generating", "token": "地"}),
    ]


def test_remote_failure_with_fallback_but_local_load_fails():
    remote = FakeRemote([{"status": "error", "message": "boom"}])
    svc = _service({"provider": "openai", "fallback_to_local": True}, remote=remote)
    svc._local = FakeLocal(load_status="error")
    out = run(collect(svc.generate("你好")))
    assert '"status": "info"' in out[0]
    assert '"status": "error"' in out[1]
    assert "本地模型加载失败" in out[1]


def test_status_reports_active_provider():
    svc = _service({"provider": "local", "fallback_to_local": False})
    status = run(svc.get_status())
    assert status["active_provider"] == "local"
    assert status["remote"] is False

    remote = FakeRemote([])
    svc2 = _service({"provider": "openai", "fallback_to_local": False}, remote=remote)
    status2 = run(svc2.get_status())
    assert status2["active_provider"] == "openai"
    assert status2["remote"] is True
    assert status2["remote_model"] == "fake-model"


def test_is_ready_delegates_to_active_provider():
    svc = _service({"provider": "local", "fallback_to_local": False})
    assert svc.is_ready() is True          # FakeLocal.is_ready() -> True
    remote = FakeRemote([])
    svc2 = _service({"provider": "openai", "fallback_to_local": False}, remote=remote)
    assert svc2.is_ready() is True         # FakeRemote.is_ready() -> True


# ---------------------------------------------------------------- 假对象

class FakeManager:
    def __init__(self):
        self.load_calls = 0
        self.unload_calls = 0

    async def load_model(self):
        self.load_calls += 1
        return {"status": "ok", "message": "loaded"}

    async def unload_model(self):
        self.unload_calls += 1
        return {"status": "ok", "message": "unloaded"}

    async def get_status(self):
        return {"loaded": False, "generating": False, "worker_alive": True,
                "memory_mb": 0, "error": None}


class FakeLocal:
    def __init__(self, load_status="ok"):
        self._load_status = load_status

    def is_ready(self):
        return True

    async def load_model(self):
        if self._load_status == "error":
            return {"status": "error", "message": "本地模型加载失败"}
        return {"status": "ok", "message": "loaded"}

    async def unload_model(self):
        return {"status": "ok", "message": "unloaded"}

    async def get_status(self):
        return {"loaded": False, "generating": False, "worker_alive": True, "memory_mb": 0}

    async def generate(self, prompt, max_tokens=None, system_prompt=None):
        yield {"status": "generating", "token": "本"}
        yield {"status": "generating", "token": "地"}


class FakeRemote:
    name = "openai"
    model = "fake-model"
    base_url = "https://fake/v1"

    def __init__(self, events):
        self._events = list(events)

    def is_ready(self):
        return True

    async def load_model(self):
        return {"status": "ok", "message": "远程无需加载"}

    async def unload_model(self):
        return {"status": "ok", "message": "远程无需卸载"}

    async def get_status(self):
        return {"loaded": True, "generating": False, "worker_alive": False, "remote": True}

    async def generate(self, prompt, max_tokens=None, system_prompt=None):
        for event in self._events:
            yield event
