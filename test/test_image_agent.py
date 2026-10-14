"""绘图意图判定 + 模型自绘 Mermaid 代码块抽取测试。"""
from backend.agent.llm_gen_agent.agent import (
    extract_mermaid_blocks,
    has_image_subject,
    is_degenerate_mermaid,
    sanitize_mermaid_code,
    should_use_image,
)


def test_should_use_image_with_subject():
    assert should_use_image("画一个操作系统层次结构") is True
    assert should_use_image("画一个计算机网络七层模型") is True
    assert should_use_image("请给出 TCP 状态转换图") is True


def test_should_use_image_negative():
    assert should_use_image("死锁产生的四个条件是什么") is False
    assert should_use_image("今天天气怎么样") is False


def test_has_image_subject_detects_subject():
    assert has_image_subject("画一个计算机网络七层模型") is True
    assert has_image_subject("画一个二叉树的遍历") is True


def test_has_image_subject_without_subject():
    # "画个图" 无具体对象 → 应降级
    assert has_image_subject("画个图") is False
    assert has_image_subject("帮我生成一张图") is False


def test_extract_mermaid_block():
    text = "下面是一个示意图：\n\n```mermaid\ngraph TD\n    A[开始] --> B[结束]\n```\n\n以上。"
    cleaned, blocks = extract_mermaid_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].startswith("graph TD")
    assert "```" not in cleaned
    assert "下面是一个示意图" in cleaned
    assert "以上。" in cleaned


def test_extract_mermaid_no_block():
    cleaned, blocks = extract_mermaid_blocks("普通回答，没有代码块")
    assert blocks == []
    assert cleaned == "普通回答，没有代码块"


def test_extract_mermaid_tolerates_spacing_and_case():
    _, blocks = extract_mermaid_blocks("``` Mermaid\ngraph LR\n    A --> B\n```")
    assert blocks == ["graph LR\n    A --> B"]


def test_extract_mermaid_multiple_blocks():
    text = "```mermaid\ngraph TD\n A-->B\n```\n中间文字\n```mermaid\ngraph LR\n C-->D\n```"
    cleaned, blocks = extract_mermaid_blocks(text)
    assert len(blocks) == 2
    assert "中间文字" in cleaned


def test_sanitize_arrow_with_space():
    raw = "graph BT\n    root1 -- 中序遍历: 左子树1 -- > leaf1"
    out = sanitize_mermaid_code(raw)
    assert "-- >" not in out
    assert "--> leaf1" in out


def test_sanitize_single_dash_arrow():
    raw = 'graph LR\nA("根节点") -- "左子树" -> B'
    out = sanitize_mermaid_code(raw)
    assert "--> B" in out
    assert "->" not in out.replace("-->", "")


def test_sanitize_keeps_valid_arrows():
    raw = "graph TD\n    A --> B\n    C -.-> D\n    E ==> F"
    out = sanitize_mermaid_code(raw)
    assert "A --> B" in out
    assert "C -.-> D" in out
    assert "E ==> F" in out


def test_sanitize_trailing_colon_label():
    raw = "graph LR\n    A(客户) --> B(服务器): TCP 连接请求报文段"
    out = sanitize_mermaid_code(raw)
    assert "A(客户) -->|TCP 连接请求报文段| B(服务器)" in out
    assert "B(服务器):" not in out


def test_sanitize_trailing_colon_label_bare_node():
    out = sanitize_mermaid_code("graph LR\n    C --> A: 确认报文段")
    assert "C -->|确认报文段| A" in out


def test_sanitize_keeps_edge_text_form_with_colon():
    raw = "graph LR\n    root1 -- 中序遍历: 左子树 --> leaf1"
    out = sanitize_mermaid_code(raw)
    assert out.strip() == raw.strip()


