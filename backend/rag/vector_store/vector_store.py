"""
Chroma 向量库 - 本地持久化
增删改查，删除按 doc_id
支持分批导入 + 自定义 HNSW 参数避免并发压缩错误
"""
import logging
import os
from typing import Dict, List, Optional, Any
import numpy as np

logger = logging.getLogger(__name__)

# 分批导入批次大小（每个文件整体写入，不分小批，减少 segment 数量）
BATCH_SIZE = 9999
HNSW_PARAMS = {"hnsw:space": "cosine", "hnsw:construction_ef": 100, "hnsw:M": 24}


class ChromaStore:
    def __init__(self, persist_dir: str, collection_name: str = "kb_collection"):
        import chromadb
        self.persist_dir = os.path.abspath(persist_dir)
        self.collection_name = collection_name
        os.makedirs(self.persist_dir, exist_ok=True)
        logger.info("Initializing ChromaDB at: %s", self.persist_dir)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        try:
            self.collection = self.client.get_collection(collection_name)
            logger.info("Reusing collection: %s", collection_name)
        except Exception:
            # 创建时指定 HNSW 参数，避免压缩大索引出错
            self.collection = self.client.create_collection(
                collection_name, metadata=HNSW_PARAMS
            )
            logger.info("Created collection: %s with HNSW params: %s", collection_name, HNSW_PARAMS)

    def add_chunks(self, chunks: List[Dict], embeddings: np.ndarray):
        """分批导入 chunks，避免 HNSW 索引合并错误"""
        if not chunks or len(embeddings) == 0:
            return

        ids = [c["chunk_id"] for c in chunks]
        texts = [c["text"] for c in chunks]
        metadatas = [{"doc_id": c.get("doc_id", ""),
                      "doc_name": c.get("doc_name", ""),
                      "page": str(c.get("page", "")),
                      "section": c.get("section", ""),
                      "char_start": str(c.get("char_start", "")),
                      "char_end": str(c.get("char_end", ""))} for c in chunks]
        embs = embeddings.tolist()

        # 分批写入，每批 BATCH_SIZE 条
        total = len(ids)
        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            self.collection.add(
                ids=ids[batch_start:batch_end],
                embeddings=embs[batch_start:batch_end],
                metadatas=metadatas[batch_start:batch_end],
                documents=texts[batch_start:batch_end],
            )
        logger.info("Added %d chunks to '%s' (batch_size=%d)", total, self.collection_name, BATCH_SIZE)

    def delete_by_doc_id(self, doc_id: str):
        results = self.collection.get(where={"doc_id": doc_id})
        if results and results.get("ids"):
            self.collection.delete(ids=results["ids"])
            logger.info("Deleted %d chunks for doc=%s", len(results["ids"]), doc_id)
        else:
            logger.info("No chunks found for doc=%s", doc_id)

    def search(self, embedding: np.ndarray, top_k: int = 10) -> List[Dict[str, Any]]:
        if embedding.ndim == 1:
            embedding = embedding.reshape(1, -1)
        results = self.collection.query(
            query_embeddings=embedding.tolist(),
            n_results=min(top_k, self.collection.count()),
            include=["metadatas", "documents", "distances"])
        output = []
        if results and results.get("ids") and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                output.append({
                    "id": results["ids"][0][i],
                    "doc_id": meta.get("doc_id", ""),
                    "doc_name": meta.get("doc_name", ""),
                    "text": results["documents"][0][i] if results.get("documents") else "",
                    "page": meta.get("page", ""),
                    "section": meta.get("section", ""),
                    "score": 1.0 - results["distances"][0][i] if results.get("distances") else 0.0,
                })
        return output

    def get_collection_stats(self) -> Dict[str, Any]:
        count = self.collection.count()
        all_data = self.collection.get(include=["metadatas"])
        doc_ids = set()
        if all_data and all_data.get("metadatas"):
            for m in all_data["metadatas"]:
                if m.get("doc_id"):
                    doc_ids.add(m["doc_id"])
        return {"total_chunks": count, "collection_name": self.collection_name,
                "persist_dir": self.persist_dir, "doc_ids": sorted(doc_ids),
                "num_docs": len(doc_ids)}