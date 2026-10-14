"""
Agent 日志工具
每条日志包含：会话 ID、Agent 名、动作、输入摘要、输出摘要、状态、耗时、创建时间
"""
import logging
import time
from typing import Optional

from backend.agent.state.state import AgentLogEntry

logger = logging.getLogger(__name__)


def make_log(agent_name: str, action: str, input_summary: str = "",
             output_summary: str = "", status: str = "success",
             elapsed: float = 0.0) -> AgentLogEntry:
    return {
        "agent_name": agent_name,
        "action": action,
        "input_summary": input_summary[:200],
        "output_summary": output_summary[:200],
        "status": status,
        "elapsed": round(elapsed, 3),
        "created_at": time.time(),
    }


def collect_logs(state: dict) -> list:
    """从状态中提取日志列表"""
    return state.get("agent_logs", [])