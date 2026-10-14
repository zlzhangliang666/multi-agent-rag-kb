"""
Model API - 模型状态/加载/卸载接口
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.llm_core.providers.service import get_llm_service

logger = logging.getLogger("api.model")

router = APIRouter(prefix="/api/model", tags=["模型管理"])


class MessageResponse(BaseModel):
    status: str
    message: str


def _log_model_event(event: str, result: dict):
    """把模型加载/卸载事件写入 model_logs 表（失败不影响接口返回）"""
    try:
        from backend.database import SessionLocal, crud
        db = SessionLocal()
        try:
            mem = None
            try:
                import asyncio
                # get_status 是协程，这里用同步方式取内存
                mem = None
            except Exception:
                mem = None
            crud.add_model_log(db, event, memory_mb=mem,
                               message=(result or {}).get("message"))
        finally:
            db.close()
    except Exception as e:
        logger.warning("model_log 落库失败: %s", e)


@router.get("/status", summary="获取模型状态")
async def model_status():
    """获取模型加载状态、内存占用、空闲时间等（含当前生效的 provider）"""
    service = get_llm_service()
    return await service.get_status()


@router.post("/load", summary="加载模型")
async def model_load():
    """手动加载模型到内存（远程模式下无需加载本地权重）"""
    service = get_llm_service()
    if service.is_remote():
        return {"status": "ok", "message": "当前为远程模型（OpenAI 兼容），无需加载本地模型"}
    result = await service.load_model()
    _log_model_event("loaded", result)
    return result


@router.post("/unload", summary="卸载模型")
async def model_unload():
    """手动卸载模型，释放内存（远程模式下无需卸载本地权重）"""
    service = get_llm_service()
    result = await service.unload_model()
    _log_model_event("unloaded", result)
    return result