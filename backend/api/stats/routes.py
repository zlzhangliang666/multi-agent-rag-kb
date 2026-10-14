"""Stats API - 仪表盘统计"""
import logging, datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.database import get_db
from backend.database.models import (
    Document, Chunk, Message, AgentLog, Conversation, ImageRecord, User, ModelLog,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["统计"])

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    doc_count = db.query(Document).count()
    msg_count = db.query(Message).count()
    conv_count = db.query(func.count(func.distinct(Message.conversation_id))).scalar() or 0

    today = datetime.datetime.utcnow().date()
    today_msgs = db.query(Message).filter(func.date(Message.created_at) == today).count()

    agent_stats = {}
    rows = db.query(AgentLog.agent_name, func.count(AgentLog.id)).group_by(AgentLog.agent_name).all()
    for name, cnt in rows:
        agent_stats[name] = cnt

    return {
        "documents": doc_count,
        "messages": msg_count,
        "conversations": conv_count,
        "today_messages": today_msgs,
        "agent_calls": agent_stats,
    }


@router.get("/overview", summary="数据大屏总览")
def overview(db: Session = Depends(get_db)):
    """数据大屏用：把各类统计一次性聚合返回，前端一次请求即可渲染。"""
    counts = {
        "documents": db.query(Document).count(),
        "chunks": db.query(Chunk).count(),
        "conversations": db.query(Conversation).count(),
        "messages": db.query(Message).count(),
        "users": db.query(User).count(),
        "agent_logs": db.query(AgentLog).count(),
        "images": db.query(ImageRecord).count(),
    }

    now = datetime.datetime.utcnow()
    today = now.date()
    today_msgs = db.query(Message).filter(func.date(Message.created_at) == today).count()

    # Agent 调用次数 & 状态分布
    agent_calls = dict(db.query(AgentLog.agent_name, func.count(AgentLog.id))
                       .group_by(AgentLog.agent_name).all())
    agent_status = dict(db.query(AgentLog.status, func.count(AgentLog.id))
                        .group_by(AgentLog.status).all())

    # 近 7 天消息量
    messages_by_day = []
    for i in range(6, -1, -1):
        day = today - datetime.timedelta(days=i)
        cnt = db.query(Message).filter(func.date(Message.created_at) == day).count()
        messages_by_day.append({"date": day.strftime("%m-%d"), "count": cnt})

    # 文档类型分布（按扩展名）
    doc_types = {}
    for (name,) in db.query(Document.doc_name).all():
        ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "other")
        doc_types[ext] = doc_types.get(ext, 0) + 1

    # 最近会话
    recent = db.query(Conversation).order_by(Conversation.id.desc()).limit(6).all()
    recent_conversations = [{
        "conversation_id": c.conversation_id,
        "title": c.title,
        "created_at": str(c.created_at),
    } for c in recent]

    # 最近的模型加载/卸载事件
    events = db.query(ModelLog).order_by(ModelLog.id.desc()).limit(6).all()
    model_events = [{
        "event": e.event, "memory_mb": e.memory_mb, "created_at": str(e.created_at),
    } for e in events]

    logger.info("stats overview: %s", counts)
    return {
        "counts": counts,
        "today_messages": today_msgs,
        "agent_calls": agent_calls,
        "agent_status": agent_status,
        "messages_by_day": messages_by_day,
        "doc_types": doc_types,
        "recent_conversations": recent_conversations,
        "model_events": model_events,
    }