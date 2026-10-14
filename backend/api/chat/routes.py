"""Chat API - 流式对话 + 历史记录"""
import json, logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from backend.database import get_db, crud
from backend.database.models import Conversation, Message, AgentLog, ImageRecord
from backend.llm_core.providers.service import get_llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["对话"])

class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4096)
    conversation_id: Optional[str] = Field(None)

@router.post("/stream")
async def chat_stream(req: ChatRequest):
    service = get_llm_service()
    async def sse():
        if not service.is_ready():
            yield f"data: {json.dumps({'status': 'model_loading', 'message': '模型加载中...'})}\n\n"
            r = await service.load_model()
            if r.get("status") != "ok":
                yield f"data: {json.dumps({'status': 'error', 'message': r.get('message')})}\n\n"
                return
        async for event in service.generate(prompt=req.prompt):
            yield event
    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get("/history")
def chat_history(conversation_id: str = None, db: Session = Depends(get_db)):
    if conversation_id:
        msgs = db.query(Message).filter(Message.conversation_id == conversation_id)\
                .order_by(Message.created_at).all()
        logs = db.query(AgentLog).filter(AgentLog.conversation_id == conversation_id)\
                .order_by(AgentLog.created_at).all()
        imgs = db.query(ImageRecord).filter(ImageRecord.conversation_id == conversation_id)\
                .order_by(ImageRecord.created_at).all()
        return {"messages": [{"role": m.role, "content": m.content, "sources": m.sources,
                              "created_at": str(m.created_at)} for m in msgs],
                "images": [{"image_type": i.image_type, "code": i.code,
                            "query": i.query, "created_at": str(i.created_at)} for i in imgs],
                "agent_logs": [{"agent_name": l.agent_name, "action": l.action, "status": l.status,
                                "elapsed": l.elapsed, "created_at": str(l.created_at)} for l in logs]}
    convs = db.query(Conversation).order_by(Conversation.created_at.desc()).limit(50).all()
    return {"conversations": [{"conversation_id": c.conversation_id, "title": c.title,
                                "created_at": str(c.created_at)} for c in convs]}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    """删除历史会话及其消息 / Agent 日志 / 绘图记录（不可恢复）"""
    result = crud.delete_conversation(db, conversation_id)
    if sum(result.values()) == 0:
        raise HTTPException(status_code=404, detail="会话不存在")
    logger.info("Deleted conversation=%s %s", conversation_id, result)
    return {"conversation_id": conversation_id, "deleted": True, **result}