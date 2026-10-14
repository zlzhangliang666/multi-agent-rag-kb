"""
TXT 解析器
"""
import logging
import os
import re

from . import ParsedDocument

logger = logging.getLogger(__name__)

_ENCODING_CANDIDATES = ["utf-8", "utf-8-sig", "gbk", "gb2312",
                        "gb18030", "big5", "shift_jis", "euc-kr", "latin-1"]


def _detect_encoding(file_path):
    try:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read(8192)
        result = chardet.detect(raw)
        if result and result.get("encoding"):
            try:
                with open(file_path, "r", encoding=result["encoding"]) as f:
                    f.read(1024)
                return result["encoding"]
            except (UnicodeDecodeError, LookupError):
                pass
    except ImportError:
        pass
    for enc in _ENCODING_CANDIDATES:
        try:
            with open(file_path, "r", encoding=enc) as f:
                f.read(1024)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


class TxtParser:
    def parse(self, file_path: str, doc_id: str) -> ParsedDocument:
        file_path = os.path.abspath(file_path)
        encoding = _detect_encoding(file_path)
        with open(file_path, "r", encoding=encoding) as f:
            content = f.read()
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
        pages = [{"page_num": i + 1, "text": para}
                 for i, para in enumerate(paragraphs)]
        return ParsedDocument(doc_id=doc_id, doc_name=os.path.basename(file_path),
                              pages=pages,
                              metadata={"encoding": encoding,
                                        "total_paragraphs": len(paragraphs)})