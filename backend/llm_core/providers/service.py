"""LLMService —— 对 Agent / API 层暴露统一入口

对外签名与 ModelManager 完全一致（load_model / unload_model / get_status /
generate），因此可以 drop-in 替换 `init_llm_gen_agent(...)` 与
`/api/chat/stream` 里的 get_model_manager()。

选择逻辑（决策：仅 OpenAI 兼容协议）：
  - provider == "openai" 且已配置 api_key  → 使用远程模型
  - 否则                                    → 使用本地模型

回退语义（决策 3）：远程若未产出任何 token：
  - 开启 fallback_to_local → 先提示「已回退本地模型」，再加载本地并流式生成
  - 未开启                 → 直接输出 error 事件
切到远程时（决策 4）：自动卸载本地模型权重，释放内存。
"""
import logging
from typing import AsyncGenerator, Optional

from backend.llm_core.model_manager.manager import get_model_manager
from backend.llm_core.providers.local_provider import LocalProvider
from backend.llm_core.providers.openai_provider import (
    OpenAICompatibleProvider,
    build_sse,
)
from backend.llm_core.providers.settings_store import (
    load_settings,
    public_view,
    save_settings,
)

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, manager=None):
        self._manager = manager or get_model_manager()
        self._local = LocalProvider(self._manager)
        self._remote: Optional[OpenAICompatibleProvider] = None
        self._active_name = "local"
        self._cfg = load_settings()
        self._refresh()

    # ------------------------------------------------------------ 配置

    def _build_remote(self) -> Optional[OpenAICompatibleProvider]:
        cfg = self._cfg
        if cfg.get("provider") == "openai" and cfg.get("api_key"):
            return OpenAICompatibleProvider(
                api_key=cfg.get("api_key", ""),
                base_url=cfg.get("base_url", ""),
                model=cfg.get("model", ""),
                temperature=cfg.get("temperature", 0.7),
                max_tokens=cfg.get("max_tokens", 1024),
            )
        return None

    def _refresh(self):
        self._remote = self._build_remote()
        self._active_name = "openai" if self._remote else "local"

    def reload_config(self) -> dict:
        self._cfg = load_settings()
        self._refresh()
        logger.info("LLM 配置已重载，active_provider=%s", self._active_name)
        return dict(self._cfg)

    async def save_config(self, data: dict) -> dict:
        self._cfg = save_settings(data)
        self._refresh()
        if self.is_remote():
            # 决策 4：切到远程后卸载本地权重
            result = await self._manager.unload_model()
            logger.info("已切换到远程模型，本地模型卸载结果: %s", result)
        return dict(self._cfg)

    def get_config(self) -> dict:
        return dict(self._cfg)

    def get_config_public(self) -> dict:
        view = public_view(self._cfg)
        view["active_provider"] = self._active_name
        return view

    # ------------------------------------------------------------ 状态

    @property
    def active_provider_name(self) -> str:
        return self._active_name

    def is_remote(self) -> bool:
        return self._active_name == "openai"

    def _active(self):
        return self._remote if self._remote else self._local

    def is_ready(self) -> bool:
        return bool(self._active().is_ready())

    async def load_model(self) -> dict:
        return await self._active().load_model()

    async def unload_model(self) -> dict:
        return await self._active().unload_model()

    async def get_status(self) -> dict:
        status = await self._local.get_status()
        status["active_provider"] = self._active_name
        status["remote"] = self.is_remote()
        if self.is_remote() and self._remote:
            status["remote_model"] = self._remote.model
            status["remote_base_url"] = self._remote.base_url
        return status

    # ------------------------------------------------------------ 生成

    async def generate(self, prompt: str, max_tokens: int = None,
                       system_prompt: str = None) -> AsyncGenerator[str, None]:
        """流式生成，产出 SSE 字符串（与 ModelManager.generate 一致）"""
        if not self.is_remote():
            async for event in self._local.generate(prompt, max_tokens, system_prompt):
                yield build_sse(event)
            return

        emitted = False
        error_msg: Optional[str] = None
        async for event in self._remote.generate(prompt, max_tokens, system_prompt):
            if event.get("status") == "error":
                # 错误延后输出：先看后续是否真有 token，再决定回退 / 报错
                error_msg = event.get("message") or "远程模型生成失败"
                continue
            if event.get("status") == "generating":
                emitted = True
            yield build_sse(event)

        if emitted:
            return

        # 未产出任何 token
        if self._cfg.get("fallback_to_local"):
            yield build_sse({"status": "info",
                             "message": "远程模型不可用，已自动回退到本地模型"})
            load_result = await self._local.load_model()
            if load_result.get("status") != "ok":
                yield build_sse({"status": "error",
                                 "message": load_result.get("message", "本地模型加载失败")})
                return
            async for event in self._local.generate(prompt, max_tokens, system_prompt):
                yield build_sse(event)
            return

        yield build_sse({"status": "error",
                         "message": error_msg or "远程模型未返回任何内容"})


# 全局单例工厂
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
