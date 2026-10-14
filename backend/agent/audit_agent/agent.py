"""
审核 Agent - DFA 敏感词检测 + 正则

严格遵守约束：不使用 LLM 做审核，避免抢模型实例。
"""
import logging
import os
import re
import time
from pathlib import Path

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log

logger = logging.getLogger(__name__)

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent

# 默认敏感词文件（每行一个词）
DEFAULT_SENSITIVE_FILE = ROOT_DIR / "data" / "sensitive_words.txt"

# 默认敏感词（内置备用）
FALLBACK_SENSITIVE_WORDS = [
    "赌博", "毒品", "枪支", "弹药", "爆炸物", "色情", "裸聊",
    "杀人", "放火", "投毒", "恐怖", "邪教", "反动", "分裂",
]


class DFAuditor:
    """DFA 敏感词审核器"""

    def __init__(self, word_file: str = None):
        self.sensitive_words = set()
        self.sensitive_patterns = []
        self._build_index(word_file or str(DEFAULT_SENSITIVE_FILE))

    def _build_index(self, word_file: str):
        """构建敏感词索引"""
        words = set()
        if os.path.isfile(word_file):
            try:
                with open(word_file, "r", encoding="utf-8") as f:
                    for line in f:
                        w = line.strip()
                        if w and not w.startswith("#"):
                            words.add(w.lower())
            except Exception as e:
                logger.warning("Failed to load sensitive words file: %s", e)

        if not words:
            words = set(FALLBACK_SENSITIVE_WORDS)
            logger.info("Using fallback sensitive words (%d words)", len(words))

        self.sensitive_words = words
        self.sensitive_patterns = []
        for w in words:
            try:
                self.sensitive_patterns.append(re.compile(re.escape(w), re.IGNORECASE))
            except re.error:
                pass
        logger.info("DFA auditor loaded %d sensitive words", len(words))

    def check(self, text: str) -> dict:
        """审核文本，返回 {result: pass/blocked, reason: str, matched: list}"""
        if not text:
            return {"result": "pass", "reason": "", "matched": []}

        matched = []
        for i, pattern in enumerate(self.sensitive_patterns):
            if pattern.search(text):
                matched.append(list(self.sensitive_words)[i])

        if matched:
            return {
                "result": "blocked",
                "reason": f"包含敏感词: {', '.join(matched[:3])}",
                "matched": matched,
            }
        return {"result": "pass", "reason": "", "matched": []}


# 全局单例
_auditor = None


def get_auditor() -> DFAuditor:
    global _auditor
    if _auditor is None:
        _auditor = DFAuditor()
    return _auditor


def audit_node(state: AgentState) -> AgentState:
    """审核节点 - 检查用户输入是否含敏感词"""
    t0 = time.time()
    query = state.get("query", "")
    auditor = get_auditor()

    logger.info("[AuditAgent] 审核输入: %s", query[:60])
    result = auditor.check(query)

    if result["result"] == "blocked":
        logger.warning("[AuditAgent] 拦截: %s", result["reason"])
        state["audit_input_result"] = "blocked"
        state["audit_input_reason"] = result["reason"]
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("audit_agent", "输入审核", query[:100],
                     f"blocked: {result['reason']}", "blocked", time.time() - t0)
        ]
        return state

    state["audit_input_result"] = "pass"
    state["agent_logs"] = state.get("agent_logs", []) + [
        make_log("audit_agent", "输入审核", query[:100],
                 "pass", "success", time.time() - t0)
    ]
    return state


def audit_output_node(state: AgentState) -> AgentState:
    """输出审核节点 - 检查生成内容是否合规

    无论 generated_text 是否为空，都记录 agent_log：
    - 空文本：status=skip
    - 含敏感词：status=blocked
    - 正常：status=success（pass）
    """
    t0 = time.time()
    text = state.get("generated_text", "")

    if not text:
        logger.info("[AuditAgent] 输出审核：文本为空，跳过")
        state["audit_output_result"] = "skip"
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("audit_agent", "输出审核", "(空)",
                     "文本为空，跳过审核", "skip", time.time() - t0)
        ]
        return state

    auditor = get_auditor()
    result = auditor.check(text)

    if result["result"] == "blocked":
        logger.warning("[AuditAgent] 输出拦截: %s", result["reason"])
        state["audit_output_result"] = "blocked"
        state["audit_output_reason"] = result["reason"]
        state["generated_text"] = (
            "⚠️ 生成内容未通过安全审核，已自动拦截。"
        )
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("audit_agent", "输出审核", text[:100],
                     f"blocked: {result['reason']}", "blocked", time.time() - t0)
        ]
        return state

    state["audit_output_result"] = "pass"
    state["agent_logs"] = state.get("agent_logs", []) + [
        make_log("audit_agent", "输出审核", text[:100],
                 "pass", "success", time.time() - t0)
    ]
    return state