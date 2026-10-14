"""
多 Agent 协同 RAG 知识库问答系统 - 后端入口

启动方式：
    python backend/main.py
    或
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 将项目根目录加入 path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.config import get_config
from backend.llm_core.model_manager.manager import get_model_manager
from backend.llm_core.providers.service import get_llm_service

# 配置日志
config = get_config()
log_level = getattr(logging, config.get("logging", {}).get("level", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(name)-16s %(levelname)-8s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("main")


def _init_agents():
    """初始化多 Agent 组件 + 数据库"""
    # 初始化数据库
    try:
        from backend.database import init_db
        init_db()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.warning("数据库初始化失败: %s", e)

    from backend.agent.retrieval_agent.agent import init_retrieval_agent
    from backend.agent.llm_gen_agent.agent import init_llm_gen_agent

    # 初始化生成 Agent（LLM Service：本地 / OpenAI 兼容远程，按配置自动选择）
    init_llm_gen_agent(get_llm_service())

    # 初始化检索 Agent（需要 RAG 组件，如果已存在的话）
    try:
        from backend.rag.embedding import ONNXEmbedding
        from backend.rag.vector_store import ChromaStore
        from backend.rag.bm25_index import BM25Index
        from backend.rag.retriever import HybridRetriever

        embedder = ONNXEmbedding(model_dir=config.get("embedding", {}).get("model_dir"))
        persist_dir = config.get("chroma", {}).get("persist_directory", "data/chroma_db")
        bm25_path = config.get("retrieval", {}).get("bm25_index_path", "data/bm25_index.pkl")

        vs = ChromaStore(persist_dir=persist_dir)
        bm25 = BM25Index(persist_path=bm25_path)
        retriever = HybridRetriever(vs, bm25, embedder)
        init_retrieval_agent(retriever)
        logger.info("RAG components initialized for Agent workflow")

        # 注入到知识库 API 路由（修复 /api/kb/* 503 问题）
        from backend.rag.parser import parse_document
        from backend.rag.chunker import AdaptiveChunker
        from backend.api.kb.routes import init_kb_routes
        init_kb_routes(vs, bm25, embedder, parse_document, AdaptiveChunker())
        logger.info("KB API routes initialized")
    except Exception as e:
        logger.warning("RAG components not available for Agent (KB may be empty): %s", e)

    logger.info("All Agent components initialized")


@asynccontextmanager
async def lifespan(app):
    """应用生命周期管理"""
    manager = get_model_manager()
    logger.info("正在启动 Worker 子进程...")
    ok = await manager.start_worker()
    if ok:
        logger.info("Worker 子进程启动成功，模型将按需加载")
    else:
        logger.warning("Worker 子进程启动失败，请检查配置后手动重启")

    # 初始化 Agent 组件
    _init_agents()

    yield

    logger.info("正在停止 Worker 子进程...")
    await manager.stop_worker()
    logger.info("Worker 子进程已停止")


def create_app():
    """创建 FastAPI 应用实例"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    config = get_config()

    app = FastAPI(
        title="知识库问答系统",
        description="多 Agent 协同 RAG 知识库问答系统（低配本轻量化版）",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS 配置（允许前端开发服务器访问）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 静态文件服务（图片等）
    images_dir = Path(config["static"]["images_dir"])
    if images_dir.exists():
        app.mount("/static", StaticFiles(directory=str(images_dir)), name="static")

    # ---- 注册路由 ----

    from backend.api.chat.routes import router as chat_router
    from backend.api.model.routes import router as model_router
    from backend.api.agent_log.routes import router as agent_router
    from backend.api.kb.routes import router as kb_router
    from backend.api.auth.routes import router as auth_router
    from backend.api.stats.routes import router as stats_router
    from backend.api.llm.routes import router as llm_router

    app.include_router(chat_router)
    app.include_router(model_router)
    app.include_router(agent_router)
    app.include_router(kb_router)
    app.include_router(auth_router)
    app.include_router(stats_router)
    app.include_router(llm_router)

    @app.get("/")
    async def root():
        return {
            "message": "知识库问答系统后端服务",
            "version": "0.1.0",
            "docs": "/docs",
        }

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    config = get_config()

    # reload 默认关闭 —— 开启 reload 时 Uvicorn 会重启主进程，
    # 但 Worker 子进程已通过 DETACHED_PROCESS + 持久化信息文件
    # 实现了独立生存和自动重连，不受 reload 影响。
    # 如需热重载开发，手动传参：uvicorn backend.main:app --reload
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=config.get("logging", {}).get("level", "info").lower(),
    )