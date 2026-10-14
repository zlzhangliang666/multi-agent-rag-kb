"""
BM25 索引 - 使用 jieba 中文分词 + rank_bm25
支持增量添加和按 doc_id 删除，pickle 持久化
"""
import logging
import os
import pickle
from typing import Dict, List, Optional, Any

import jieba

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    return list(jieba.cut(text))


class BM25Index:
    def __init__(self, persist_path: Optional[str] = None):
        from rank_bm25 import BM25Okapi
        self.persist_path = persist_path
        self._bm25: Optional[BM25Okapi] = None
        self._chunks: List[Dict] = []
        self._tokenized: List[List[str]] = []
        if persist_path and os.path.isfile(persist_path):
            self._load()

    def build(self, chunks: List[Dict]):
        self._chunks = list(chunks)
        self._tokenized = [_tokenize(c["text"]) for c in chunks]
        self._rebuild_bm25()
        self._save()
        logger.info("BM25 built with %d chunks", len(self._chunks))

    def add_chunks(self, chunks: List[Dict]):
        self._chunks.extend(chunks)
        self._tokenized.extend(_tokenize(c["text"]) for c in chunks)
        self._rebuild_bm25()
        self._save()
        logger.info("BM25 added %d chunks (total: %d)", len(chunks), len(self._chunks))

    def remove_by_doc_id(self, doc_id: str):
        before = len(self._chunks)
        remaining = [(c, tok) for c, tok in zip(self._chunks, self._tokenized)
                     if c.get("doc_id") != doc_id]
        if len(remaining) == len(self._chunks):
            logger.info("No chunks found for doc=%s", doc_id)
            return
        self._chunks = [c for c, _ in remaining]
        self._tokenized = [t for _, t in remaining]
        self._rebuild_bm25()
        self._save()
        logger.info("BM25 removed %d chunks for doc=%s (total: %d)",
                     before - len(self._chunks), doc_id, len(self._chunks))

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        if self._bm25 is None or not self._chunks:
            return []
        query_tokens = _tokenize(query)
        scores = self._bm25.get_scores(query_tokens)
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        top_indices = [idx for idx, _ in indexed_scores[:top_k] if scores[idx] > 0]
        results = []
        for idx in top_indices:
            chunk = self._chunks[idx]
            results.append({
                "chunk_id": chunk.get("chunk_id", ""),
                "doc_id": chunk.get("doc_id", ""),
                "doc_name": chunk.get("doc_name", ""),
                "text": chunk.get("text", ""),
                "page": chunk.get("page", ""),
                "section": chunk.get("section", ""),
                "score": float(scores[idx]),
            })
        return results

    def get_stats(self) -> Dict[str, Any]:
        return {"total_chunks": len(self._chunks), "persist_path": self.persist_path}

    def _save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        data = {"chunks": self._chunks, "tokenized": self._tokenized}
        with open(self.persist_path, "wb") as f:
            pickle.dump(data, f)

    def _load(self):
        try:
            with open(self.persist_path, "rb") as f:
                data = pickle.load(f)
            self._chunks = data.get("chunks", [])
            self._tokenized = data.get("tokenized", [])
            self._rebuild_bm25()
            logger.info("BM25 loaded: %d chunks", len(self._chunks))
        except Exception as e:
            logger.error("Failed to load BM25: %s", e)
            self._chunks, self._tokenized, self._bm25 = [], [], None

    def _rebuild_bm25(self):
        from rank_bm25 import BM25Okapi
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None