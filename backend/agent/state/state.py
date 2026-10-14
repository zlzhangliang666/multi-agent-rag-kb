"""
Agent 共享状态定义
"""
from typing import List, Optional, TypedDict


class AgentLogEntry(TypedDict, total=False):
    agent_name: str
    action: str
    input_summary: str
    output_summary: str
    status: str  # success / blocked / error
    elapsed: float
    created_at: float


class AgentState(TypedDict, total=False):
    """LangGraph 多 Agent 共享状态"""

    # 输入
    query: str
    conversation_id: str

    # 审核
    audit_input_result: str          # pass / blocked
    audit_input_reason: str          # 拦截原因
    audit_output_result: str         # pass / blocked
    audit_output_reason: str

    # 检索
    retrieved_chunks: List[dict]     # 检索结果片段
    retrieved_context: str           # 拼接后的上下文文本

    # 生成
    generated_text: str              # 完整生成文本
    generation_error: Optional[str]

    # 绘图
    needs_image: bool                # 是否需要生成示意图
    image_code: Optional[str]        # Mermaid/Graphviz 代码
    image_path: Optional[str]        # 生成的图片路径
    image_type: Optional[str]        # mermaid / graphviz

    # Agent 日志
    agent_logs: List[AgentLogEntry]

    # 系统
    error: Optional[str]