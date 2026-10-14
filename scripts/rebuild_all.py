#!/usr/bin/env python
"""
一键全量重建知识库（从数据源目录重生成全部派生数据）

适用场景：
  - 换机器 / 拷贝项目后，data/ 为空或缺失
  - 临时目录被清理导致运行时数据丢失
  - 想从源码得到一份干净、可复现的知识库

会重建的内容（全部落在 config 的运行时数据根 ${KB_DATA_DIR:-data}/ 下）：
  1. Chroma 向量库      chroma_db/
  2. BM25 索引          bm25_index.pkl
  3. SQLite 文档表      kb_qa.db 的 documents / chunks 表（不清空聊天历史）
  4. （可选）检索评测   打印 Hit@4 / MRR

数据来源（必须存在于项目内）：
  数据源目录   由 config.yaml 的 datasource.path 指定（默认内置 408 考研资料作为演示语料）
  models/       Qwen GGUF + bge-small-zh-v1.5 ONNX

用法：
  python scripts/rebuild_all.py            # 预览计划，不写库
  python scripts/rebuild_all.py --yes      # 真正执行重建（会先把旧派生数据改名为 .bak_<时间戳>）
  python scripts/rebuild_all.py --yes --skip-eval

注意：执行前请先停掉后端，否则 Chroma / SQLite 文件可能被占用。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# 课件补齐清单从 config.yaml 的 rebuild.courseware 读取（不再硬编码，
# 便于换成其他语料：只需改配置，不改代码）。


def _run(script: str, extra_args: list) -> None:
    """以子进程方式运行 scripts/ 下的脚本，失败即中止"""
    cmd = [sys.executable, os.path.join(ROOT_DIR, "scripts", script)] + extra_args
    print("\n$ " + " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ret = subprocess.run(cmd, cwd=ROOT_DIR, env=env)
    if ret.returncode != 0:
        print(f"\n[错误] {script} 退出码 {ret.returncode}，重建中止。")
        sys.exit(ret.returncode)


def _backup(path: str) -> None:
    """把既有派生数据改名为 .bak_<时间戳>（非破坏性）"""
    if not os.path.exists(path):
        return
    ts = int(time.time())
    bak = f"{path}.bak_{ts}"
    os.rename(path, bak)
    print(f"  [备份] {os.path.relpath(path, ROOT_DIR)} -> {os.path.basename(bak)}")


def main():
    ap = argparse.ArgumentParser(description="Rebuild the whole knowledge base from the configured datasource")
    ap.add_argument("--yes", action="store_true", help="确认执行（否则仅预览计划）")
    ap.add_argument("--skip-eval", action="store_true", help="跳过最后的检索评测")
    args = ap.parse_args()

    from backend.config import get_config
    cfg = get_config()

    data_root = os.path.dirname(cfg["chroma"]["persist_directory"])
    chroma_dir = cfg["chroma"]["persist_directory"]
    bm25_path = cfg["retrieval"]["bm25_index_path"]
    emb_cache = cfg.get("embedding", {}).get("cache_path") or os.path.join(data_root, "embeddings_cache.npz")
    db_url = cfg["database"]["url"]
    src_dir = cfg["datasource"]["path"]

    print("=" * 68)
    print(" 一键全量重建知识库")
    print("=" * 68)
    print(f"  运行时数据根 : {data_root}")
    print(f"  数据源       : {src_dir}")
    print(f"  Chroma       : {chroma_dir}")
    print(f"  BM25         : {bm25_path}")
    print(f"  SQLite       : {db_url}")

    if not os.path.isdir(src_dir):
        print(f"\n[错误] 数据源目录不存在: {src_dir}")
        sys.exit(1)

    # 展开课件清单（来自 config.yaml 的 rebuild.courseware）
    courseware = cfg.get("rebuild", {}).get("courseware", []) or []
    ppt_dirs = []
    missing = []
    for item in courseware:
        subject = item.get("subject", "")
        pattern = item.get("path", "")
        matches = [d for d in sorted(glob.glob(os.path.join(src_dir, pattern))) if os.path.isdir(d)]
        if not matches:
            missing.append((subject, pattern))
            continue
        for d in matches:
            ppt_dirs.append((subject, d, os.path.basename(d)))
    print(f"  课件章节     : {len(ppt_dirs)} 个目录（配置 rebuild.courseware 共 {len(courseware)} 条）")
    for subject, d, chapter in ppt_dirs:
        print(f"      - [{subject}] {chapter}")
    if missing:
        print("\n  [警告] 以下课件清单未匹配到任何目录（请检查 config.rebuild.courseware 与语料结构）：")
        for subject, pattern in missing:
            print(f"      ! [{subject}] {pattern}")

    if not args.yes:
        print("\n以上为预览。确认执行请加 --yes（会先把旧派生数据改名为 .bak_<时间戳>）。")
        return

    # ---- 1. 备份旧的派生数据（保留 SQLite，避免丢失聊天历史）----
    print("\n[1/5] 备份旧派生数据 ...")
    for p in (chroma_dir, bm25_path, emb_cache):
        _backup(p)

    # ---- 2. 扫描笔记，构建 Chroma + BM25 ----
    print("\n[2/5] 导入笔记（408-master 下的 .md）...")
    _run("init_knowledge_base.py",
         ["--data-dir", src_dir, "--persist-dir", chroma_dir, "--bm25-path", bm25_path])

    # ---- 3. 从课件补齐章节 ----
    print("\n[3/5] 导入课件 PDF ...")
    for subject, d, chapter in ppt_dirs:
        _run("import_ppt.py", ["--dir", d, "--subject", subject, "--chapter", chapter])

    # ---- 4. 同步 SQLite ----
    print("\n[4/5] 同步 SQLite documents / chunks ...")
    _run("sync_kb_to_db.py", [])

    # ---- 5. 评测 ----
    if not args.skip_eval:
        print("\n[5/5] 检索评测 ...")
        _run("evaluate_retrieval.py", [])
    else:
        print("\n[5/5] 跳过评测（--skip-eval）")

    # ---- 汇总 ----
    from backend.rag.vector_store import ChromaStore
    from backend.rag.bm25_index import BM25Index
    vs = ChromaStore(persist_dir=chroma_dir)
    bm = BM25Index(persist_path=bm25_path)
    c_cnt, b_cnt = vs.collection.count(), len(bm._chunks)
    print("\n" + "=" * 68)
    print(" 重建完成")
    print("=" * 68)
    print(f"  Chroma chunks : {c_cnt}")
    print(f"  BM25  chunks  : {b_cnt}")
    print(f"  两者一致      : {c_cnt == b_cnt}")
    print("=" * 68)
    print("提示：请重启后端以载入新索引：python backend/main.py")


if __name__ == "__main__":
    main()