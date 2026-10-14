"""数据库初始化

DB 路径来自 config.yaml 的 database.url。
注意：SQLite 的 -journal 文件若位于 TRAE 工作区内会被沙箱拦截，
因此默认指向工作区外的 ASCII 路径。
"""
import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config import get_config

logger = logging.getLogger(__name__)

_cfg = get_config()
DB_URL = _cfg.get("database", {}).get("url", "sqlite:///./data/kb_qa.db")

# 相对路径 → 基于项目根目录展开
if DB_URL.startswith("sqlite:///./"):
    _root = Path(__file__).resolve().parent.parent.parent
    _rel = DB_URL.replace("sqlite:///./", "")
    DB_URL = "sqlite:///" + str((_root / _rel)).replace("\\", "/")

engine = create_engine(DB_URL, echo=_cfg.get("database", {}).get("echo", False),
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

logger.info("Database URL: %s", DB_URL)


def get_db():
    """FastAPI 依赖注入用"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表（幂等）"""
    from backend.database import models
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")
