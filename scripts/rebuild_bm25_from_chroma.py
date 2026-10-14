#!/usr/bin/env python
"""
从 Chroma 重建 BM25 索引（幂等）

背景：BM25 以 pickle 持久化，后端进程启动时载入内存。
若在导入/重建知识库之后没有重启后端，进程内存里仍是旧索引；
此后任何 upload / delete 都会用旧内存索引覆盖磁盘上的新索引，
造成 BM25 与 Chroma 不一致（BM25 缺内容，检索质量下降）。

本脚本以 Chroma 为权威数据源，重建 BM25 并落盘，消除该不一致。

用法：
  python scripts/rebuild_bm25_from_chroma.py
  python scripts/rebuild_bm25_from_chroma.py --dry-run
"""
import argparse
import logging
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("rebuild_bm25")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persist-dir", default=None, help="Chroma 目录，默认读 config.yaml")
    ap.add_argument("--bm25-path", default=None, help="BM25 输出路径，默认读 config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from backend.config import get_config
    cfg = get_config()
    persist_dir = args.persist_dir or cfg.get("chroma", {}).get("persist_directory")
    bm25_path = args.bm25_path or cfg.get("retrieval", {}).get("bm25_index_path")
    if not bm25_path:
        logger.error("未找到 bm25_index_path 配置")
        sys.exit(1)

    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index

    vs = ChromaStore(persist_dir=persist_dir)
    total = vs.collection.count()
    logger.info("Chroma chunks: %d", total)

    data = vs.collection.get(include=["documents", "metadatas"])
    ids = data.get("ids") or []
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []

    chunks = []
    seen = set()
    for cid, text, meta in zip(ids, docs, metas):
        if not cid or cid in seen:
            continue
        seen.add(cid)
        meta = meta or {}
        chunks.append({
            "chunk_id": cid,
            "doc_id": meta.get("doc_id", ""),
            "doc_name": meta.get("doc_name", ""),
            "text": text or "",
            "page": meta.get("page", ""),
            "section": meta.get("section", ""),
            "char_start": meta.get("char_start", ""),
            "char_end": meta.get("char_end", ""),
        })

    doc_ids = sorted(set(c["doc_id"] for c in chunks if c["doc_id"]))
    logger.info("重建 chunks: %d（去重后），文档数: %d", len(chunks), len(doc_ids))

    if args.dry_run:
        for did in doc_ids:
            n = sum(1 for c in chunks if c["doc_id"] == did)
            logger.info("  [dry-run] %s | %d chunks", did[:50], n)
        return

    bm25 = BM25Index(persist_path=bm25_path)
    bm25.build(chunks)
    logger.info("BM25 重建完成: %d chunks -> %s", len(bm25._chunks), bm25_path)

    # 校验
    check = BM25Index(persist_path=bm25_path)
    logger.info("校验: BM25=%d / Chroma=%d / 一致=%s",
                len(check._chunks), total, len(check._chunks) == total)


if __name__ == "__main__":
    main()