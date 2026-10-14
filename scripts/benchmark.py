#!/usr/bin/env python
"""
性能测试脚本（Performance Benchmark）

产出 `evaluation/performance_report.md`，覆盖三类指标：
  1. 检索性能：对评测集全量查询统计 HybridRetriever 延迟（avg / p50 / p95 / max）
  2. 模型性能：加载耗时、卸载后内存下降、峰值内存（经后端 /api/model/*）
  3. 端到端问答：SSE 首帧延迟(TTFT)、总耗时、生成吞吐（字/秒）

依赖后端已启动（`python backend/main.py`）。`--skip-model` 可只跑检索部分。

用法：
    python scripts/benchmark.py                 # 全部
    python scripts/benchmark.py --skip-model    # 仅检索（不需模型）
    python scripts/benchmark.py --e2e 5         # 端到端跑 5 条
"""
import argparse
import json
import logging
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.WARNING)

DEFAULT_QUERIES_FILE = os.path.join(os.path.dirname(__file__), "..", "evaluation", "queries.json")
DEFAULT_REPORT_FILE = os.path.join(os.path.dirname(__file__), "..", "evaluation", "performance_report.md")


def _percentiles(lat_ms: list) -> dict:
    s = sorted(lat_ms)
    def pct(p):
        if not s:
            return 0.0
        k = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
        return s[k]
    return {
        "n": len(s),
        "avg": statistics.mean(s) if s else 0.0,
        "p50": pct(50),
        "p95": pct(95),
        "max": max(s) if s else 0.0,
        "min": min(s) if s else 0.0,
    }


