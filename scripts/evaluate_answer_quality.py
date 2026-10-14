#!/usr/bin/env python
"""
答案质量评测脚本（Answer Quality Evaluation）

在「检索评测」（evaluate_retrieval.py：Hit@4 / MRR）之外，补充**答案侧**质量指标：

  1. 引用正确率 Citation Precision : Top-k 引用来源中真正相关（命中查询关键词）的比例
  2. 引用覆盖率 Citation Coverage  : 至少有一条相关性引用的查询占比（引用不"跑题"的比例）
  3. 答案忠实度 Faithfulness       : 回答句子中被检索上下文"词面支撑"的比例（轻量启发式）
  4. 答案覆盖度 Answer Coverage    : 回答命中查询要点词（answer_keywords）的比例
  5. 耗时                        : 首帧延迟 / 总耗时

实现方式：通过后端 SSE 接口 `POST /api/agent/chat` 跑**真实端到端链路**
（输入审核 → 检索 → 生成 → 输出审核），因此需要后端已启动；
检索上下文文本由脚本本地用同一套 RAG 组件复算（与后端一致），用于计算忠实度。

说明（诚实记录）：
  - 忠实度用「词面证据覆盖」作为 LLM 判定的**轻量代理**，不做严格语义蕴含（NLI）；
    数值应作为**相对参考**，不能等同于学术级 NLI 忠实度。
  - 引用正确率按「引用来源的 doc_name/section 是否含查询关键词」判定，
    依赖关键词质量（评测集 keywords 字段）。

用法：
    python scripts/evaluate_answer_quality.py            # 全量评测（约 6~10 分钟，需后端在跑）
    python scripts/evaluate_answer_quality.py --limit 5  # 先跑 5 条做冒烟验证
"""
import argparse
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.WARNING)
logging.getLogger("jieba").setLevel(logging.WARNING)   # 静音 jieba 的 DEBUG 噪声
logger = logging.getLogger("eval_answer_quality")

DEFAULT_QUERIES_FILE = os.path.join(os.path.dirname(__file__), "..", "evaluation", "queries.json")
DEFAULT_REPORT_FILE = os.path.join(os.path.dirname(__file__), "..", "evaluation", "answer_quality_report.md")

# 句级判定：被上下文支撑的词面覆盖率阈值
SUPPORT_THRESHOLD = 0.6

_STOP = set(
    "的 了 和 是 在 有 也 就 都 而 及 与 着 或 一个 我们 你们 他们 它们 这 那 "
    "对于 以及 等等 可以 因为 所以 但是 如果 那么 这个 那个 这些 那些 因此 从而 "
    "进行 需要 通过 使用 采用 主要 一般 通常 表示 称为 属于 一种 每个 各个 如下 上述 "
    "the a an of to in on for and or is are be by with as at".split()
)


