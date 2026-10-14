"""
生成 Agent - 使用大模型生成回答（流式）
注意：实际流式由 API 层处理，Agent 节点负责准备提示词和记录日志
"""
import logging
import re
import time

from backend.agent.state.state import AgentState
from backend.agent.agent_log import make_log

logger = logging.getLogger(__name__)

# 全局模型管理器引用（由外部注入）
_model_manager = None


def init_llm_gen_agent(model_manager):
    global _model_manager
    _model_manager = model_manager
    logger.info("LLM Gen agent initialized")


_MERMAID_BLOCK_RE = re.compile(r"```[ \t]*mermaid[ \t]*\r?\n([\s\S]*?)```", re.IGNORECASE)

_DRAW_INSTRUCTION = (
    "【绘图任务】如果用户要求画图（流程图、示意图、架构图、结构图、层次结构、拓扑图、时序图等），"
    "你必须直接用 Mermaid 语法画出示意图；即使参考资料中没有相关内容，也要根据常识画出来，"
    "绝对不要回答“参考资料不足”。输出格式必须是（放在回答最后）：\n"
    "```mermaid\n<Mermaid 源码>\n```\n"
    "语法要点（必须严格遵守）：用 `graph LR` 或 `graph TD` 开头；"
    "节点写成 `A[文字]` 或 `A(文字)`；箭头只能写 `-->`（禁止 `->`、`-- >`）；"
    "边上的文字写成 `A -->|文字| B`，禁止写成 `A --> B: 文字`。"
    "示例：graph LR 换行 A[开始] -->|下一步| B[结束]。"
    "节点文字用中文且简短，整段源码必须能被 Mermaid 正确解析。"
)

_STRICT_DRAW_INSTRUCTION = (
    "【重画要求】上一次的示意图不合格：节点太少，或所有箭头都是自环（例如 A-->A 重复出现），"
    "信息量为零。请重新画一张结构完整的图：至少 5 个节点，节点之间是真实的层级/流程关系；"
    "严禁出现 a-->a 这种自环，严禁反复用同一个节点充当所有边的起点与终点。"
    "若问题涉及遍历/步骤顺序，请把每种顺序分别画成独立路径。"
)

# 常见箭头笔误：`-- >` / `- ->` 等带空格的箭头，以及单横线 `->`
_ARROW_SPACE_RE = re.compile(r"-\s*-\s*>")
_SINGLE_ARROW_RE = re.compile(r"(?<![-.])->")
# 非法写法 `A --> B: 文字`（尾随冒号标签）→ `A -->|文字| B`
_COLON_EDGE_LABEL_RE = re.compile(
    r"^(?P<left>.*?)\s*-{2,3}>\s*(?P<dst>[^\s:|]+)\s*[:：]\s*(?P<label>.+?)\s*$",
    re.MULTILINE,
)
# 非法写法 `A -->|标签| B: 补充`（标签后多出冒号内容）→ 并回标签 `A -->|标签: 补充| B`
_COLON_AFTER_BARS_RE = re.compile(
    r"^(?P<head>.*?-->\|)(?P<label>[^|\n]*)(?P<tail>\|)\s*(?P<dst>[^\s:|]+)\s*[:：]\s*(?P<extra>.+?)\s*$",
    re.MULTILINE,
)


def sanitize_mermaid_code(code: str) -> str:
    """修正常见 Mermaid 语法笔误，避免模型自绘时前端词法报错

    - `-- >` / `- ->` 等带空格的箭头 → `-->`
    - 单横线箭头 `->` → `-->`（保留合法的 `-->` 与虚线 `-.->`）
    - 尾随冒号标签 `A --> B: 文字` → `A -->|文字| B`
    - 标签后多出冒号 `A -->|标签| B: 补充` → `A -->|标签: 补充| B`
    """
    text = str(code or "")
    text = _ARROW_SPACE_RE.sub("-->", text)
    text = _SINGLE_ARROW_RE.sub("-->", text)
    text = _COLON_EDGE_LABEL_RE.sub(
        lambda m: f"{m.group('left')} -->|{m.group('label')}| {m.group('dst')}", text
    )
    text = _COLON_AFTER_BARS_RE.sub(_merge_extra_into_label, text)
    return text


def _merge_extra_into_label(m):
    label = (m.group("label") or "").strip()
    extra = (m.group("extra") or "").strip()
    merged = f"{label}: {extra}" if label else extra
    return f"{m.group('head')}{merged}| {m.group('dst')}"


def extract_mermaid_blocks(text: str):
    """从模型回答中抽出 ```mermaid 代码块

    :return: (去掉代码块后的文本, [mermaid 源码, ...])
    """
    blocks = []

    def _take(match):
        code = (match.group(1) or "").strip()
        if code:
            blocks.append(code)
        return ""

    cleaned = _MERMAID_BLOCK_RE.sub(_take, text or "")
    return cleaned.strip(), blocks


