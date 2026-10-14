"""文档解析器测试：TXT / Markdown / 不支持扩展名。"""
import pytest

from backend.rag.parser import parse_document


def test_parse_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第一段。\n\n第二段。", encoding="utf-8")
    doc = parse_document(str(p), "kb_txt")
    assert doc.doc_name == "a.txt"
    assert doc.get_page_count() == 2
    assert "第一段。" in doc.get_full_text()


def test_parse_markdown_sections(tmp_path):
    p = tmp_path / "m.md"
    p.write_text("# 标题\n\n正文内容\n\n## 小节\n\n更多内容", encoding="utf-8")
    doc = parse_document(str(p), "kb_md")
    assert doc.get_page_count() >= 2
    assert "标题" in doc.get_full_text()


def test_parse_rejects_unsupported_ext(tmp_path):
    p = tmp_path / "a.xyz"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_document(str(p), "kb_bad")


def test_parse_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_document("E:/not/exist/file.txt", "kb_none")
