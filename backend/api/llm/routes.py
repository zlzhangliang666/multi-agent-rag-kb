"""
LLM 配置 API —— 模型设置页后端

  GET  /api/llm/config   读取配置（api_key 脱敏，仅返回末 4 位 + 是否已配置）
  PUT  /api/llm/config   保存配置（api_key 传空串表示保持原值）
  POST /api/llm/test     用当前/临时配置测试远程模型连通性

安全约定：API Key 只存后端 SQLite，不写日志、不回传明文。
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.llm_core.providers.openai_provider import OpenAICompatibleProvider
from backend.llm_core.providers.service import get_llm_service

logger = logging.getLogger("api.llm")

router = APIRouter(prefix="/api/llm", tags=["模型设置"])


class LLMConfigRequest(BaseModel):
    provider: str = "local"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    fallback_to_local: bool = False


class LLMTestRequest(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 1


@router.get("/config", summary="读取 LLM 配置（脱敏）")
async def get_config():
    return get_llm_service().get_config_public()


@router.put("/config", summary="保存 LLM 配置")
async def update_config(req: LLMConfigRequest):
    service = get_llm_service()
    await service.save_config(req.model_dump())
    logger.info("LLM 配置已更新，provider=%s, active=%s",
                req.provider, service.active_provider_name)
    return service.get_config_public()


@router.post("/test", summary="测试远程模型连通性")
async def test_config(req: LLMTestRequest):
    service = get_llm_service()
    cfg = service.get_config()
    api_key = (req.api_key or "").strip() or cfg.get("api_key", "")
    if not api_key:
        return {"status": "error", "message": "请先填写 API Key"}

    provider = OpenAICompatibleProvider(
        api_key=api_key,
        base_url=req.base_url or cfg.get("base_url", ""),
        model=req.model or cfg.get("model", ""),
        temperature=0.0,
        max_tokens=1,
    )
    result = await provider.test_connection()
    # 不打印 api_key / 原文，仅记录结论
    logger.info("远程模型测试: %s -> %s", provider.endpoint, result.get("status"))
    return result