# 节点文字（`A[文字]` / `A(文字)` / `A{文字}`）与边标签（`|文字|`）
_NODE_TEXT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_EDGE_LABEL_RE = re.compile(r"\|[^|\n]*\|")


def _edge_endpoints(code: str):
    """从 Mermaid 源码里粗解析出所有边的 (起点, 终点)，用于退化图判定

    只做轻量文本解析（不引入 Mermaid 解析器）：
    先抹掉节点文字与边标签，再按 `-->` 切分每行取两端节点 id。
    """
    text = str(code or "")
    text = _EDGE_LABEL_RE.sub(" ", text)
    text = _NODE_TEXT_RE.sub("", text)

    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if "-->" not in line:
            continue
        parts = [p.strip() for p in line.split("-->")]
        src = parts[0].split()[-1] if parts[0].split() else ""
        for nxt in parts[1:]:
            tokens = nxt.split()
            dst = tokens[0] if tokens else ""
            if src and dst:
                pairs.append((src, dst))
            src = dst or src
    return pairs


def is_degenerate_mermaid(code: str) -> bool:
    """判断自绘 Mermaid 是否"退化"（无信息量）

    退化条件（满足其一）：
    - 没有任何边；
    - 涉及节点数 < 3（如只有一个节点）；
    - 所有边都是自环（如 A-->A 反复出现）。
    """
    pairs = _edge_endpoints(code)
    if not pairs:
        return True
    nodes = {node for pair in pairs for node in pair}
    if len(nodes) < 3:
        return True
    if all(a == b for a, b in pairs):
        return True
    return False


def build_prompt(query: str, context: str, want_draw: bool = False, strict_draw: bool = False) -> str:
    """构建带上下文的提示词

    :param want_draw: 仅在确认「用户要画图」时才下发绘图指令，
        避免普通问答也被模型画成图并清空正文。
    :param strict_draw: 自绘图被判定退化后重试时下发，要求画出结构完整的图。
    """
    parts = []
    if want_draw:
        parts.append(_DRAW_INSTRUCTION)
    if strict_draw:
        parts.append(_STRICT_DRAW_INSTRUCTION)
    if context:
        parts.append(f"参考资料：\n{context}")
        parts.append(f"用户问题：{query}")
        parts.append("请用中文回答，注明信息来源；参考资料不足时如实说明。")
    else:
        parts.append(f"用户问题：{query}")
    return "\n\n".join(parts)


def should_use_image(query: str) -> bool:
    """判断是否需要生成示意图（关键词 + 画图动词匹配）"""
    q = query.lower()

    # 图表类名词
    diagram_nouns = [
        "流程图", "示意图", "架构图", "结构图", "拓扑", "层次结构",
        "关系图", "状态图", "时序图", "类图", "用例图", "思维导图",
        "过程图", "框图", "原理图", "序列图", "泳道图", "甘特图",
        "饼图", "柱状图", "折线图", "对象图", "部署图", "组网图",
        "七层模型", "osi", "tcp/ip", "协议栈", "状态转换图",
        "flowchart", "diagram", "architecture", "topology",
    ]
    for kw in diagram_nouns:
        if kw in q:
            return True

    # 画图类动词表达
    draw_verbs = [
        "画个图", "画一张", "画一个", "画一下", "画出来", "画幅",
        "画图", "画出", "画下", "图示", "配个图",
        "生成图片", "生成一张图", "生成图", "配图", "作图", "绘制",
        "draw", "plot",
    ]
    for kw in draw_verbs:
        if kw in q:
            return True

    return False


def has_image_subject(query: str) -> bool:
    """判断画图请求是否指明了具体对象

    "画个图" → False（无对象）
    "画一个操作系统层次结构" → True
    """
    q = query.lower()
    # 可绘图的具体主题关键词（示例语料相关；换语料时可自行调整）
    # 注意：不能放单字"图"，否则"画个图"会误判为有对象
    subjects = [
        "操作系统", "层次结构", "死锁", "进程", "线程", "中断", "调度",
        "内存", "页面", "文件系统", "io", "i/o", "磁盘",
        "网络", "tcp", "ip", "osi", "七层", "协议", "握手",
        "数据结构", "二叉树", "树与", "排序", "查找", "b+", "b树",
        "邻接矩阵", "遍历", "有向图", "无向图", "最小生成树", "最短路径",
        "组成原理", "cache", "cpu", "流水线", "总线", "指令",
        "存储系统", "控制器", "运算器", "浮点数", "补码",
    ]
    return any(s in q for s in subjects)


