"""
调度 Agent - 负责调度完整的 LangGraph 工作流
"""
import logging
import time

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log

logger = logging.getLogger(__name__)


def dispatch_node(state: AgentState) -> AgentState:
    """调度入口节点 - 初始化状态"""
    t0 = time.time()
    logger.info("[DispatchAgent] 收到请求: %s", state.get("query", "")[:60])
    state["agent_logs"] = state.get("agent_logs", []) + [
        make_log("dispatch_agent", "调度入口", state.get("query", "")[:100],
                 "开始处理", "success", time.time() - t0)
    ]
    return state


def aggregate_node(state: AgentState) -> AgentState:
    """汇总节点 - 收集所有 Agent 结果，生成最终输出"""
    t0 = time.time()
    query = state.get("query", "")
    audit_result = state.get("audit_input_result", "")

    if audit_result == "blocked":
        state["generated_text"] = (
            "⚠️ 您的输入包含敏感内容，已被系统拦截。"
        )
        logger.info("[DispatchAgent] 输入被拦截")
    else:
        logger.info("[DispatchAgent] 流程完成, generated_len=%d, needs_image=%s",
                     len(state.get("generated_text", "")), state.get("needs_image", False))

    state["agent_logs"] = state.get("agent_logs", []) + [
        make_log("dispatch_agent", "汇总输出", query[:100],
                 f"最终结果 {len(state.get('generated_text',''))}字",
                 "success", time.time() - t0)
    ]
    return state