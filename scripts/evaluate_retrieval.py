#!/usr/bin/env python
"""
检索评测脚本 - 计算 Hit@4、MRR、平均耗时

匹配逻辑：按 doc_name + section + text 的中文关键词匹配（不依赖 doc_id 格式）
测试集：外置于 evaluation/queries.json（换语料时只改数据，不改代码）
"""
import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("eval_retrieval")

# 评测集外置在 evaluation/queries.json，便于换语料时只改数据不改代码。
# 结构: [{"subject": str, "query": str, "keywords": [str, ...]}, ...]
DEFAULT_QUERIES_FILE = os.path.join(os.path.dirname(__file__), "..", "evaluation", "queries.json")


def load_queries(path: str) -> list:
    """从 JSON 加载评测集，返回 [(subject, query, keywords), ...]"""
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    return [(it["subject"], it["query"], it["keywords"]) for it in items]



def is_hit(results, keywords):
    """判断 Top-K 结果中是否命中任一关键词"""
    for r in results:
        blob = " ".join([
            str(r.get("doc_name", "")),
            str(r.get("section", "")),
            str(r.get("text", "")),
        ]).lower()
        for kw in keywords:
            if kw.lower() in blob:
                return True, kw
    return False, None


def reciprocal_rank(results, keywords):
    for rank, r in enumerate(results, start=1):
        blob = " ".join([
            str(r.get("doc_name", "")),
            str(r.get("section", "")),
            str(r.get("text", "")),
        ]).lower()
        for kw in keywords:
            if kw.lower() in blob:
                return 1.0 / rank
    return 0.0


def main():
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality")
    # 默认 None → 从 config.yaml 读取，避免脚本内硬编码路径
    parser.add_argument("--persist-dir", default=None)
    parser.add_argument("--bm25-path", default=None)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--queries-file", default=None,
                        help="评测集 JSON 路径，默认 evaluation/queries.json")
    args = parser.parse_args()

    queries_file = args.queries_file or DEFAULT_QUERIES_FILE
    if not os.path.isfile(queries_file):
        raise FileNotFoundError(f"评测集不存在: {queries_file}")
    queries = load_queries(queries_file)

    from backend.config import get_config
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index
    from backend.rag.retriever import HybridRetriever

    # 路径优先级：命令行参数 > config.yaml
    cfg = get_config()
    model_dir = args.model_dir or cfg.get("embedding", {}).get("model_dir")
    persist_dir = args.persist_dir or cfg.get("chroma", {}).get("persist_directory")
    bm25_path = args.bm25_path or cfg.get("retrieval", {}).get("bm25_index_path") \
        or os.path.join(os.path.dirname(__file__), "..", "data", "bm25_index.pkl")

    embedder = ONNXEmbedding(model_dir=model_dir)
    vs = ChromaStore(persist_dir=persist_dir)
    bm25 = BM25Index(persist_path=bm25_path)
    retriever = HybridRetriever(vs, bm25, embedder)

    # 知识库实际覆盖情况
    import pickle
    with open(bm25_path, "rb") as f:
        bm25_data = pickle.load(f)
    all_chunks = bm25_data.get("chunks", [])
    doc_names = sorted(set(c.get("doc_name", "") for c in all_chunks))

    print("=" * 88)
    print("检索评测 (Retrieval Evaluation)")
    print("=" * 88)
    print(f"知识库总 chunks: {len(all_chunks)}")
    print(f"知识库文档数: {len(doc_names)}")
    print("文档清单:")
    for dn in doc_names:
        cnt = sum(1 for c in all_chunks if c.get("doc_name") == dn)
        print(f"  - {dn} ({cnt} chunks)")
    print()

    subj_stats = {}
    total_hits = 0
    total_rr = 0.0
    total_lat = 0.0
    idx = 0

    for subject, query, kws in queries:
        idx += 1
        subj_stats.setdefault(subject, {"hit": 0, "rr": 0.0, "total": 0})
        subj_stats[subject]["total"] += 1

        t0 = time.perf_counter()
        results = retriever.retrieve(query, top_k=args.top_k)
        lat = time.perf_counter() - t0

        hit, matched_kw = is_hit(results, kws)
        rr = reciprocal_rank(results, kws)

        if hit:
            total_hits += 1
            subj_stats[subject]["hit"] += 1
        total_rr += rr
        total_lat += lat
        subj_stats[subject]["rr"] += rr

        mark = "HIT " if hit else "MISS"
        print(f"[{idx:2d}] {mark} [{subject}] {query}  (RR={rr:.3f}, {lat*1000:.0f}ms)")
        for rank, r in enumerate(results[:args.top_k], 1):
            snip = str(r.get("text", ""))[:55].replace("\n", " ")
            print(f"       {rank}. doc={r.get('doc_name','?')[:28]:28s} "
                  f"sec={str(r.get('section',''))[:20]:20s} "
                  f"score={r.get('score',0):.4f} | {snip}")
        print()

    hit_rate = total_hits / len(queries) * 100
    mrr = total_rr / len(queries)
    avg_lat = total_lat / len(queries) * 1000

    print("=" * 88)
    print("汇总")
    print("=" * 88)
    print(f"总 Hit@{args.top_k}: {total_hits}/{len(queries)} = {hit_rate:.1f}%")
    print(f"总 MRR:    {mrr:.4f}")
    print(f"平均耗时:  {avg_lat:.1f} ms")
    print()
    print("分学科 Hit@4:")
    for s, st in subj_stats.items():
        rate = st["hit"] / st["total"] * 100 if st["total"] else 0
        m = st["rr"] / st["total"] if st["total"] else 0
        print(f"  {s}: {st['hit']}/{st['total']} = {rate:.1f}%  (MRR={m:.4f})")
    print("=" * 88)


if __name__ == "__main__":
    main()