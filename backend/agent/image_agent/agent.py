"""
绘图 Agent - 生成 Mermaid / Graphviz 示意图
"""
import logging
import os
import time
from pathlib import Path

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
IMAGES_DIR = ROOT_DIR / "backend" / "static" / "images"


def _save_diagram(code: str):
    """把 Mermaid 源码写入静态目录（目录取自配置，沙箱不允许在工作区内新建文件）。

    写盘失败返回 None，不影响返回 Mermaid 代码。
    """
    try:
        from backend.config import get_config
        _cfg = get_config()
        img_dir = Path(_cfg.get("static", {}).get("images_dir") or str(IMAGES_DIR))
        os.makedirs(str(img_dir), exist_ok=True)
        img_file = img_dir / f"diagram_{int(time.time())}.mmd"
        with open(str(img_file), "w", encoding="utf-8") as f:
            f.write(code)
        return str(img_file)
    except Exception as save_err:
        logger.warning("[ImageAgent] 示意图写盘失败（降级，仍返回代码）: %s", save_err)
        return None


def generate_mermaid_code(query: str, chunk_texts: list) -> str:
    """根据查询和检索内容生成 Mermaid 代码

    使用模板匹配常见知识点类型
    """
    q = query.lower()

    if "网络" in q or "tcp" in q or "ip" in q or "osi" in q or "七层" in q:
        return """graph TD
    subgraph OSI七层模型 与 TCP/IP协议栈
        A7[应用层 HTTP/FTP/DNS/SMTP] --> A6[表示层 加密/编码]
        A6 --> A5[会话层 建立/管理会话]
        A5 --> A4[传输层 TCP/UDP]
        A4 --> A3[网络层 IP/ICMP/路由]
        A3 --> A2[数据链路层 以太网/PPP]
        A2 --> A1[物理层 比特流传输]
    end
"""

    if "死锁" in q:
        return """graph TD
    A[进程A] -->|持有资源R1| B[等待资源R2]
    C[进程B] -->|持有资源R2| D[等待资源R1]
    B -.->|互相等待| D
    style A fill:#f9f,stroke:#333
    style C fill:#f9f,stroke:#333
"""
    if "中断" in q:
        return """sequenceDiagram
    participant CPU
    participant 设备
    CPU->>CPU: 执行指令
    设备->>CPU: 中断请求
    CPU->>CPU: 保存上下文
    CPU->>CPU: 中断处理
    CPU->>CPU: 恢复上下文
    CPU->>CPU: 继续执行
"""
    if "调度" in q or "调度算法" in q:
        return """graph LR
    A[作业/进程] --> B{调度算法}
    B --> C[先来先服务 FCFS]
    B --> D[短作业优先 SJF]
    B --> E[优先级调度]
    B --> F[时间片轮转 RR]
    B --> G[多级反馈队列 MFQ]
"""
    if "os" in q or "操作系统" in q or "分层" in q:
        return """graph TD
    subgraph 操作系统层次结构
        A[用户接口] --> B[应用程序]
        B --> C[系统调用接口]
        C --> D[文件系统]
        C --> E[进程管理]
        C --> F[内存管理]
        C --> G[设备管理]
        D --> H[硬件]
        E --> H
        F --> H
        G --> H
    end
"""
    if "网络" in q or "tcp" in q or "ip" in q:
        return """graph TD
    subgraph TCP/IP 协议栈
        A[应用层 HTTP/FTP] --> B[传输层 TCP/UDP]
        B --> C[网络层 IP/ICMP]
        C --> D[网络接口层]
    end
"""

    # 默认架构图
    return """graph LR
    A[用户输入] --> B[审核Agent]
    B -->|通过| C[检索Agent]
    B -->|拦截| E[结束]
    C --> D[生成Agent]
    D --> F[输出审核]
    F -->|通过| G[汇总回复]
    F -->|拦截| E
"""


def image_node(state: AgentState) -> AgentState:
    """绘图节点 - 生成 Mermaid 示意图"""
    t0 = time.time()

    # 方案 B 主路径：模型已在回答中输出 Mermaid 代码块，直接采用
    if state.get("image_code"):
        from backend.agent.llm_gen_agent.agent import sanitize_mermaid_code
        code = sanitize_mermaid_code(str(state["image_code"]))
        state["image_code"] = code
        state["image_type"] = state.get("image_type") or "mermaid"
        state["image_path"] = _save_diagram(code)
        elapsed = time.time() - t0
        logger.info("[ImageAgent] 采用模型自绘 Mermaid, %d 字符", len(code))
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("image_agent", "生成示意图", state.get("query", "")[:100],
                     f"模型自绘 Mermaid {len(code)}字符", "success", elapsed)
        ]
        return state

    if not state.get("needs_image", False):
        logger.info("[ImageAgent] 无需绘图")
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("image_agent", "生成示意图", "判断无需绘图",
                     "skip", "success", time.time() - t0)
        ]
        return state

    query = state.get("query", "")
    chunks = state.get("retrieved_chunks", [])

    # 判断是否指明了具体绘图对象
    from backend.agent.llm_gen_agent.agent import has_image_subject
    if not has_image_subject(query):
        hint = "请说明要画什么图，例如“画一个操作系统层次结构”或“画计算机网络七层模型”。"
        logger.info("[ImageAgent] 绘图请求无具体对象，返回提示")
        prev_text = state.get("generated_text", "")
        state["generated_text"] = (prev_text + "\n\n" + hint) if prev_text else hint
        state["image_code"] = None
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("image_agent", "生成示意图", query[:100],
                     "无具体对象，返回提示语", "skip", time.time() - t0)
        ]
        return state

    logger.info("[ImageAgent] 生成示意图: %s", query[:60])

    try:
        mermaid_code = generate_mermaid_code(query, chunks)
        state["image_code"] = mermaid_code
        state["image_type"] = "mermaid"

        # 保存到文件（可选，失败不影响 Mermaid 代码返回）
        state["image_path"] = _save_diagram(mermaid_code)

        elapsed = time.time() - t0
        logger.info("[ImageAgent] 示意图生成完成, 耗时%.2fs", elapsed)
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("image_agent", "生成示意图", query[:100],
                     f"Mermaid 代码 {len(mermaid_code)}字符", "success", elapsed)
        ]
    except Exception as e:
        logger.error("[ImageAgent] 绘图失败: %s", e)
        state["error"] = str(e)
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("image_agent", "生成示意图", query[:100],
                     f"error: {e}", "error", time.time() - t0)
        ]

    return state