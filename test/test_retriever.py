"""RRF 混合检索测试：用假向量库 / BM25 / Embedding 验证融合与排序。"""
from backend.rag.retriever.retriever import HybridRetriever


def _chunk(cid, text, doc="d", name="n", page=1, section="s"):
    return {"id": cid, "chunk_id": cid, "text": text, "doc_id": doc,
            "doc_name": name, "page": page, "section": section}


class FakeVectorStore:
    def __init__(self, items):
        self._items = items

    def search(self, query_emb, top_k=10):
        return self._items[:top_k]


class FakeBM25:
    def __init__(self, items):
        self._items = items

    def search(self, query, top_k=10):
        return self._items[:top_k]


class FakeEmbedding:
    def encode(self, texts):
        return [[0.0] * 8 for _ in texts]


def test_rrf_prefers_chunk_hit_by_both_retrievers():
    vec = [_chunk("c1", "命中两路"), _chunk("c2", "仅向量")]
    bm25 = [_chunk("c1", "命中两路"), _chunk("c3", "仅BM25")]
    r = HybridRetriever(FakeVectorStore(vec), FakeBM25(bm25), FakeEmbedding())
    res = r.retrieve("查询", top_k=3)
    assert res[0]["chunk_id"] == "c1"       # 两路都命中，RRF 分最高
    assert len(res) == 3                    # c1/c2/c3 去重后三个


def test_rrf_respects_top_k():
    vec = [_chunk(f"v{i}", "x") for i in range(10)]
    bm25 = [_chunk(f"b{i}", "y") for i in range(10)]
    r = HybridRetriever(FakeVectorStore(vec), FakeBM25(bm25), FakeEmbedding())
    res = r.retrieve("查询", top_k=4)
    assert len(res) == 4


def test_rrf_scores_are_positive_and_sorted():
    vec = [_chunk("c1", "x"), _chunk("c2", "y")]
    bm25 = [_chunk("c2", "y")]
    r = HybridRetriever(FakeVectorStore(vec), FakeBM25(bm25), FakeEmbedding())
    res = r.retrieve("查询", top_k=2)
    scores = [x["score"] for x in res]
    assert all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)