def test_sanitize_colon_after_label_bars():
    raw = "graph LR\n    root[遍历示意图] -->|前序遍历| root: NLR"
    out = sanitize_mermaid_code(raw)
    assert "root[遍历示意图] -->|前序遍历: NLR| root" in out
    assert "root: NLR" not in out


def test_degenerate_all_self_loops():
    # 用户截图里的情况：三个箭头都是 root --> root
    code = (
        "graph LR\n"
        "root[遍历示意图] -->|前序遍历: NLR| root\n"
        "root -->|中序遍历: LNR| root\n"
        "root -->|后序遍历: LRN| root"
    )
    assert is_degenerate_mermaid(code) is True


def test_degenerate_no_edge():
    assert is_degenerate_mermaid("graph LR\n  A[开始]\n  B[结束]") is True


def test_degenerate_too_few_nodes():
    assert is_degenerate_mermaid("graph LR\n  A[开始] --> B[结束]") is True


def test_degenerate_false_for_normal_diagram():
    code = "graph LR\n  A[开始] --> B[处理] --> C[结束]"
    assert is_degenerate_mermaid(code) is False


def test_degenerate_false_for_tree_with_labels():
    code = (
        "graph TD\n"
        "  R[根] --> L[左子树]\n"
        "  R --> RR[右子树]\n"
        "  L --> L1[叶子1]\n"
        "  RR --> R1[叶子2]"
    )
    assert is_degenerate_mermaid(code) is False


def test_degenerate_two_node_with_label_is_degenerate():
    # 有边标签但节点只有 2 个 → 仍视为退化（节点数不足）
    assert is_degenerate_mermaid("graph LR\n  A -->|下一步| B") is True


"""----------------- llm_gen 节点：退化图重试 / 降级 -----------------"""

import asyncio
import json

from backend.agent.llm_gen_agent import agent as llm_agent

_BAD = (
    "graph LR\n"
    "root[遍历示意图] -->|前序遍历: NLR| root\n"
    "root -->|中序遍历: LNR| root\n"
    "root -->|后序遍历: LRN| root"
)
_GOOD = (
    "graph LR\n"
    "  A[根] --> B[左子树]\n"
    "  A --> C[右子树]\n"
    "  B --> D[叶子1]\n"
    "  C --> E[叶子2]"
)


def _fenced(code):
    return f"以下是指意图：\n\n```mermaid\n{code}\n```\n"


class _FakeModelManager:
    """按调用次序依次吐出预设文本，用于验证重试逻辑"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def load_model(self):
        return {"status": "ok"}

    async def generate(self, prompt, max_tokens=768, system_prompt=""):
        self.calls += 1
        text = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        yield "data: " + json.dumps({"token": text}, ensure_ascii=False)


_QUERY = "画一张二叉树的遍历示意图"


def test_llm_gen_retries_degenerate_diagram():
    mgr = _FakeModelManager([_fenced(_BAD), _fenced(_GOOD)])
    llm_agent.init_llm_gen_agent(mgr)

    state = asyncio.run(llm_agent.llm_gen_node({"query": _QUERY, "retrieved_context": ""}))

    assert mgr.calls == 2, "退化图应触发且只触发一次重试"
    assert state["needs_image"] is True
    assert "A[根]" in state["image_code"]
    assert "root" not in state["image_code"], "不应沿用退化图"
    assert "```" not in state["generated_text"]


def test_llm_gen_discards_when_retry_still_degenerate():
    mgr = _FakeModelManager([_fenced(_BAD), _fenced(_BAD)])
    llm_agent.init_llm_gen_agent(mgr)

    state = asyncio.run(llm_agent.llm_gen_node({"query": _QUERY, "retrieved_context": ""}))

    assert mgr.calls == 2
    assert not state.get("image_code"), "重试仍退化时应丢弃自绘图"
    assert state["needs_image"] is True, "退回关键词/模板兜底"
    assert "graph LR" not in state["generated_text"], "不应把 Mermaid 源码当正文"
    assert "以下是指意图" in state["generated_text"]



