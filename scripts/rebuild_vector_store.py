#!/usr/bin/env python
"""
重建 Chroma 向量库（从 BM25 pickle + embedding 缓存）

用途：
  1. Chroma HNSW 索引损坏/缺失时快速重建，无需重新解析文档
  2. 工作区路径变更后重建向量库
  3. embedding 缓存命中时跳过编码（省约 3 分钟）

用法：
  python scripts/rebuild_vector_store.py            # 重建并验证
  python scripts/rebuild_vector_store.py --no-cache # 强制重新编码

数据来源优先级：
  data/bm25_index.pkl（权威 chunk 列表）
  data/embeddings_cache.npz（embedding 缓存，按 chunk_id 顺序对齐）
"""
import argparse
import logging
import os
import pickle
import shutil
import sys

import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("rebuild_vs")

from backend.config import get_config

_CFG = get_config()
CHROMA_DIR = _CFG.get("chroma", {}).get("persist_directory") or os.path.join(ROOT_DIR, "data", "chroma_db")
BM25_PATH = _CFG.get("retrieval", {}).get("bm25_index_path") or os.path.join(ROOT_DIR, "data", "bm25_index.pkl")
EMB_CACHE = _CFG.get("embedding", {}).get("cache_path") or os.path.join(ROOT_DIR, "data", "embeddings_cache.npz")


def main():
    ap = argparse.ArgumentParser(description="Rebuild Chroma vector store from BM25 + embedding cache")
    ap.add_argument("--no-cache", action="store_true", help="忽略 embedding 缓存，强制重新编码")
    ap.add_argument("--model-dir", default=None, help="bge ONNX 模型目录（默认读 config.yaml）")
    args = ap.parse_args()

    from backend.config import get_config
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore

    cfg = get_config()
    model_dir = args.model_dir or cfg.get("embedding", {}).get("model_dir")

    # ---- 1. 读取 BM25 chunks（权威数据源）----
    if not os.path.isfile(BM25_PATH):
        logger.error("BM25 索引不存在: %s", BM25_PATH)
        logger.error("请先运行 scripts/init_knowledge_base.py 完成文档解析与分块")
        sys.exit(1)

    with open(BM25_PATH, "rb") as f:
        bm25_data = pickle.load(f)
    chunks = bm25_data.get("chunks", [])
    if not chunks:
        logger.error("BM25 索引为空，无可重建数据")
        sys.exit(1)

    ids = [c["chunk_id"] for c in chunks]
    texts = [c["text"] for c in chunks]
    logger.info("BM25 chunks: %d", len(chunks))

    # ---- 2. embedding（优先用缓存）----
    embs = None
    if not args.no_cache and os.path.isfile(EMB_CACHE):
        try:
            z = np.load(EMB_CACHE, allow_pickle=True)
            cached_ids = [str(x) for x in z["ids"]]
            if cached_ids == ids:
                embs = z["embeddings"]
                logger.info("命中 embedding 缓存: %s", str(embs.shape))
            else:
                logger.warning("缓存 chunk_id 不匹配，需重新编码")
        except Exception as e:
            logger.warning("缓存读取失败(%s)，需重新编码", e)

    embedder = ONNXEmbedding(model_dir=model_dir)

    if embs is None:
        logger.info("开始编码 %d 条文本...", len(texts))
        embs = embedder.encode(texts, show_progress=True)
        try:
            os.makedirs(os.path.dirname(EMB_CACHE), exist_ok=True)
            np.savez_compressed(EMB_CACHE, ids=np.array(ids, dtype=object), embeddings=embs)
            logger.info("已保存 embedding 缓存: %s", EMB_CACHE)
        except Exception as e:
            logger.warning("缓存保存失败（不影响重建）: %s", e)

    if embs.shape[0] != len(chunks):
        logger.error("embedding 数量(%d)与 chunk 数量(%d)不一致", embs.shape[0], len(chunks))
        sys.exit(1)

    # ---- 3. 重建 Chroma ----
    if os.path.exists(CHROMA_DIR):
        shutil.rmtree(CHROMA_DIR)
        logger.info("已删除旧 Chroma 目录")

    vs = ChromaStore(persist_dir=CHROMA_DIR)
    vs.add_chunks(chunks, embs)
    logger.info("Chroma 写入完成")

    # ---- 4. 验证 ----
    cnt = vs.collection.count()
    logger.info("Chroma count = %d / BM25 count = %d / 一致 = %s",
                cnt, len(chunks), cnt == len(chunks))

    # 检查 HNSW 二进制文件是否真正落盘
    hnsw_files = []
    for root, _dirs, files in os.walk(CHROMA_DIR):
        for fn in files:
            if fn.endswith(".bin"):
                hnsw_files.append((os.path.relpath(os.path.join(root, fn), CHROMA_DIR),
                                   os.path.getsize(os.path.join(root, fn))))
    if hnsw_files:
        logger.info("HNSW 索引文件已落盘:")
        for name, size in sorted(hnsw_files):
            logger.info("  %s (%d bytes)", name, size)
    else:
        logger.error("未找到任何 .bin 文件 —— HNSW 索引未持久化！")
        logger.error("跨进程读取将会失败。请检查运行环境对 .bin 文件的写入权限。")
        sys.exit(2)

    q = embedder.encode(["死锁产生的四个条件"])
    res = vs.search(q, top_k=4)
    logger.info("检索自检 '死锁产生的四个条件' → %d 条", len(res))
    for r in res:
        logger.info("  doc=%s | sec=%s | score=%.4f",
                    r["doc_name"], str(r["section"])[:28], r["score"])

    if cnt != len(chunks):
        sys.exit(3)
    logger.info("重建成功")


if __name__ == "__main__":
    main()