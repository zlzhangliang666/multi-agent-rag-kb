"""
RRF 混合检索器

向量 Top10 + BM25 Top10 → RRF(k=60) 融合 → 最终 Top4
返回内容、来源文档、页码、段落索引、融合分数
"""
import logging
import time
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class HybridRetriever:
    RRF_K = 60

    def __init__(self, vector_store, bm25_index, embedding_model):
        self.vector_store = vector_store
        self.bm25_index = bm25_index
        self.embedding_model = embedding_model

    def retrieve(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        start_time = time.perf_counter()

        query_emb = self.embedding_model.encode([query])
        vec_results = self.vector_store.search(query_emb, top_k=10)
        bm25_results = self.bm25_index.search(query, top_k=10)

        fused = self._rrf_fuse(vec_results, bm25_results)
        fused.sort(key=lambda x: x["score"], reverse=True)
        final = fused[:top_k]

        elapsed = time.perf_counter() - start_time
        logger.info("Hybrid retrieval: query=%r, vec=%d, bm25=%d, final=%d, time=%.3fs",
                     query[:50], len(vec_results), len(bm25_results), len(final), elapsed)
        return final

    def _rrf_fuse(self, vec_results: List[Dict],
                  bm25_results: List[Dict]) -> List[Dict[str, Any]]:
        vec_ranks = {r["id"]: rank + 1 for rank, r in enumerate(vec_results)}
        bm25_ranks = {r["chunk_id"]: rank + 1 for rank, r in enumerate(bm25_results)}
        all_ids = set(vec_ranks.keys()) | set(bm25_ranks.keys())

        result_map = {}
        for r in vec_results:
            result_map[r["id"]] = {
                "text": r.get("text", ""), "doc_id": r.get("doc_id", ""),
                "doc_name": r.get("doc_name", ""), "page": r.get("page", ""),
                "section": r.get("section", ""), "chunk_id": r["id"],
                "vec_rank": vec_ranks.get(r["id"], 9999),
                "bm25_rank": bm25_ranks.get(r["id"], 9999), "score": 0.0,
            }
        for r in bm25_results:
            cid = r["chunk_id"]
            if cid not in result_map:
                result_map[cid] = {
                    "text": r.get("text", ""), "doc_id": r.get("doc_id", ""),
                    "doc_name": r.get("doc_name", ""), "page": r.get("page", ""),
                    "section": r.get("section", ""), "chunk_id": cid,
                    "vec_rank": vec_ranks.get(cid, 9999),
                    "bm25_rank": bm25_ranks.get(cid, 9999), "score": 0.0,
                }
        for cid, entry in result_map.items():
            entry["score"] = (1.0 / (self.RRF_K + entry["vec_rank"]) +
                              1.0 / (self.RRF_K + entry["bm25_rank"]))
        return list(result_map.values())