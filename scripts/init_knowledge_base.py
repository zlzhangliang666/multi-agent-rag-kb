#!/usr/bin/env python
"""
初始化知识库 - 扫描 408-master/ 下的**笔记**（.md）并导入

只导入 .md 笔记；课件 PDF 由 import_ppt.py 按章节单独补齐（见 rebuild_all.py）。
这样「笔记」与「课件」两类来源互不重叠，重建结果可稳定复现：
    19 篇笔记（README + 考研经验分享 + 3 门学科章节笔记，计组第 2 章笔记为空文件被跳过）

doc_id 由 backend/rag/doc_id.py 的 note_doc_id() 统一生成（唯一权威实现，与课件共用同一套
规范化规则：路径分隔符与空白字符统一转下划线、去扩展名），保证幂等重建不产生重复。
例如：操作系统/第1章 计算机系统概述.md -> kb_操作系统_第1章_计算机系统概述
"""
import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.rag.doc_id import note_doc_id  # noqa: E402  唯一权威 doc_id 实现

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("init_kb")


def scan_files(root_dir: str) -> list:
    """扫描所有笔记 .md 文件（课件 PDF 由 import_ppt.py 单独导入，不在此扫描）"""
    files = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() == ".md":
                files.append(os.path.join(dirpath, fn))
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description="Initialize knowledge base")
    parser.add_argument("--data-dir", default=None, help="数据源目录，默认读 config.yaml 的 datasource.path")
    parser.add_argument("--persist-dir", default=None, help="Chroma 目录，默认读 config.yaml 的 chroma.persist_directory")
    parser.add_argument("--bm25-path", default=None, help="BM25 索引路径，默认读 config.yaml 的 retrieval.bm25_index_path")
    parser.add_argument("--model-dir", default=None, help="bge ONNX 模型目录，默认读 config.yaml 的 embedding.model_dir")
    args = parser.parse_args()

    from backend.config import get_config
    _cfg = get_config()
    if args.data_dir is None:
        args.data_dir = _cfg.get("datasource", {}).get("path") or os.path.join(os.path.dirname(__file__), "..", "408-master")
    if args.persist_dir is None:
        args.persist_dir = _cfg.get("chroma", {}).get("persist_directory")
    if args.bm25_path is None:
        args.bm25_path = _cfg.get("retrieval", {}).get("bm25_index_path")
    if args.model_dir is None:
        args.model_dir = _cfg.get("embedding", {}).get("model_dir")

    from backend.rag.parser import parse_document
    from backend.rag.chunker import AdaptiveChunker
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index

    logger.info("Initializing RAG components...")
    chunker = AdaptiveChunker()
    embedder = ONNXEmbedding(model_dir=args.model_dir)
    vs = ChromaStore(persist_dir=args.persist_dir)
    bm25 = BM25Index(persist_path=args.bm25_path)

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        logger.error("Data dir not found: %s", data_dir); sys.exit(1)

    files = scan_files(data_dir)
    logger.info("Found %d documents in: %s", len(files), data_dir)
    if not files:
        logger.warning("No documents found."); return

    try:
        from tqdm import tqdm
        iterator = tqdm(files, desc="Processing")
    except ImportError:
        iterator = files

    total_chunks = 0; total_elapsed = 0.0; failed = 0; skipped_scanned = 0
    for file_path in iterator:
        doc_id = note_doc_id(data_dir, file_path)
        try:
            # 幂等：先清同 doc_id 的旧块，重复执行不会产生重复文档
            vs.delete_by_doc_id(doc_id)
            bm25.remove_by_doc_id(doc_id)
            t0 = time.perf_counter()
            parsed = parse_document(file_path, doc_id)
            chunks = chunker.chunk(text=parsed.get_full_text(), doc_id=doc_id,
                                    doc_name=parsed.doc_name, pages_info=parsed.pages,
                                    sections=parsed.pages)
            if not chunks:
                logger.warning("Skipped (no chunkable text): %s", file_path)
                skipped_scanned += 1
                continue
            texts = [c["text"] for c in chunks]
            embeddings = embedder.encode(texts)
            vs.add_chunks(chunks, embeddings)
            bm25.add_chunks(chunks)
            elapsed = time.perf_counter() - t0
            total_chunks += len(chunks); total_elapsed += elapsed
            try:
                iterator.set_postfix(chunks=total_chunks, last=f"{len(chunks)}ch/{elapsed:.2f}s")
            except (AttributeError, TypeError):
                pass
        except ValueError as e:
            logger.warning("Skipped (scanned/no text): %s: %s", file_path, e); skipped_scanned += 1
        except Exception as e:
            logger.error("Failed: %s: %s", file_path, e); failed += 1

    stats = vs.get_collection_stats()
    bm25_stats = bm25.get_stats()
    processed = len(files) - failed - skipped_scanned
    print("\n" + "=" * 60)
    print("KNOWLEDGE BASE INITIALIZATION COMPLETE")
    print("=" * 60)
    print(f"  Total documents found:    {len(files)}")
    print(f"  Documents processed:      {processed}")
    print(f"  Skipped (scanned/empty):  {skipped_scanned}")
    print(f"  Failed:                   {failed}")
    print(f"  Total chunks:             {total_chunks}")
    print(f"  Total time:               {total_elapsed:.2f}s")
    print(f"  Chroma chunks:            {stats['total_chunks']}")
    print(f"  Chroma unique docs:       {stats['num_docs']}")
    print(f"  BM25 chunks:              {bm25_stats['total_chunks']}")
    print("=" * 60)


if __name__ == "__main__":
    main()