"""
引用归因（Citation Attribution）

背景问题
--------
检索会返回 Top-k 片段，接口把它们**整批**当作「引用来源」展示。
但模型未必真的用到了每一条 —— 「检索到」不等于「被引用」。
这会让引用数量虚高、引用正确率偏低，也会误导用户以为回答有出处。

本模块做一件很轻的事：判断每条来源是否**被回答实际使用**，只保留用到的。

判定方式（诚实记录）
------------------
采用「回答句 → 来源词面覆盖」的轻量启发式，**不做严格语义蕴含（NLI）**：
  - 把回答切句，每句提取内容词（jieba 分词，去停用词）；
  - 若某句的内容词有 >= threshold 的比例能在该来源（正文+章节）中找到，
    就认为该句由这条来源支撑，进而认为这条来源被使用。
这是**词面代理指标**，会漏掉「模型改写/概括」的情况，因此：
  1. 阈值取得偏宽松（默认 0.35）；
  2. 至少保留 min_keep 条（默认 1），避免全部判为未使用导致引用为空。
"""
import logging

logger = logging.getLogger(__name__)

# 判定阈值：回答句中内容词能被来源覆盖的最低比例
DEFAULT_THRESHOLD = 0.35
# 至少保留的引用条数
DEFAULT_MIN_KEEP = 1
# 引用正文入库/推送时的截断长度
SOURCE_TEXT_LIMIT = 300

_STOP = set(
    "的 了 和 是 在 有 也 就 都 而 及 与 着 或 一个 我们 你们 他们 它们 这 那 "
    "对于 以及 等等 可以 因为 所以 但是 如果 那么 这个 那个 这些 那些 因此 从而 "
    "进行 需要 通过 使用 采用 主要 一般 通常 表示 称为 属于 一种 每个 各个 如下 上述 "
    "the a an of to in on for and or is are be by with as at".split()
)


def _content_tokens(text: str) -> set:
    """提取内容词：长度 >= 2、含中英文字母、非停用词"""
    import jieba
    jieba.setLogLevel(logging.WARNING)

    import re
    toks = [t.strip() for t in jieba.cut(text or "")]
    return {
        t for t in toks
        if len(t) >= 2
        and re.search(r"[\u4e00-\u9fffA-Za-z]", t)
        and t.lower() not in _STOP
    }


def _split_sentences(text: str) -> list:
    import re
    parts = re.split(r"[。！？!?；;\n]+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def is_source_used(source: dict, answer_sentences: list, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """判断单条来源是否被回答使用

    :param source: 来源字典，取 text / section 参与匹配
    :param answer_sentences: 回答切句后的每句内容词集合（预计算，避免重复分词）
    :param threshold: 句级覆盖阈值
    """
    src_tokens = _content_tokens(f"{source.get('section', '')} {source.get('text', '')}")
    if not src_tokens:
        return False
    for sent_tokens in answer_sentences:
        if len(sent_tokens) < 3:
            continue
        cov = len(sent_tokens & src_tokens) / len(sent_tokens)
        if cov >= threshold:
            return True
    return False


def attribute_sources(answer: str, sources: list,
                      threshold: float = DEFAULT_THRESHOLD,
                      min_keep: int = DEFAULT_MIN_KEEP) -> list:
    """只保留被回答真正使用的来源

    :param answer: 模型回答（无回答时不做过滤，原样返回）
    :param sources: 来源列表（每条含 doc_name/page/section/score/chunk_id/可选的 text）
    :param threshold: 句级覆盖阈值
    :param min_keep: 全部未命中时至少保留的前 N 条（按原顺序，检索已按分数降序）
    :return: 过滤后的来源列表（元素为原字典的浅拷贝并带 used 标记）
    """
    items = list(sources or [])
    if not items:
        return []
    if not (answer or "").strip():
        return items

    sentences = [_content_tokens(s) for s in _split_sentences(answer)]
    sentences = [s for s in sentences if s]
    if not sentences:
        return items

    kept = []
    for src in items:
        if is_source_used(src, sentences, threshold):
            kept.append({**src, "used": True})
    if len(kept) >= max(min_keep, 1):
        logger.info("[Citation] 归因完成: %d/%d 条被使用", len(kept), len(items))
        return kept

    # 未命中（或命中不足）：保留分数最高的前 min_keep 条作为兜底
    fallback = [{**s, "used": False} for s in items[:max(min_keep, 1)]]
    logger.info("[Citation] 全部未命中，回退保留前 %d 条", len(fallback))
    return fallback
