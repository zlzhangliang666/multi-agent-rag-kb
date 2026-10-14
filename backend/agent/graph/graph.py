"""
LangGraph 多 Agent 工作流图

流程：
用户提问 → 输入审核 → [不合规 → 结束]
                    → 合规 → 检索 → LLM生成 → 输出审核 → 判断是否需要图片
                    → [需要 → 绘图] → 汇总 → 结束
"""
import logging

from backend.agent.dispatch_agent.agent import dispatch_node, aggregate_node
from backend.agent.audit_agent.agent import audit_node, audit_output_node
from backend.agent.retrieval_agent.agent import retrieval_node
from backend.agent.llm_gen_agent.agent import llm_gen_node
from backend.agent.image_agent.agent import image_node

logger = logging.getLogger(__name__)


# ================================================================
# 条件路由函数
# ================================================================

def route_after_input_audit(state: dict) -> str:
    """输入审核后的路由"""
    if state.get("audit_input_result") == "blocked":
        return "end"  # 输入被拦截，直接结束
    return "retrieval"


def route_after_output_audit(state: dict) -> str:
    """输出审核后的路由"""
    if state.get("needs_image", False):
        return "image"
    return "aggregate"


# ================================================================
# 构建 LangGraph 图
# ================================================================

def build_agent_graph():
    """构建多 Agent 工作流图"""
    from langgraph.graph import StateGraph, END
    from backend.agent.state.state import AgentState

    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("dispatch", dispatch_node)
    workflow.add_node("audit_input", audit_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("llm_gen", llm_gen_node)
    workflow.add_node("audit_output", audit_output_node)
    workflow.add_node("image", image_node)
    workflow.add_node("aggregate", aggregate_node)

    # 设置入口
    workflow.set_entry_point("dispatch")

    # 连接边
    workflow.add_edge("dispatch", "audit_input")
    workflow.add_conditional_edges(
        "audit_input",
        route_after_input_audit,
        {
            "retrieval": "retrieval",
            "end": "aggregate",  # 被拦截也走 aggregate（汇总拦截信息）
        }
    )
    workflow.add_edge("retrieval", "llm_gen")
    workflow.add_edge("llm_gen", "audit_output")
    workflow.add_conditional_edges(
        "audit_output",
        route_after_output_audit,
        {
            "image": "image",
            "aggregate": "aggregate",
        }
    )
    workflow.add_edge("image", "aggregate")
    workflow.add_edge("aggregate", END)

    graph = workflow.compile()
    logger.info("Agent workflow graph compiled successfully")
    return graph


# 全局图实例
_agent_graph = None


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
    return _agent_graph