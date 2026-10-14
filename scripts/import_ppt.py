#!/usr/bin/env python
"""
导入指定学科/章节的课件 PDF 到知识库（Chroma + BM25）

背景：`init_knowledge_base.py` 默认排除所有 ppt/ 目录，因为多数课件的正文
已由笔记 .md 覆盖。但个别学科存在缺口（例如计组第 2 章笔记为空文件、
数据结构章节笔记是手写扫描件），需要从 ppt/ 课件补齐。

本脚本按目录导入课件 PDF，doc_id 由 backend/rag/doc_id.py 的 ppt_doc_id() 统一生成
（唯一权威实现，与笔记共用同一套规范化规则：路径分隔符与空白字符统一转下划线、去扩展名）：
    kb_<学科>_ppt_<章节>_<文件名>
幂等：同一 doc_id 先删除旧块再重新写入，可反复执行。

用法：
  python scripts/import_ppt.py ^
      --dir "408-master/计算机组成原理/ppt/第2章 数据的表示和运算" ^
      --subject "计算机组成原理" ^
      --chapter "第2章 数据的表示和运算"

  python scripts/import_ppt.py --dir ... --subject ... --chapter ... --dry-run
"""
import argparse
import glob
import logging
import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)
from backend.rag.doc_id import ppt_doc_id  # noqa: E402  唯一权威 doc_id 实现

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("import_ppt")


def main():
    ap = argparse.ArgumentParser(description="Import PPT/PDF courseware into the knowledge base")
    ap.add_argument("--dir", required=True, help="课件目录（相对项目根目录或绝对路径）")
    ap.add_argument("--subject", required=True, help="学科名，用于 doc_id 前缀")
    ap.add_argument("--chapter", required=True, help="章节名，用于 doc_id 前缀")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要导入的文件，不写库")
    args = ap.parse_args()

    src_dir = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT_DIR, args.dir)
    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        logger.error("课件目录不存在: %s", src_dir)
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(src_dir, "*.pdf")))
    if not files:
        logger.error("目录下没有 .pdf 文件: %s", src_dir)
        sys.exit(1)

    logger.info("课件目录: %s", src_dir)
    logger.info("待导入文件: %d", len(files))
    logger.info("doc_id 规则: kb_%s_ppt_<章节>_<文件名>（空格统一转下划线）", args.subject)

    if args.dry_run:
        for f in files:
            logger.info("  [dry-run] %s", ppt_doc_id(args.subject, args.chapter, f))
        return

    from backend.config import get_config
    from backend.rag.parser import parse_document
    from backend.rag.chunker import AdaptiveChunker
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index

    cfg = get_config()
    persist_dir = cfg.get("chroma", {}).get("persist_directory")
    bm25_path = cfg.get("retrieval", {}).get("bm25_index_path")
    if not persist_dir or not bm25_path:
        logger.error("config.yaml 缺少 chroma.persist_directory 或 retrieval.bm25_index_path")
        sys.exit(1)

    model_dir = cfg.get("embedding", {}).get("model_dir") or os.path.join(ROOT_DIR, "models", "bge-small-zh-v1.5-onnx")
    if not os.path.isabs(model_dir):
        model_dir = os.path.join(ROOT_DIR, model_dir)

    logger.info("加载 RAG 组件...")
    embedder = ONNXEmbedding(model_dir=model_dir)
    vs = ChromaStore(persist_dir=persist_dir)
    bm25 = BM25Index(persist_path=bm25_path)
    chunker = AdaptiveChunker()

    before_chroma = vs.collection.count()
    before_bm25 = len(bm25._chunks)
    logger.info("导入前: Chroma=%d / BM25=%d", before_chroma, before_bm25)

    added_chunks = 0
    ok, skipped, failed = 0, 0, 0
    t0 = time.perf_counter()

    for f in files:
        doc_id = ppt_doc_id(args.subject, args.chapter, f)
        doc_name = os.path.basename(f)
        try:
            # 幂等：先清同 doc_id 的旧块
            vs.delete_by_doc_id(doc_id)
            bm25.remove_by_doc_id(doc_id)

            parsed = parse_document(f, doc_id)
            chunks = chunker.chunk(text=parsed.get_full_text(), doc_id=doc_id,
                                   doc_name=parsed.doc_name, pages_info=parsed.pages,
                                   sections=parsed.pages)
            if not chunks:
                logger.warning("跳过（无可提取文本）: %s", doc_name)
                skipped += 1
                continue
            embeddings = embedder.encode([c["text"] for c in chunks])
            vs.add_chunks(chunks, embeddings)
            bm25.add_chunks(chunks)
            added_chunks += len(chunks)
            ok += 1
            logger.info("  + %s | %d chunks", doc_name, len(chunks))
        except ValueError as e:
            logger.warning("跳过（扫描件/空文本）: %s: %s", doc_name, e)
            skipped += 1
        except Exception as e:
            logger.error("失败: %s: %s", doc_name, e)
            failed += 1

    elapsed = time.perf_counter() - t0
    after_chroma = vs.collection.count()
    after_bm25 = len(bm25._chunks)

    print("\n" + "=" * 60)
    print("PPT IMPORT COMPLETE")
    print("=" * 60)
    print(f"  目录:            {src_dir}")
    print(f"  文件总数:        {len(files)}")
    print(f"  成功:            {ok}")
    print(f"  跳过(空文本):    {skipped}")
    print(f"  失败:            {failed}")
    print(f"  新增 chunks:     {added_chunks}")
    print(f"  耗时:            {elapsed:.2f}s")
    print(f"  Chroma:          {before_chroma} -> {after_chroma}")
    print(f"  BM25:            {before_bm25} -> {after_bm25}")
    print(f"  两者一致:        {after_chroma == after_bm25}")
    print("=" * 60)
    print("提示: 导入后必须重启后端，否则后端内存里仍是旧 BM25 索引。")
    print("      然后执行 python scripts/sync_kb_to_db.py 同步 SQLite。")


if __name__ == "__main__":
    main()