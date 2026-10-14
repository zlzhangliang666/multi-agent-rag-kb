"""
PDF 解析器 - 使用 PyMuPDF
自动检测扫描件（无可提取文本），触发 ValueError 由上层跳过
"""
import logging
import os

from . import ParsedDocument

logger = logging.getLogger(__name__)

# 最小有效文本字符数（低于此值视为扫描件/不可解析）
MIN_TEXT_CHARS = 50


class PDFParser:
    def parse(self, file_path: str, doc_id: str) -> ParsedDocument:
        import fitz
        file_path = os.path.abspath(file_path)
        logger.debug("Opening PDF: %s", file_path)
        doc = fitz.open(file_path)
        pages = []
        metadata = {}
        try:
            raw_meta = doc.metadata
            if raw_meta:
                metadata = {k: v for k, v in raw_meta.items() if v is not None}
        except Exception:
            pass
        metadata["total_pages"] = len(doc)
        total_text_chars = 0
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text("text").strip()
            total_text_chars += len(text)
            pages.append({"page_num": page_num + 1, "text": text})
        doc.close()

        # 检测扫描件：如果总字符数太少，判定为扫描件
        if total_text_chars < MIN_TEXT_CHARS:
            logger.warning("PDF scanned or no text: %s (only %d chars)", file_path, total_text_chars)
            raise ValueError(
                f"PDF has insufficient extractable text ({total_text_chars} chars < {MIN_TEXT_CHARS}): "
                f"likely a scanned document, skipped"
            )

        doc_name = os.path.basename(file_path)
        return ParsedDocument(doc_id=doc_id, doc_name=doc_name,
                              pages=pages, metadata=metadata)