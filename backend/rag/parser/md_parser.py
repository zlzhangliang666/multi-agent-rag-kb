"""
Markdown 解析器 - 按标题层级分割
自动移除图片链接 ![](url) 和 HTML 图片标签
"""
import logging
import os
import re

from . import ParsedDocument

logger = logging.getLogger(__name__)


class MDParser:
    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    # 匹配 Markdown 图片 ![](url) 和 ![alt](url) 以及 HTML <img> 标签
    IMAGE_PATTERN = re.compile(r"!\[.*?\]\(.*?\)|<img[^>]*>")

    def parse(self, file_path: str, doc_id: str) -> ParsedDocument:
        file_path = os.path.abspath(file_path)
        content = self._read_file(file_path)
        # 移除图片链接
        content = self.IMAGE_PATTERN.sub("", content)
        sections = self._split_by_headings(content)
        pages = []
        for i, sec in enumerate(sections):
            pages.append({"page_num": i + 1, "text": sec["text"],
                          "heading": sec.get("heading", ""),
                          "heading_level": sec.get("level", 0)})
        return ParsedDocument(doc_id=doc_id, doc_name=os.path.basename(file_path),
                              pages=pages,
                              metadata={"total_sections": len(sections)})

    def _read_file(self, file_path):
        for enc in ["utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"]:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(file_path, "r", encoding="latin-1") as f:
            return f.read()

    def _split_by_headings(self, content):
        matches = list(self.HEADING_PATTERN.finditer(content))
        if not matches:
            return [{"heading": "", "level": 0, "text": content.strip()}]
        sections = []
        for idx, m in enumerate(matches):
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            sections.append({"heading": heading_text, "level": level,
                             "text": f"{'#' * level} {heading_text}\n\n{body}"})
        return sections