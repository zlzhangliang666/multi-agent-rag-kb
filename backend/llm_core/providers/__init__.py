"""LLM Provider 层

把「本地模型（llama.cpp 子进程）」与「OpenAI 兼容远程模型」抽象为同一接口，
由 LLMService 统一对外提供与 ModelManager 完全一致的
load_model / unload_model / get_status / generate 签名，
从而在 Agent 与 API 层实现零改动替换。

    local_provider.py   本地模型（包装 ModelManager）
    openai_provider.py  远程模型（OpenAI 兼容 /chat/completions 流式）
    service.py          LLMService：按配置选择 provider，并处理回退
    settings_store.py   配置持久化（SQLite 单行）+ API Key 脱敏
"""
from backend.llm_core.providers import settings_store
from backend.llm_core.providers.service import LLMService, get_llm_service

__all__ = ["LLMService", "get_llm_service", "settings_store"]
