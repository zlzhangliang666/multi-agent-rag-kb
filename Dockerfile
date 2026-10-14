# =============================================================================
# 后端镜像（可选部署方式之一，非唯一启动方式）
# -----------------------------------------------------------------------------
# 构建：  docker build -t kb-qa-backend -f Dockerfile .
# 运行：  docker run --rm -p 8000:8000 \
#           -v "$(pwd)/models:/app/models:ro" \
#           -v "$(pwd)/408-master:/app/408-master:ro" \
#           -v kb_data:/app/data \
#           -e KB_DATA_DIR=/app/data kb-qa-backend
# 说明：  models/ 与 408-master/ 通过卷挂载（体积大，不进镜像）；
#         data/ 用命名卷持久化（向量库 / SQLite / BM25 / 上传文件）。
# =============================================================================
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KB_DATA_DIR=/app/data

WORKDIR /app

# llama-cpp-python 需要编译工具链（若使用 CPU 预编译轮子可省，但保底装上）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ cmake make libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# 优先使用 llama-cpp-python 的 CPU 预编译轮子源
RUN pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
        -r requirements.txt

COPY backend ./backend
COPY scripts ./scripts
COPY evaluation ./evaluation

EXPOSE 8000

# 首次启动前需保证 data/ 中已有知识库（可用 scripts/rebuild_all.py 重建）
CMD ["python", "backend/main.py"]