def _stream_sse(base_url, path, prompt, conv_id=None, timeout=300.0):
    """调用 SSE 接口，收集回答并测量首帧延迟(TTFT)与总耗时。

    兼容两种事件形态：
      - /api/agent/chat : {'status':'token','token':...}  ← 整段回答一次性推送
      - /api/chat/stream: {'token':...,'status':'generating'} ← 逐批流式（真 token 流）
    """
    import httpx
    out = {"answer": "", "ttft": None, "total": None, "error": None}
    payload = {"prompt": prompt}
    if conv_id:
        payload["conversation_id"] = conv_id
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{base_url}{path}", json=payload) as resp:
                if resp.status_code != 200:
                    out["error"] = f"HTTP {resp.status_code}"
                    return out
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    status = data.get("status")
                    if status in ("token", "generating"):
                        tok = data.get("token", "")
                        if tok:
                            if out["ttft"] is None:
                                out["ttft"] = time.perf_counter() - t0
                            out["answer"] += tok
                    elif status == "error":
                        out["error"] = data.get("message")
    except Exception as e:
        out["error"] = str(e)
    out["total"] = time.perf_counter() - t0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--queries-file", default=DEFAULT_QUERIES_FILE)
    ap.add_argument("--out", default=DEFAULT_REPORT_FILE)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--e2e", type=int, default=3, help="端到端问答采样条数")
    ap.add_argument("--skip-model", action="store_true", help="跳过模型加载/卸载与端到端测试")
    args = ap.parse_args()

    import httpx

    # 机器信息
    try:
        import psutil
        cpu = f"{psutil.cpu_count(logical=True)} 逻辑核 / {psutil.cpu_count(logical=False)} 物理核"
        ram = f"{psutil.virtual_memory().total / 1024**3:.1f} GB"
    except Exception:
        cpu, ram = "unknown", "unknown"

    # ---- 1. 检索性能 ----
    from backend.config import get_config
    from backend.rag.embedding import ONNXEmbedding
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index
    from backend.rag.retriever import HybridRetriever

    with open(args.queries_file, "r", encoding="utf-8") as f:
        queries = json.load(f)
    cfg = get_config()
    embedder = ONNXEmbedding(model_dir=cfg.get("embedding", {}).get("model_dir"))
    retriever = HybridRetriever(
        ChromaStore(persist_dir=cfg.get("chroma", {}).get("persist_directory")),
        BM25Index(persist_path=cfg.get("retrieval", {}).get("bm25_index_path")),
        embedder)
    # 预热一次（排除首次编码开销）
    retriever.retrieve("预热", top_k=args.top_k)
    lat = []
    for q in queries:
        t0 = time.perf_counter()
        retriever.retrieve(q["query"], top_k=args.top_k)
        lat.append((time.perf_counter() - t0) * 1000)
    rp = _percentiles(lat)
    print(f"[检索] n={rp['n']} avg={rp['avg']:.1f}ms p95={rp['p95']:.1f}ms max={rp['max']:.1f}ms")

    # ---- 2. 模型性能 ----
    model = {}
    e2e = []
    genm = []
    if not args.skip_model:
        try:
            httpx.get(f"{args.base_url}/api/health", timeout=5.0)
        except Exception as e:
            print(f"[WARN] 后端未就绪，跳过模型与端到端测试：{e}")
            args.skip_model = True

    if not args.skip_model:
        st = httpx.get(f"{args.base_url}/api/model/status", timeout=10).json()
        mem_loaded = st.get("memory_mb", 0)
        # 卸载
        t0 = time.perf_counter()
        httpx.post(f"{args.base_url}/api/model/unload", timeout=120)
        unload_s = time.perf_counter() - t0
        st2 = httpx.get(f"{args.base_url}/api/model/status", timeout=10).json()
        mem_unloaded = st2.get("memory_mb", 0)
        # 加载
        t0 = time.perf_counter()
        httpx.post(f"{args.base_url}/api/model/load", timeout=300)
        load_s = time.perf_counter() - t0
        st3 = httpx.get(f"{args.base_url}/api/model/status", timeout=10).json()
        mem_loaded2 = st3.get("memory_mb", 0)
        model = {"load_s": load_s, "unload_s": unload_s,
                 "mem_loaded": mem_loaded2, "mem_unloaded": mem_unloaded,
                 "mem_drop": max(0.0, mem_loaded2 - mem_unloaded)}
        print(f"[模型] load={load_s:.2f}s unload={unload_s:.2f}s "
              f"mem={mem_loaded2:.0f}MB → {mem_unloaded:.0f}MB（降 {model['mem_drop']:.0f}MB）")

        # ---- 3. 多 Agent 全链路（/api/agent/chat 不逐 token 推送，故只统计总耗时）----
        conv_id = "bench-" + time.strftime("%Y%m%d%H%M%S")
        for q in queries[:args.e2e]:
            r = _stream_sse(args.base_url, "/api/agent/chat", q["query"], conv_id)
            total = r["total"] or 0.001
            e2e.append({"query": q["query"], "chars": len(r["answer"]),
                        "total": r["total"], "cps": len(r["answer"]) / total,
                        "error": r["error"]})
            print(f"[Agent] {q['query'][:16]:16s} total={r['total']:.2f}s "
                  f"{len(r['answer'])}字 整体吞吐={e2e[-1]['cps']:.1f}字/s"
                  + (f" ERR={r['error']}" if r["error"] else ""))

        # ---- 4. 纯模型流式生成（/api/chat/stream 逐批 token，可测真 TTFT 与吞吐）----
        for q in queries[:args.e2e]:
            r = _stream_sse(args.base_url, "/api/chat/stream", q["query"])
            gen = (r["total"] - (r["ttft"] or 0)) or 0.001
            cps = len(r["answer"]) / gen if r["ttft"] is not None else 0
            genm.append({"query": q["query"], "chars": len(r["answer"]),
                         "ttft": r["ttft"], "total": r["total"], "cps": cps,
                         "error": r["error"]})
            print(f"[Gen]   {q['query'][:16]:16s} ttft={r['ttft']:.2f}s "
                  f"total={r['total']:.2f}s {len(r['answer'])}字 {cps:.1f}字/s"
                  + (f" ERR={r['error']}" if r["error"] else ""))

    # ---- 报告 ----
    L = []
    L.append("# 性能测试报告（Performance Report）\n")
    L.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"> 机器：CPU {cpu}　内存 {ram}　（纯 CPU 推理，无 GPU）\n")

    L.append("## 1. 检索性能（HybridRetriever，TopK=4）\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 样本数 | {rp['n']} |")
    L.append(f"| 平均延迟 | **{rp['avg']:.1f} ms** |")
    L.append(f"| P50 | {rp['p50']:.1f} ms |")
    L.append(f"| P95 | {rp['p95']:.1f} ms |")
    L.append(f"| 最大 | {rp['max']:.1f} ms |")
    L.append(f"| 最小 | {rp['min']:.1f} ms |")
    L.append("")

    if model:
        L.append("## 2. 模型加载 / 卸载\n")
        L.append("| 指标 | 数值 |")
        L.append("|---|---|")
        L.append(f"| 模型加载耗时 | **{model['load_s']:.2f} s** |")
        L.append(f"| 模型卸载耗时 | {model['unload_s']:.2f} s |")
        L.append(f"| 加载后内存 | {model['mem_loaded']:.0f} MB |")
        L.append(f"| 卸载后内存 | {model['mem_unloaded']:.0f} MB |")
        L.append(f"| 内存下降 | **{model['mem_drop']:.0f} MB** |")
        L.append("")
    if e2e:
        L.append("## 3. 多 Agent 全链路响应耗时（/api/agent/chat）\n")
        L.append("> 该接口在整条链路（审核 → 检索 → 生成 → 汇总）跑完后**一次性推送整段回答**，")
        L.append("> 因此不适用逐 token 的 TTFT / 生成吞吐；此处给出端到端总耗时与"
                 "整体吞吐（含检索与审核开销）。\n")
        L.append("| 查询 | 回答字数 | 总耗时 | 整体吞吐 |")
        L.append("|---|---|---|---|")
        for e in e2e:
            L.append(f"| {e['query']} | {e['chars']} | {e['total']:.2f}s | {e['cps']:.1f} 字/s |")
        L.append(f"\n平均：总耗时 {statistics.mean([e['total'] for e in e2e]):.2f}s，"
                 f"整体吞吐 {statistics.mean([e['cps'] for e in e2e]):.1f} 字/s。\n")

    if genm:
        L.append("## 4. 纯模型流式生成（/api/chat/stream）\n")
        L.append("> 该接口逐批推送 token，可测真实首帧延迟与生成吞吐。\n")
        L.append("| 查询 | 回答字数 | 首帧延迟 TTFT | 总耗时 | 生成吞吐 |")
        L.append("|---|---|---|---|---|")
        for e in genm:
            ttft = f"{e['ttft']:.2f}s" if e["ttft"] is not None else "-"
            L.append(f"| {e['query']} | {e['chars']} | {ttft} | {e['total']:.2f}s | {e['cps']:.1f} 字/s |")
        ts = [e["ttft"] for e in genm if e["ttft"] is not None]
        L.append(f"\n平均：TTFT {statistics.mean(ts):.2f}s，"
                 f"总耗时 {statistics.mean([e['total'] for e in genm]):.2f}s，"
                 f"吞吐 {statistics.mean([e['cps'] for e in genm]):.1f} 字/s。\n")

    L.append("## 5. 局限说明\n")
    L.append("- 纯 CPU 推理（Qwen2.5-1.5B Q4_K_M），吞吐受 CPU 核数限制；")
    L.append("- SQLite 单文件 + 模型单进程 + 请求锁，**不适合高并发多用户**；")
    L.append("- `/api/agent/chat` 不逐 token 推送，无法从中测得 TTFT，"
             "真实的生成 TTFT/吞吐见第 4 节（`/api/chat/stream`）；")
    L.append("- 端到端耗时随模型是否已加载（冷启动含加载时间）波动较大。")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"报告已写入: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
