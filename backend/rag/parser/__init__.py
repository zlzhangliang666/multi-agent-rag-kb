"""
RAG Parser Package
"""
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


class ParsedDocument:
    def __init__(self, doc_id: str, doc_name: str, pages: List[dict],
                 metadata: Optional[dict] = None):
        self.doc_id = doc_id
        self.doc_name = doc_name
        self.pages = pages
        self.metadata = metadata or {}

    def get_full_text(self) -> str:
        return "\n".join(p.get("text", "") for p in self.pages)

    def get_page_count(self) -> int:
        return len(self.pages)

    def __repr__(self):
        return (f"ParsedDocument(doc_id={self.doc_id!r}, doc_name={self.doc_name!r}, "
                f"pages={len(self.pages)})")


_PARSER_MAP = {}
_SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})


def _get_parser_map():
    if not _PARSER_MAP:
        from .pdf_parser import PDFParser
        from .docx_parser import DocxParser
        from .txt_parser import TxtParser
        from .md_parser import MDParser
        _PARSER_MAP.update({".pdf": PDFParser, ".docx": DocxParser,
                            ".txt": TxtParser, ".md": MDParser})
    return _PARSER_MAP


def parse_document(file_path: str, doc_id: str) -> ParsedDocument:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Document not found: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported extension '{ext}'")
    parser_cls = _get_parser_map()[ext]
    parser = parser_cls()
    logger.info("Parsing %s with %s", file_path, parser_cls.__name__)
    result = parser.parse(file_path, doc_id)
    logger.info("Parsed %s: %d pages, %d chars", file_path,
                result.get_page_count(), len(result.get_full_text()))
    return result