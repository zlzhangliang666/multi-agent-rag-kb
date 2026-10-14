"""引用正确率提升相关测试

覆盖三块改动：
  1. 引用归因（backend/agent/citation.py）——只保留回答真正用到的来源
  2. 相对分数阈值截断（backend/agent/retrieval_agent/agent.py:cut_low_score）
  3. 评测口径修正（scripts/evaluate_answer_quality.py:citation_metrics）——纳入引用正文
"""
import importlib.util
import os

from backend.agent.citation import attribute_sources, is_source_used
from backend.agent.retrieval_agent.agent import cut_low_score

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_eval_module():
    """按文件路径加载评测脚本（避免依赖 scripts 作为包）"""
    path = os.path.join(ROOT, "scripts", "evaluate_answer_quality.py")
    spec = importlib.util.spec_from_file_location("eval_answer_quality", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- 引用归因

def _src(doc="第7章 查找.pdf", section="", text="", score=0.03):
    return {"doc_name": doc, "page": 1, "section": section, "text": text, "score": score}


def test_attribute_keeps_used_and_drops_unused():
    answer = "哈希表处理冲突常用链地址法和开放定址法，前者把同义词串成链表。"
    used = _src(text="哈希表处理冲突的方法包括链地址法和开放定址法，即同义词链表。")
    unused = _src(doc="第3章 栈和队列.pdf", text="队列是先进先出的线性表，只允许队尾插入队头删除。")
    out = attribute_sources(answer, [used, unused])
    assert len(out) == 1
    assert out[0]["doc_name"] == "第7章 查找.pdf"
    assert out[0]["used"] is True


def test_attribute_falls_back_to_min_keep_when_nothing_matches():
    answer = "抱歉，参考资料不足，无法回答该问题。"
    a = _src(doc="A.pdf", text="与回答完全无关的内容，涉及操作系统进程调度。")
    b = _src(doc="B.pdf", text="另一段无关内容，涉及计算机网络的分层结构。")
    out = attribute_sources(answer, [a, b], min_keep=1)
    assert len(out) == 1
    assert out[0]["doc_name"] == "A.pdf"      # 兜底保留最高分（原顺序第一条）
    assert out[0]["used"] is False


def test_attribute_passthrough_on_empty_inputs():
    assert attribute_sources("任意回答", []) == []
    srcs = [_src(text="内容")]
    assert attribute_sources("", srcs) == srcs          # 无回答不过滤
    assert attribute_sources("问题太短", srcs) == srcs   # 无有效句不过滤


def test_is_source_used_requires_overlap():
    # 用真实分词结果构造「回答句」，避免手写 token 与 jieba 结果不一致
    from backend.agent.citation import _content_tokens
    sentences = [_content_tokens("链地址法处理哈希表冲突")]
    assert is_source_used(_src(text="链地址法可以处理哈希表冲突，是最常用的方法"), sentences) is True
    assert is_source_used(_src(text="二叉树的中序遍历按左根右的顺序访问节点"), sentences) is False


# ----------------------------------------------------------- 分数阈值截断

def test_cut_low_score_filters_relative_low():
    rows = [{"score": 1.0}, {"score": 0.8}, {"score": 0.3}, {"score": 0.1}]
    out = cut_low_score(rows, min_ratio=0.45, min_keep=2)
    assert [r["score"] for r in out] == [1.0, 0.8]


def test_cut_low_score_keeps_min_sources():
    rows = [{"score": 1.0}, {"score": 0.01}, {"score": 0.005}]
    out = cut_low_score(rows, min_ratio=0.45, min_keep=2)
    assert len(out) == 2


def test_cut_low_score_noop_when_short_or_zero_top():
    rows = [{"score": 0.5}]
    assert cut_low_score(rows, 0.45, 2) == rows
    zeros = [{"score": 0.0}, {"score": 0.0}]
    assert cut_low_score(zeros, 0.45, 1) == zeros


# ------------------------------------------------------------- 评测口径

def test_citation_metrics_includes_source_text():
    mod = _load_eval_module()
    keywords = ["哈希", "散列", "冲突"]
    # 文件名/章节名不含关键词，但引用正文含 → 新口径算相关，旧口径不算
    sources = [{"doc_name": "第7章 查找.pdf", "section": "", "text": "哈希表处理冲突的方法"}]
    m = mod.citation_metrics(sources, keywords)
    assert m["precision"] == 1.0
    assert m["precision_name"] == 0.0
    assert m["covered"] is True


def test_citation_metrics_empty_sources():
    mod = _load_eval_module()
    m = mod.citation_metrics([], ["哈希"])
    assert m["precision"] == 0.0 and m["total"] == 0 and m["covered"] is False
