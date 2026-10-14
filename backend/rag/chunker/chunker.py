"""
自适应分块器

规则：
- 目标大小：300-500 tokens
- 相邻块重叠：50-100 tokens
- 按标题(##)切章节
- 代码块单独成块
- 长段按句子(。！？)切分
- 每个块保存完整元数据
"""
import logging
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import jieba
except ImportError:
    jieba = None


def _estimate_tokens(text: str) -> int:
    """估算文本 token 数（中英文混合）"""
    if jieba:
        words = list(jieba.cut(text))
        return len(words)
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    ascii_words = len(re.findall(r'\b\w+\b', text))
    return int(chinese_chars * 0.6 + ascii_words * 1.3)


def _split_sentences(text: str) -> List[str]:
    """按句子边界分割"""
    parts = re.split(r'(?<=[。！？.!?])\s*', text)
    return [p.strip() for p in parts if p.strip()]


class AdaptiveChunker:
    def __init__(self, target_tokens: int = 400,
                 min_tokens: int = 300,
                 max_tokens: int = 500,
                 overlap_tokens: int = 80):
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, text: str, doc_id: str, doc_name: str,
              pages_info: Optional[List[Dict]] = None,
              sections: Optional[List[Dict]] = None) -> List[Dict[str, Any]]:
        """文档自适应分块

        Returns:
            List of chunk dicts with keys:
                chunk_id, doc_id, doc_name, page, section, text,
                char_start, char_end, is_code
        """
        if sections:
            sections_data = sections
        else:
            heading_matches = list(re.finditer(r'^(#{1,6})\s+(.+)$', text, re.MULTILINE))
            if heading_matches:
                sections_data = []
                for idx, m in enumerate(heading_matches):
                    level = len(m.group(1))
                    h_text = m.group(2).strip()
                    start = m.end()
                    end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(text)
                    body = text[start:end].strip()
                    sections_data.append({"heading": h_text, "level": level,
                                          "text": f"{'#' * level} {h_text}\n\n{body}"})
            else:
                sections_data = [{"heading": "", "level": 0, "text": text}]

        page_map = self._build_page_map(pages_info) if pages_info else {}

        chunks = []
        for sec in sections_data:
            sec_text = sec.get("text", "")
            sec_heading = sec.get("heading", "")
            if not sec_text.strip():
                continue

            code_blocks = list(re.finditer(r'(```[\s\S]*?```|~~~[\s\S]*?~~~)', sec_text))
            if code_blocks:
                last_end = 0
                for cb in code_blocks:
                    before = sec_text[last_end:cb.start()].strip()
                    if before:
                        chunks.extend(self._chunk_text(
                            before, doc_id, doc_name, sec_heading, page_map, len(chunks)))
                    code_text = cb.group(0)
                    start_offset = last_end + (cb.start() - last_end)
                    page = self._find_page(start_offset, page_map)
                    chunks.append({
                        "chunk_id": f"{doc_id}_chunk_{len(chunks):04d}",
                        "doc_id": doc_id, "doc_name": doc_name,
                        "page": page, "section": sec_heading,
                        "text": code_text,
                        "char_start": start_offset,
                        "char_end": start_offset + len(code_text),
                        "is_code": True})
                    last_end = cb.end()
                after = sec_text[last_end:].strip()
                if after:
                    chunks.extend(self._chunk_text(
                        after, doc_id, doc_name, sec_heading, page_map, len(chunks)))
            else:
                chunks.extend(self._chunk_text(
                    sec_text, doc_id, doc_name, sec_heading, page_map, len(chunks)))
        return chunks

    def _chunk_text(self, text: str, doc_id: str, doc_name: str,
                    section: str, page_map: Dict, chunk_idx_offset: int) -> List[Dict]:
        est_tokens = _estimate_tokens(text)
        if est_tokens <= self.max_tokens:
            page = self._find_page(0, page_map)
            return [{"chunk_id": f"{doc_id}_chunk_{chunk_idx_offset:04d}",
                     "doc_id": doc_id, "doc_name": doc_name,
                     "page": page, "section": section,
                     "text": text, "char_start": 0, "char_end": len(text),
                     "is_code": False}]

        sentences = _split_sentences(text)
        chunks = []
        current_batch = []
        current_tokens = 0
        current_chars = 0
        char_offset = 0

        for sent in sentences:
            sent_tokens = _estimate_tokens(sent)
            if current_tokens + sent_tokens > self.max_tokens and current_batch:
                chunk_text = "".join(current_batch)
                page = self._find_page(char_offset - current_chars, page_map)
                chunks.append({"chunk_id": f"{doc_id}_chunk_{chunk_idx_offset + len(chunks):04d}",
                               "doc_id": doc_id, "doc_name": doc_name,
                               "page": page, "section": section,
                               "text": chunk_text,
                               "char_start": char_offset - current_chars,
                               "char_end": char_offset, "is_code": False})
                overlap_sents = []
                overlap_chars = 0
                for osent in reversed(current_batch):
                    otok = _estimate_tokens(osent)
                    if sum(_estimate_tokens(s) for s in overlap_sents) + otok > self.overlap_tokens:
                        break
                    overlap_sents.insert(0, osent)
                    overlap_chars += len(osent)
                current_batch = list(overlap_sents)
                current_tokens = sum(_estimate_tokens(s) for s in current_batch)
                current_chars = sum(len(s) for s in current_batch)
                char_offset -= (sum(len(s) for s in current_batch) - overlap_chars) if overlap_chars else 0

            current_batch.append(sent)
            current_tokens += sent_tokens
            current_chars += len(sent)
            char_offset += len(sent)

        if current_batch:
            chunk_text = "".join(current_batch)
            page = self._find_page(char_offset - current_chars, page_map)
            chunks.append({"chunk_id": f"{doc_id}_chunk_{chunk_idx_offset + len(chunks):04d}",
                           "doc_id": doc_id, "doc_name": doc_name,
                           "page": page, "section": section,
                           "text": chunk_text,
                           "char_start": char_offset - current_chars,
                           "char_end": char_offset, "is_code": False})
        return chunks

    def _build_page_map(self, pages_info: List[Dict]) -> Dict[int, int]:
        page_map = {}
        offset = 0
        for p in pages_info:
            page_map[offset] = p.get("page_num", 1)
            offset += len(p.get("text", "")) + 1
        return page_map

    def _find_page(self, char_offset: int, page_map: Dict) -> int:
        if not page_map:
            return 0
        sorted_offsets = sorted(page_map.keys())
        best = sorted_offsets[0]
        for off in sorted_offsets:
            if off <= char_offset:
                best = off
            else:
                break
        return page_map[best]