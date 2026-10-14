"""CRUD 操作封装"""
import json
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from backend.database.models import (
    AgentLog, Chunk, Conversation, Document, ImageRecord, Message, ModelLog, User,
)

logger = logging.getLogger(__name__)


# ---------- Conversation ----------

def ensure_conversation(db: Session, conversation_id: str, title: str = None,
                        user_id: int = None) -> Conversation:
    """会话不存在则创建"""
    conv = db.query(Conversation).filter(
        Conversation.conversation_id == conversation_id).first()
    if conv is None:
        conv = Conversation(conversation_id=conversation_id,
                            title=title or "新对话", user_id=user_id)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    elif title and conv.title in (None, "新对话"):
        conv.title = title
        db.commit()
    return conv


# ---------- Message ----------

def add_message(db: Session, conversation_id: str, role: str, content: str,
                sources: Optional[list] = None) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=json.dumps(sources, ensure_ascii=False) if sources else None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def delete_conversation(db: Session, conversation_id: str) -> dict:
    """删除会话及其消息 / Agent 日志 / 绘图记录，返回各表删除条数"""
    msg_n = db.query(Message).filter(Message.conversation_id == conversation_id).delete()
    log_n = db.query(AgentLog).filter(AgentLog.conversation_id == conversation_id).delete()
    img_n = db.query(ImageRecord).filter(ImageRecord.conversation_id == conversation_id).delete()
    conv_n = db.query(Conversation).filter(Conversation.conversation_id == conversation_id).delete()
    db.commit()
    return {"conversation": conv_n, "messages": msg_n, "agent_logs": log_n, "image_records": img_n}


def list_messages(db: Session, conversation_id: str) -> List[Message]:
    return db.query(Message).filter(Message.conversation_id == conversation_id)\
             .order_by(Message.created_at, Message.id).all()


# ---------- AgentLog ----------

def add_agent_logs(db: Session, conversation_id: str, logs: List[dict]) -> int:
    """批量写入 Agent 日志，返回写入条数"""
    if not logs:
        return 0
    n = 0
    for lg in logs:
        db.add(AgentLog(
            conversation_id=conversation_id,
            agent_name=lg.get("agent_name"),
            action=lg.get("action"),
            input_summary=(lg.get("input_summary") or "")[:256],
            output_summary=(lg.get("output_summary") or "")[:256],
            status=lg.get("status"),
            elapsed=lg.get("elapsed"),
        ))
        n += 1
    db.commit()
    return n


def list_agent_logs(db: Session, conversation_id: str) -> List[AgentLog]:
    return db.query(AgentLog).filter(AgentLog.conversation_id == conversation_id)\
             .order_by(AgentLog.created_at, AgentLog.id).all()


# ---------- Document / Chunk ----------

def add_document(db: Session, doc_id: str, doc_name: str, file_type: str,
                 chunk_count: int) -> Document:
    exist = db.query(Document).filter(Document.doc_id == doc_id).first()
    if exist:
        exist.doc_name = doc_name
        exist.chunk_count = chunk_count
        db.commit()
        return exist
    doc = Document(doc_id=doc_id, doc_name=doc_name,
                   file_type=file_type, chunk_count=chunk_count)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def add_chunks(db: Session, chunks: List[dict]) -> int:
    n = 0
    for c in chunks:
        db.add(Chunk(
            chunk_id=c.get("chunk_id"),
            doc_id=c.get("doc_id"),
            page=int(c.get("page") or 0) if str(c.get("page") or "0").isdigit() else 0,
            section=c.get("section"),
            text=c.get("text"),
            char_start=c.get("char_start"),
            char_end=c.get("char_end"),
        ))
        n += 1
    db.commit()
    return n


def delete_document(db: Session, doc_id: str) -> int:
    """删除文档及其 chunks，返回删除的 chunk 数"""
    n = db.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
    db.query(Document).filter(Document.doc_id == doc_id).delete()
    db.commit()
    return n


def list_documents(db: Session) -> List[Document]:
    return db.query(Document).order_by(Document.upload_time.desc()).all()


# ---------- ModelLog ----------

def add_model_log(db: Session, event: str, memory_mb: float = None,
                  message: str = None) -> ModelLog:
    log = ModelLog(event=event, memory_mb=memory_mb, message=message)
    db.add(log)
    db.commit()
    return log


# ---------- ImageRecord ----------

def add_image_record(db: Session, conversation_id: str, image_type: str,
                     code: str, query: str) -> ImageRecord:
    rec = ImageRecord(conversation_id=conversation_id, image_type=image_type,
                      code=code, query=(query or "")[:256])
    db.add(rec)
    db.commit()
    return rec


# ---------- User ----------

def get_user_by_name(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, username: str, password_hash: str) -> User:
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user