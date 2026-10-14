"""
知识库文档 ID 约定 —— 唯一权威实现

init_knowledge_base.py（笔记）与 import_ppt.py（课件）都必须通过本模块生成 doc_id，
以保证「同一份源码 → 同一套 doc_id」，重建结果可稳定复现、不产生重复文档。

规范化规则（对所有来源段统一生效）：
    - 路径分隔符 / 与 \\ 统一转下划线 "_"
    - 连续空白字符（空格 / 制表 / 换行）统一转下划线 "_"
    - 去掉文件扩展名

doc_id 形态：
    笔记  : kb_<相对数据源根目录的路径>
    课件  : kb_<学科>_ppt_<章节>_<文件名>

示例：
    操作系统/第1章 计算机系统概述.md
        -> kb_操作系统_第1章_计算机系统概述
    计算机组成原理/ppt/第2章 数据的表示和运算/2.1.1 进位计数制.pdf
        -> kb_计算机组成原理_ppt_第2章_数据的表示和运算_2.1.1_进位计数制
"""
import os
import re

PREFIX = "kb_"
_WHITESPACE = re.compile(r"\s+")


def _norm(segment: str) -> str:
    """把一段文本规范化为 doc_id 片段：路径分隔符与空白字符统一为 '_'"""
    segment = segment.replace("\\", "/").replace("/", "_")
    return _WHITESPACE.sub("_", segment)


def note_doc_id(root_dir: str, file_path: str) -> str:
    """笔记 doc_id：kb_<相对 root_dir 的路径>"""
    rel = os.path.relpath(file_path, root_dir)
    rel = os.path.splitext(rel)[0]
    return PREFIX + _norm(rel)


def ppt_doc_id(subject: str, chapter: str, file_path: str) -> str:
    """课件 doc_id：kb_<学科>_ppt_<章节>_<文件名>"""
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return f"{PREFIX}{_norm(subject)}_ppt_{_norm(chapter)}_{_norm(stem)}"