"""
检索 Agent - 使用 RRF 混合检索器
"""
import logging
import time

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log
from backend.config import get_config

logger = logging.getLogger(__name__)

# 全局检索器引用（由外部注入）
_retriever = None

# 默认值（可被 config.yaml 的 retrieval 段覆盖）
DEFAULT_TOP_K = 4
# 相对分数阈值：低于 top1 分数 * 该比例的来源会被丢弃
DEFAULT_MIN_SCORE_RATIO = 0.45
# 至少保留的来源条数
DEFAULT_MIN_SOURCES = 2


def init_retrieval_agent(retriever):
    global _retriever
    _retriever = retriever
    logger.info("Retrieval agent initialized")


def _retrieval_params() -> tuple:
    """读取检索参数：top_k / 相对分数阈值 / 最少保留条数"""
    cfg = get_config().get("retrieval", {}) or {}
    try:
        top_k = int(cfg.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K)
        ratio = float(cfg.get("min_score_ratio", DEFAULT_MIN_SCORE_RATIO) or DEFAULT_MIN_SCORE_RATIO)
        min_keep = int(cfg.get("min_sources", DEFAULT_MIN_SOURCES) or DEFAULT_MIN_SOURCES)
    except (TypeError, ValueError):
        top_k, ratio, min_keep = DEFAULT_TOP_K, DEFAULT_MIN_SCORE_RATIO, DEFAULT_MIN_SOURCES
    return top_k, ratio, min_keep


def cut_low_score(results: list, min_ratio: float = DEFAULT_MIN_SCORE_RATIO,
                  min_keep: int = DEFAULT_MIN_SOURCES) -> list:
    """按相对分数阈值截断低分来源

    RRF 融合分数只反映排名融合，绝对值没有可比性，因此用「相对 top1 的比例」过滤，
    避免明显跑题的来源也被当作引用展示（precision 上升，coverage 可能下降）。
    """
    if len(results) <= min_keep:
        return results
    top = float(results[0].get("score", 0) or 0)
    if top <= 0:
        return results
    kept = [r for r in results if float(r.get("score", 0) or 0) >= top * min_ratio]
    if len(kept) < min_keep:
        kept = results[:min_keep]
    return kept


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

    top_k, min_ratio, min_keep = _retrieval_params()
    logger.info("[RetrievalAgent] 检索: %s", query[:60])
    try:
        results = _retriever.retrieve(query, top_k=top_k)
        results = cut_low_score(results, min_ratio, min_keep)
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