def load_queries(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    out = []
    for i, it in enumerate(items, 1):
        out.append({
            "id": it.get("id") or f"q{i}",
            "subject": it.get("subject", ""),
            "query": it["query"],
            "keywords": it.get("keywords", []),
            "answer_keywords": it.get("answer_keywords", []),
        })
    return out


def _content_tokens(text: str) -> list:
    """提取内容词：长度>=2 且含中英文字母、非停用词"""
    import jieba
    jieba.setLogLevel(logging.WARNING)     # 静音 jieba 的 DEBUG 噪声
    toks = [t.strip() for t in jieba.cut(text)]
    return [t for t in toks
            if len(t) >= 2
            and re.search(r"[\u4e00-\u9fffA-Za-z]", t)
            and t.lower() not in _STOP]


def _split_sentences(text: str) -> list:
    parts = re.split(r"[。！？!?；;\n]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def compute_faithfulness(answer: str, context: str) -> dict:
    """词面证据覆盖启发式：回答句被检索上下文支撑的比例"""
    if not answer.strip():
        return {"faithfulness": 0.0, "avg_coverage": 0.0, "sentences": 0, "unsupported": []}
    ctx_tokens = set(_content_tokens(context))
    if not ctx_tokens:
        return {"faithfulness": 0.0, "avg_coverage": 0.0, "sentences": 0, "unsupported": []}

    judged = 0
    supported = 0
    cov_sum = 0.0
    unsupported = []
    for sent in _split_sentences(answer):
        toks = set(_content_tokens(sent))
        if len(toks) < 3:          # 太短，无法判定，跳过
            continue
        judged += 1
        cov = len(toks & ctx_tokens) / len(toks)
        cov_sum += cov
        if cov >= SUPPORT_THRESHOLD:
            supported += 1
        else:
            unsupported.append({"sentence": sent[:80], "coverage": round(cov, 3)})
    if judged == 0:
        return {"faithfulness": 0.0, "avg_coverage": 0.0, "sentences": 0, "unsupported": []}
    return {
        "faithfulness": supported / judged,
        "avg_coverage": cov_sum / judged,
        "sentences": judged,
        "unsupported": unsupported[:5],
    }


def citation_metrics(sources: list, keywords: list) -> dict:
    """引用正确率：引用来源的 doc_name/section 是否含查询关键词"""
    if not sources:
        return {"precision": 0.0, "relevant": 0, "total": 0, "covered": False}
    kws = [k.lower() for k in keywords if k]
    relevant = 0
    for s in sources:
        blob = f"{s.get('doc_name', '')} {s.get('section', '')}".lower()
        if any(k in blob for k in kws):
            relevant += 1
    return {
        "precision": relevant / len(sources),
        "relevant": relevant,
        "total": len(sources),
        "covered": relevant > 0,
    }


def answer_coverage(answer: str, answer_keywords: list) -> float:
    """答案覆盖度：命中 answer_keywords 的比例（无要点词则返回 None 表示不适用）"""
    if not answer_keywords:
        return None
    a = answer.lower()
    hit = sum(1 for k in answer_keywords if k and k.lower() in a)
    return hit / len(answer_keywords)


def stream_chat(base_url: str, prompt: str, conversation_id: str, timeout: float = 300.0) -> dict:
    """调用后端 SSE 接口，返回 answer / sources / logs / 耗时"""
    import httpx
    result = {"answer": "", "sources": [], "logs": [], "done": None,
              "ttft": None, "total": None, "error": None}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{base_url}/api/agent/chat",
                               json={"prompt": prompt, "conversation_id": conversation_id}) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "ignore")[:200]
                    result["error"] = f"HTTP {resp.status_code}: {body}"
                    return result
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    status = data.get("status")
                    if status == "token":
                        if result["ttft"] is None:
                            result["ttft"] = time.perf_counter() - t0
                        result["answer"] += data.get("token", "")
                    elif status == "sources":
                        result["sources"] = data.get("sources", [])
                    elif status == "agent_log":
                        result["logs"].append(data.get("log", {}))
                    elif status == "done":
                        result["done"] = data
                    elif status in ("error", "info"):
                        result["error"] = data.get("message", status)
    except Exception as e:
        result["error"] = str(e)
    result["total"] = time.perf_counter() - t0
    return result


