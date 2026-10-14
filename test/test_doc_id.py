"""doc_id 约定测试：路径分隔符 / 空白字符统一转下划线、去扩展名。"""
import os

from backend.rag.doc_id import note_doc_id, ppt_doc_id


def test_note_doc_id_strips_ext_and_normalizes():
    root = os.path.join("E:", os.sep, "thesis", "408-master")
    path = os.path.join(root, "操作系统", "第1章 计算机系统概述.md")
    assert note_doc_id(root, path) == "kb_操作系统_第1章_计算机系统概述"


def test_note_doc_id_nested_dirs():
    root = os.path.join("E:", os.sep, "thesis", "408-master")
    path = os.path.join(root, "计算机网络", "第5章 传输层.md")
    assert note_doc_id(root, path) == "kb_计算机网络_第5章_传输层"


def test_ppt_doc_id_keeps_dots_normalizes_spaces():
    path = os.path.join("x", "计算机组成原理", "ppt", "第2章 数据的表示和运算",
                        "2.3.1_2_IEEE 754.pdf")
    d = ppt_doc_id("计算机组成原理", "第2章 数据的表示和运算", path)
    assert d == "kb_计算机组成原理_ppt_第2章_数据的表示和运算_2.3.1_2_IEEE_754"


def test_doc_id_has_no_space():
    path = os.path.join("x", "数据结构", "a b c.pdf")
    assert " " not in ppt_doc_id("数据结构", "第1章", path)
