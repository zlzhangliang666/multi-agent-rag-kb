"""本地模型 Provider —— 包装 ModelManager

ModelManager.generate 产出的是 SSE 字符串（"data: {...}\\n\\n"），
Provider 层统一改为产出 **事件 dict**，由 LLMService 负责再序列化回 SSE，
这样「远程失败是否要回退本地」的判断可以在拿到事件之后再决定。
"""
import json
import logging
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


def parse_sse_event(raw: str) -> Optional[dict]:
    """把 ModelManager/Worker 产出的 SSE 字符串解析为事件 dict。

    非 data 行或 JSON 解析失败时返回 None。
    """
    if not isinstance(raw, str):
        return None
    line = raw.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class LocalProvider:
    """本地模型（Qwen2.5 + llama.cpp 子进程）"""

    name = "local"

    def __init__(self, manager):
        self._manager = manager

    def is_ready(self) -> bool:
        return bool(getattr(self._manager, "_loaded", False))

    async def load_model(self) -> dict:
        return await self._manager.load_model()

    async def unload_model(self) -> dict:
        return await self._manager.unload_model()

    async def get_status(self) -> dict:
        return await self._manager.get_status()

    async def generate(self, prompt: str, max_tokens: int = None,
                       system_prompt: str = None) -> AsyncGenerator[dict, None]:
        async for raw in self._manager.generate(prompt=prompt, max_tokens=max_tokens,
                                               system_prompt=system_prompt):
            event = parse_sse_event(raw)
            if event is not None:
                yield event