def main():
    ap = argparse.ArgumentParser(description="Answer quality evaluation")
    ap.add_argument("--queries-file", default=None)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 evaluation/answer_quality_report.md）")
    ap.add_argument("--limit", type=int, default=0, help="仅评测前 N 条（0=全量）")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--persist-dir", default=None)
    ap.add_argument("--bm25-path", default=None)
    ap.add_argument("--model-dir", default=None)
    args = ap.parse_args()

    queries_file = args.queries_file or DEFAULT_QUERIES_FILE
    report_file = args.out or DEFAULT_REPORT_FILE
    queries = load_queries(queries_file)
    if args.limit and args.limit > 0:
        queries = queries[:args.limit]

    # 预检：后端是否在跑
    import httpx
    try:
        h = httpx.get(f"{args.base_url}/api/health", timeout=5.0)
        if h.status_code != 200:
            raise RuntimeError(f"health {h.status_code}")
    except Exception as e:
        print(f"[ERROR] 后端未就绪（{args.base_url}）：{e}\n请先启动 backend/main.py")
        sys.exit(2)

    # 本地复算检索上下文（与后端同一套组件，用于忠实度）
    from backend.config import get_config
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index
    from backend.rag.retriever import HybridRetriever

    cfg = get_config()
    model_dir = args.model_dir or cfg.get("embedding", {}).get("model_dir")
    persist_dir = args.persist_dir or cfg.get("chroma", {}).get("persist_directory")
    bm25_path = args.bm25_path or cfg.get("retrieval", {}).get("bm25_index_path")
    embedder = ONNXEmbedding(model_dir=model_dir)
    retriever = HybridRetriever(ChromaStore(persist_dir=persist_dir),
                                BM25Index(persist_path=bm25_path), embedder)

    conv_id = "eval-quality-" + time.strftime("%Y%m%d%H%M%S")
    rows = []
    print("=" * 88)
    print(f"答案质量评测  |  后端={args.base_url}  |  查询数={len(queries)}  |  会话={conv_id}")
    print("=" * 88)

    for i, q in enumerate(queries, 1):
        chunks = retriever.retrieve(q["query"], top_k=args.top_k)
        context = "\n".join(c.get("text", "") for c in chunks)
        res = stream_chat(args.base_url, q["query"], conv_id)

        faith = compute_faithfulness(res["answer"], context)
        cite = citation_metrics(res["sources"], q["keywords"])
        cov = answer_coverage(res["answer"], q["answer_keywords"])

        rows.append({
            "id": q["id"], "subject": q["subject"], "query": q["query"],
            "answer_len": len(res["answer"]),
            "citation_precision": cite["precision"],
            "citation_covered": cite["covered"],
            "faithfulness": faith["faithfulness"],
            "avg_coverage": faith["avg_coverage"],
            "answer_coverage": cov,
            "ttft": res["ttft"], "total": res["total"],
            "error": res["error"],
            "unsupported": faith["unsupported"],
        })

        cov_s = f"{cov*100:.0f}%" if cov is not None else "n/a"
        print(f"[{i:2d}] {q['id']:7s} cPrec={cite['precision']*100:3.0f}% "
              f"(覆盖={cite['relevant']}/{cite['total']}) "
              f"faith={faith['faithfulness']*100:3.0f}% ansCov={cov_s:>4s} "
              f"len={len(res['answer']):4d} {res['total']:.1f}s"
              + (f"  ERR={res['error']}" if res["error"] else ""))

    n = len(rows)
    def avg(key, only=None):
        vals = [r[key] for r in rows if r[key] is not None and (only is None or only(r))]
        return sum(vals) / len(vals) if vals else 0.0

    cite_prec = avg("citation_precision")
    cite_cov = sum(1 for r in rows if r["citation_covered"]) / n * 100
    faith_avg = avg("faithfulness")
    cov_vals = [r["answer_coverage"] for r in rows if r["answer_coverage"] is not None]
    ans_cov = sum(cov_vals) / len(cov_vals) * 100 if cov_vals else None
    ttft_avg = avg("ttft")
    total_avg = avg("total")
    err_cnt = sum(1 for r in rows if r["error"])

    print("-" * 88)
    print(f"引用正确率 Citation Precision : {cite_prec*100:.1f}%")
    print(f"引用覆盖率 Citation Coverage  : {cite_cov:.1f}%  （有相关引用的查询占比）")
    print(f"答案忠实度 Faithfulness       : {faith_avg*100:.1f}%  （词面证据覆盖启发式）")
    if ans_cov is not None:
        print(f"答案覆盖度 Answer Coverage     : {ans_cov:.1f}%")
    print(f"首帧延迟 TTFT / 总耗时         : {ttft_avg:.1f}s / {total_avg:.1f}s")
    print(f"失败查询                       : {err_cnt}/{n}")
    print("=" * 88)

    # 写报告
    lines = []
    lines.append("# 答案质量评测报告（Answer Quality Report）\n")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 后端：{args.base_url}　查询数：{n}　TopK：{args.top_k}\n")
    lines.append("## 汇总指标\n")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 引用正确率 Citation Precision | **{cite_prec*100:.1f}%** | Top-k 引用中相关（命中查询关键词）的比例 |")
    lines.append(f"| 引用覆盖率 Citation Coverage | **{cite_cov:.1f}%** | 有相关引用的查询占比 |")
    lines.append(f"| 答案忠实度 Faithfulness | **{faith_avg*100:.1f}%** | 回答句被检索上下文词面支撑的比例（轻量代理） |")
    if ans_cov is not None:
        lines.append(f"| 答案覆盖度 Answer Coverage | **{ans_cov:.1f}%** | 回答命中 answer_keywords 的比例 |")
    lines.append(f"| 平均首帧延迟 TTFT | {ttft_avg:.2f} s | SSE 首帧到达时间 |")
    lines.append(f"| 平均总耗时 | {total_avg:.2f} s | 端到端（1.5B CPU 推理） |")
    lines.append(f"| 失败查询 | {err_cnt}/{n} | HTTP/生成错误 |")
    lines.append("")
    lines.append("> 忠实度为**词面证据覆盖启发式**，非严格语义蕴含（NLI），仅作相对参考。\n")
    lines.append("## 逐条结果\n")
    lines.append("| # | id | 学科 | 查询 | 引正率 | 忠实度 | 答案覆盖 | 总耗时 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        cov_s = f"{r['answer_coverage']*100:.0f}%" if r["answer_coverage"] is not None else "-"
        lines.append(f"| {i} | {r['id']} | {r['subject']} | {r['query']} | "
                     f"{r['citation_precision']*100:.0f}% | {r['faithfulness']*100:.0f}% | "
                     f"{cov_s} | {r['total']:.1f}s |")
    lines.append("")
    # 低分项关注
    low = [r for r in rows if r["faithfulness"] < SUPPORT_THRESHOLD]
    if low:
        lines.append("## 忠实度偏低（<60%）的查询\n")
        for r in low:
            lines.append(f"- **{r['query']}**（忠实度 {r['faithfulness']*100:.0f}%）：")
            for u in r["unsupported"]:
                lines.append(f"  - 覆盖 {u['coverage']*100:.0f}%：{u['sentence']}")
        lines.append("")

    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告已写入: {os.path.abspath(report_file)}")


if __name__ == "__main__":
    main()