async def llm_gen_node(state: AgentState) -> AgentState:
    """生成节点 - 调用大模型生成回答（async，支持 ainvoke）"""
    t0 = time.time()
    query = state.get("query", "")
    context = state.get("retrieved_context", "")
    want_draw = should_use_image(query)

    prompt = build_prompt(query, context, want_draw=want_draw)

    if _model_manager is None:
        logger.error("LLM Gen agent not initialized")
        state["error"] = "Model manager not initialized"
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("llm_gen_agent", "LLM生成", prompt[:100],
                     "error: not initialized", "error", time.time() - t0)
        ]
        return state

    logger.info("[LLMGenAgent] 生成回答, context_len=%d", len(context))

    try:
        # 确保模型已加载（懒加载）
        load_result = await _model_manager.load_model()
        if load_result.get("status") != "ok":
            err_msg = load_result.get("message", "模型加载失败")
            logger.error("[LLMGenAgent] 模型加载失败: %s", err_msg)
            state["generated_text"] = f"模型加载失败：{err_msg}"
            state["error"] = err_msg
            state["agent_logs"] = state.get("agent_logs", []) + [
                make_log("llm_gen_agent", "加载模型", "",
                         f"error: {err_msg}", "error", time.time() - t0)
            ]
            return state

        async def _run_generate(gen_prompt: str):
            """跑一次模型生成，返回 (完整文本, worker 错误信息)"""
            import json as _json
            parts = []
            err = None
            async for event in _model_manager.generate(
                prompt=gen_prompt, max_tokens=768,
                system_prompt=("你是知识库问答助手，请基于给定参考资料用中文回答问题，不要编造。"
                               "但当用户明确要求画图时，请按用户要求绘制 Mermaid 示意图，"
                               "不要以“参考资料不足”为由拒绝。")
            ):
                if event.startswith("data: "):
                    try:
                        data = _json.loads(event[6:])
                    except _json.JSONDecodeError:
                        continue
                    if data.get("status") == "error":
                        err = data.get("message", "未知生成错误")
                        continue
                    token = data.get("token", "")
                    if token:
                        parts.append(token)
            return "".join(parts), err

        full_text, worker_error = await _run_generate(prompt)

        # 生成失败时不要静默返回 0 字，必须暴露错误
        if not full_text:
            err = worker_error or "模型未返回任何内容"
            logger.error("[LLMGenAgent] 生成为空: %s", err)
            state["generated_text"] = ""
            state["error"] = err
            state["agent_logs"] = state.get("agent_logs", []) + [
                make_log("llm_gen_agent", "LLM生成", prompt[:100],
                         f"error: {err}", "error", time.time() - t0)
            ]
            return state

        # 方案 B：模型自绘优先 —— 抽取回答中的 ```mermaid 代码块
        cleaned, mermaid_blocks = extract_mermaid_blocks(full_text)
        image_code = sanitize_mermaid_code(mermaid_blocks[0]) if mermaid_blocks else ""
        discarded = False
        retried = False

        # 方案 B 自检：自绘图退化（节点过少 / 全为自环）时重试一次
        if image_code and is_degenerate_mermaid(image_code):
            retried = True
            logger.warning("[LLMGenAgent] 自绘图为退化图，重试一次：%s", image_code.replace("\n", " ")[:120])
            retry_text, _ = await _run_generate(
                build_prompt(query, context, want_draw=True, strict_draw=True)
            )
            if retry_text:
                r_cleaned, r_blocks = extract_mermaid_blocks(retry_text)
                r_code = sanitize_mermaid_code(r_blocks[0]) if r_blocks else ""
                full_text, cleaned, mermaid_blocks, image_code = retry_text, r_cleaned, r_blocks, r_code
            if not (image_code and not is_degenerate_mermaid(image_code)):
                # 重试仍无有效图 → 丢弃自绘图，退回关键词/模板兜底或纯文字
                logger.warning("[LLMGenAgent] 重试后仍无有效示意图，降级处理")
                mermaid_blocks, image_code, discarded = [], "", True

        if image_code:
            state["generated_text"] = cleaned
            state["image_code"] = image_code
            state["image_type"] = "mermaid"
            state["needs_image"] = True
        else:
            # 丢弃过自绘图时用去掉代码块的正文，避免把 Mermaid 源码当正文展示
            state["generated_text"] = cleaned if discarded else full_text
            # 关键词兜底：命中图表类词汇时仍走模板绘图节点
            state["needs_image"] = want_draw

        elapsed = time.time() - t0
        logger.info("[LLMGenAgent] 生成完成, len=%d, mermaid=%s, retried=%s, needs_image=%s, 耗时%.2fs",
                     len(full_text), bool(image_code), retried, state["needs_image"], elapsed)
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("llm_gen_agent", "LLM生成", prompt[:100],
                     f"生成{len(full_text)}字"
                     + ("（含 Mermaid）" if image_code else "")
                     + ("（退化图已重试）" if retried else ""),
                     "success", elapsed)
        ]

    except Exception as e:
        logger.error("[LLMGenAgent] 生成失败: %s", e)
        state["generated_text"] = f"生成回答时出错：{e}"
        state["error"] = str(e)
        state["agent_logs"] = state.get("agent_logs", []) + [
            make_log("llm_gen_agent", "LLM生成", prompt[:100],
                     f"error: {e}", "error", time.time() - t0)
        ]

    return state