"""自适应分块器测试：markdown 章节切分、代码块单列、元数据完整性。"""
from backend.rag.chunker.chunker import AdaptiveChunker, _estimate_tokens, _split_sentences


def test_small_text_single_chunk_with_metadata():
    chunker = AdaptiveChunker()
    chunks = chunker.chunk("这是一个很短的文档。", "kb_x", "x.md")
    assert len(chunks) == 1
    c = chunks[0]
    assert c["doc_id"] == "kb_x"
    assert c["doc_name"] == "x.md"
    assert c["chunk_id"].startswith("kb_x_chunk_")
    for key in ("text", "page", "section", "char_start", "char_end", "is_code"):
        assert key in c


def test_markdown_headings_split_into_sections():
    chunker = AdaptiveChunker()
    sentence = "这是一个用于测试分块行为的较长句子。"
    text = f"## A\n\n{sentence * 120}\n\n## B\n\n{sentence * 120}"
    chunks = chunker.chunk(text, "kb_d", "d.md")
    sections = {c["section"] for c in chunks}
    assert "A" in sections
    assert "B" in sections
    # 每章都超过 max_tokens(500)，各自应被切成多块
    assert len(chunks) >= 4


def test_code_block_is_isolated():
    chunker = AdaptiveChunker()
    text = "说明文字。\n\n```python\nprint('hi')\n```\n\n更多说明。"
    chunks = chunker.chunk(text, "kb_c", "c.md")
    code_chunks = [c for c in chunks if c["is_code"]]
    assert len(code_chunks) == 1
    assert "print('hi')" in code_chunks[0]["text"]


def test_estimate_tokens_and_sentence_split():
    assert _estimate_tokens("你好世界") > 0
    sents = _split_sentences("第一句。第二句！第三句？")
    assert len(sents) == 3
