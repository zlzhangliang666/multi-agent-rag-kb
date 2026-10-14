"""
检索 Agent - 使用 RRF 混合检索器
"""
import logging
import time

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log

logger = logging.getLogger(__name__)

# 全局检索器引用（由外部注入）
_retriever = None


def init_retrieval_agent(retriever):
    global _retriever
    _retriever = retriever
    logger.info("Retrieval agent initialized")


def retrieval_node(state: AgentState) -> AgentState:
    """检索节点 - 从知识库检索相关片段"""
    t0 = time.time()
    query = state.get("query", "")

    if _retriever is None:
        logger.error("Retrieval agent not initialized")
        state["retrieved_chunks"] = []
        state["retrieved_context"] = ""
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("retrieval_agent", "知识库检索", query[:100],
                     "error: not initialized", "error", time.time() - t0)
        ]
        return state

    logger.info("[RetrievalAgent] 检索: %s", query[:60])
    try:
        results = _retriever.retrieve(query, top_k=4)
        state["retrieved_chunks"] = results

        # 拼接上下文
        context_parts = []
        for i, r in enumerate(results):
            source = f"[来源: {r.get('doc_name', '?')} 第{r.get('page', '?')}页]"
            context_parts.append(f"{r.get('text', '')}\n{source}")
        state["retrieved_context"] = "\n\n".join(context_parts)

        elapsed = time.time() - t0
        logger.info("[RetrievalAgent] 检索完成: %d 结果, 耗时 %.2fs", len(results), elapsed)
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("retrieval_agent", "知识库检索", query[:100],
                     f"{len(results)} 个结果", "success", elapsed)
        ]
    except Exception as e:
        logger.error("[RetrievalAgent] 检索失败: %s", e)
        state["retrieved_chunks"] = []
        state["retrieved_context"] = ""
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("retrieval_agent", "知识库检索", query[:100],
                     f"error: {e}", "error", time.time() - t0)
        ]
    return state