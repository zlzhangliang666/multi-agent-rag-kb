#!/usr/bin/env python
"""
把已导入知识库的 documents / chunks 同步到 SQLite

背景：知识库通过 init_knowledge_base.py 或 rebuild_vector_store.py 导入时
只写了 Chroma + BM25，没有写 documents / chunks 表，导致
GET /api/stats/dashboard 的 documents 恒为 0，与实际不符。

本脚本以 BM25 pickle 为权威数据源，幂等同步到数据库。

用法：
  python scripts/sync_kb_to_db.py
  python scripts/sync_kb_to_db.py --dry-run
"""
import argparse
import logging
import os
import pickle
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("sync_kb")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bm25-path", default=None, help="BM25 索引路径，默认读 config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    if not args.bm25_path:
        from backend.config import get_config
        args.bm25_path = get_config().get("retrieval", {}).get("bm25_index_path") \
            or os.path.join(ROOT_DIR, "data", "bm25_index.pkl")

    if not os.path.isfile(args.bm25_path):
        logger.error("BM25 索引不存在: %s", args.bm25_path)
        sys.exit(1)

    logger.info("BM25 索引: %s", args.bm25_path)

    with open(args.bm25_path, "rb") as f:
        chunks = pickle.load(f).get("chunks", [])
    logger.info("BM25 chunks: %d", len(chunks))

    # 按 doc_id 聚合
    docs = {}
    for c in chunks:
        did = c.get("doc_id") or ""
        if not did:
            continue
        d = docs.setdefault(did, {"doc_name": c.get("doc_name", ""), "chunks": []})
        d["chunks"].append(c)

    logger.info("聚合出文档数: %d", len(docs))

    if args.dry_run:
        for did, d in sorted(docs.items()):
            logger.info("  [dry-run] %s | %s | %d chunks", did[:50], d["doc_name"], len(d["chunks"]))
        return

    from backend.database import SessionLocal, crud, init_db
    from backend.database.models import Chunk, Document

    init_db()
    db = SessionLocal()
    try:
        # 清理旧的（幂等重建）
        old_docs = db.query(Document).count()
        old_chunks = db.query(Chunk).count()
        if old_docs or old_chunks:
            logger.info("清理旧记录: documents=%d chunks=%d", old_docs, old_chunks)
            db.query(Chunk).delete()
            db.query(Document).delete()
            db.commit()

        total_chunks = 0
        for did, d in sorted(docs.items()):
            name = d["doc_name"] or did
            ext = os.path.splitext(name)[1].lstrip(".").lower() or "unknown"
            crud.add_document(db, did, name, ext, len(d["chunks"]))
            crud.add_chunks(db, d["chunks"])
            total_chunks += len(d["chunks"])
            logger.info("  + %s | %d chunks", name, len(d["chunks"]))

        logger.info("同步完成: documents=%d chunks=%d", len(docs), total_chunks)

        # 校验
        logger.info("数据库校验: documents=%d chunks=%d",
                    db.query(Document).count(), db.query(Chunk).count())
    finally:
        db.close()


if __name__ == "__main__":
    main()