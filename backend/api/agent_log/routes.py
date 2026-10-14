"""
Agent API - 多 Agent 工作流接口 + Agent 日志查询

POST /api/agent/chat              运行完整 Agent 链路，SSE 返回
GET  /api/agent/logs/{conv_id}    查询 Agent 日志（优先读数据库）

SSE 事件类型：agent_log / token / image / done / error
落库：conversations / messages / agent_logs / image_records
"""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.agent.graph.graph import get_agent_graph
from backend.database import SessionLocal, get_db
from backend.database import crud
from backend.database.models import AgentLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["Agent 工作流"])

# 内存兜底缓存（数据库不可用时仍能返回最近日志）
_agent_log_store = {}


class AgentChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4096)
    conversation_id: Optional[str] = Field(None)


def _persist(conv_id: str, prompt: str, logs: list, answer: str,
             sources: list, image_type: str = None, image_code: str = None):
    """把一次问答落库。使用独立 Session，失败不影响 SSE 返回。"""
    db = SessionLocal()
    try:
        title = (prompt or "")[:40] or "新对话"
        crud.ensure_conversation(db, conv_id, title=title)
        crud.add_message(db, conv_id, "user", prompt)
        crud.add_message(db, conv_id, "assistant", answer, sources=sources)
        n = crud.add_agent_logs(db, conv_id, logs)
        if image_code:
            crud.add_image_record(db, conv_id, image_type or "mermaid", image_code, prompt)
        logger.info("已落库 conv=%s logs=%d", conv_id, n)
    except Exception as e:
        db.rollback()
        logger.warning("落库失败（不影响返回）: %s", e)
    finally:
        db.close()


@router.post("/chat", summary="多 Agent 流式问答")
async def agent_chat(req: AgentChatRequest):
    conv_id = req.conversation_id or str(uuid.uuid4())
    graph = get_agent_graph()

    async def event_stream():
        initial_state = {
            "query": req.prompt,
            "conversation_id": conv_id,
            "agent_logs": [],
        }

        try:
            result = await graph.ainvoke(initial_state)
        except Exception as e:
            logger.error("Agent workflow failed: %s", e, exc_info=True)
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return

        logs = result.get("agent_logs", [])
        _agent_log_store[conv_id] = logs

        # 1) 逐条推送 agent_log
        for entry in logs:
            payload = {"status": "agent_log", "log": entry}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # 2) 输入被拦截：直接结束
        if result.get("audit_input_result") == "blocked":
            text = result.get("generated_text") or "⚠️ 您的输入包含敏感内容，已被系统拦截。"
            yield f"data: {json.dumps({'status': 'token', 'token': text}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'status': 'done', 'conversation_id': conv_id, 'blocked': True, 'agent_logs_count': len(logs)}, ensure_ascii=False)}\n\n"
            _persist(conv_id, req.prompt, logs, text, sources=[])
            return

        # 3) 生成文本
        answer = result.get("generated_text", "")
        if answer:
            yield f"data: {json.dumps({'status': 'token', 'token': answer}, ensure_ascii=False)}\n\n"

        # 4) 示意图
        img_type = result.get("image_type")
        img_code = result.get("image_code")
        if result.get("needs_image") and img_code:
            yield f"data: {json.dumps({'status': 'image', 'image_type': img_type, 'code': img_code}, ensure_ascii=False)}\n\n"

        # 5) 引用来源
        sources = []
        for c in (result.get("retrieved_chunks") or []):
            sources.append({
                "doc_name": c.get("doc_name", ""),
                "page": c.get("page", ""),
                "section": c.get("section", ""),
                "score": round(float(c.get("score", 0) or 0), 4),
                "chunk_id": c.get("chunk_id", ""),
            })
        if sources:
            yield f"data: {json.dumps({'status': 'sources', 'sources': sources}, ensure_ascii=False)}\n\n"

        # 6) 完成
        err = result.get("error")
        done = {"status": "done", "conversation_id": conv_id, "agent_logs_count": len(logs)}
        if err:
            done["error"] = err
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

        # 7) 落库
        _persist(conv_id, req.prompt, logs, answer, sources, img_type, img_code)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.get("/logs/{conversation_id}", summary="获取 Agent 日志")
def get_agent_logs(conversation_id: str, db: Session = Depends(get_db)):
    """优先从数据库读取；数据库无记录时回退内存缓存"""
    rows = crud.list_agent_logs(db, conversation_id)
    if rows:
        return {
            "conversation_id": conversation_id,
            "count": len(rows),
            "source": "database",
            "logs": [{
                "agent_name": r.agent_name, "action": r.action,
                "input_summary": r.input_summary, "output_summary": r.output_summary,
                "status": r.status, "elapsed": r.elapsed,
                "created_at": str(r.created_at),
            } for r in rows],
        }

    mem = _agent_log_store.get(conversation_id, [])
    return {"conversation_id": conversation_id, "count": len(mem),
            "source": "memory", "logs": mem}