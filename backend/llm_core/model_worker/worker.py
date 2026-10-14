"""
Model Worker - 独立子进程

作为独立进程运行，加载 Qwen2.5 GGUF 模型，通过 HTTP 提供推理服务。
主进程通过子进程管理模块（model_manager）与之通信。

启动方式（由 Manager 自动调用）：
    python backend/llm_core/model_worker/worker.py

持久化机制：
    启动后将端口和 PID 写入 data/worker_info.json
    这样即使 main 进程因 reload 重启，Manager 也能重连继续使用
"""

import sys
import json
import time
import os
import logging
import socket
import asyncio
from pathlib import Path
from threading import Lock

# 将项目根目录加入 path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="[WORKER] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("model_worker")


WORKER_INFO_FILE = ROOT_DIR / "data" / "worker_info.json"


def write_worker_info(port: int, pid: int):
    """将 Worker 信息写入持久化文件，供 Manager 重连使用"""
    try:
        os.makedirs(str(WORKER_INFO_FILE.parent), exist_ok=True)
        with open(str(WORKER_INFO_FILE), "w") as f:
            json.dump({"port": port, "pid": pid}, f)
        logger.info(f"Worker 信息已持久化: {WORKER_INFO_FILE}")
    except Exception as e:
        logger.warning(f"Worker 信息持久化失败: {e}")


def cleanup_worker_info():
    """清理 Worker 信息文件"""
    try:
        if WORKER_INFO_FILE.exists():
            WORKER_INFO_FILE.unlink()
    except Exception:
        pass


class ModelInstance:
    """模型实例封装 - 懒加载"""

    def __init__(self, config: dict):
        self.config = config
        self.model = None
        self.loaded = False
        self.lock = Lock()
        self._load_time = 0

    def load(self):
        """加载模型（线程安全）"""
        with self.lock:
            if self.loaded and self.model is not None:
                logger.info("模型已加载，跳过")
                return {"status": "ok", "message": "模型已加载"}

            logger.info("开始加载模型...")
            model_path = Path(self.config["model"]["path"])

            if not model_path.exists():
                err = f"模型文件不存在: {model_path}"
                logger.error(err)
                return {"status": "error", "message": err}

            try:
                from llama_cpp import Llama

                t0 = time.time()
                self.model = Llama(
                    model_path=str(model_path),
                    n_ctx=self.config["model"]["n_ctx"],
                    n_threads=self.config["model"]["n_threads"],
                    n_gpu_layers=self.config["model"]["n_gpu_layers"],
                    mmap=self.config["model"]["mmap"],
                    verbose=False,
                )
                self.loaded = True
                self._load_time = time.time()
                elapsed = time.time() - t0
                logger.info(f"模型加载完成，耗时 {elapsed:.1f}s")
                return {"status": "ok", "message": f"模型加载成功，耗时 {elapsed:.1f}s"}
            except Exception as e:
                self.loaded = False
                self.model = None
                logger.error(f"模型加载失败: {e}")
                return {"status": "error", "message": str(e)}

    def unload(self):
        """卸载模型，释放内存"""
        with self.lock:
            if not self.loaded or self.model is None:
                return {"status": "ok", "message": "模型未加载"}

            logger.info("卸载模型中...")
            try:
                del self.model
                self.model = None
                self.loaded = False
                import gc
                gc.collect()
                logger.info("模型已卸载，内存已释放")
                return {"status": "ok", "message": "模型已卸载"}
            except Exception as e:
                logger.error(f"卸载失败: {e}")
                return {"status": "error", "message": str(e)}

    def generate(self, prompt: str, stream: bool = True,
                 max_tokens: int = None, system_prompt: str = None):
        """生成回复（流式或非流式）

        Args:
            prompt: 用户输入
            stream: 是否流式输出
            max_tokens: 最大生成长度
            system_prompt: 系统提示词

        Yields:
            str: 生成的文本片段
        """
        if not self.loaded or self.model is None:
            yield "错误：模型未加载，请先调用 /load 接口"
            return

        max_tokens = max_tokens or self.config["model"]["max_tokens"]
        system_prompt = system_prompt or (
            "你是知识库问答助手。请仅依据给定的参考资料用中文回答问题；"
            "资料中找不到答案时如实说明，并建议提问知识库相关内容，不要编造。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            if stream:
                for chunk in self.model.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=True,
                    temperature=0.7,
                ):
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
            else:
                result = self.model.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=False,
                    temperature=0.7,
                )
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                yield content

        except Exception as e:
            logger.error(f"生成失败: {e}")
            yield f"\n\n[生成错误: {str(e)}]"

    def get_status(self) -> dict:
        """获取模型状态"""
        import psutil

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / 1024 / 1024

        status = {
            "loaded": self.loaded,
            "memory_mb": round(mem_mb, 1),
            "pid": os.getpid(),
        }
        if self.loaded and self._load_time > 0:
            status["load_time"] = self._load_time
            status["uptime"] = round(time.time() - self._load_time)
        return status


# ================================================================
# FastAPI 服务器
# ================================================================
def find_free_port():
    """查找可用端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def token_batcher(generator, batch_interval: float = 0.05, max_batch_size: int = 8):
    """token 批量收集器（纯同步实现，不使用 asyncio）

    将生成器产生的单个 token 收集成批次发送，减少 SSE 事件数量。
    满足以下任一条件即 flush：
      - 积累到 max_batch_size 个 token
      - 距上次 flush 超过 batch_interval 秒

    Args:
        generator: 原始 token 生成器
        batch_interval: 最大等待时间（秒）
        max_batch_size: 每批最大 token 数

    Yields:
        str: 合并后的文本块
    """
    buffer = []
    last_flush = time.time()

    for token in generator:
        buffer.append(token)
        now = time.time()
        if len(buffer) >= max_batch_size or (now - last_flush) >= batch_interval:
            yield "".join(buffer)
            buffer = []
            last_flush = time.time()

    if buffer:
        yield "".join(buffer)


def create_worker_app(model_instance: ModelInstance):
    """创建 Worker 的 FastAPI 应用"""
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse, JSONResponse
    from pydantic import BaseModel, Field

    app = FastAPI(title="Model Worker", version="1.0.0")

    class GenerateRequest(BaseModel):
        prompt: str = Field(..., description="用户输入")
        stream: bool = Field(True, description="是否流式输出")
        max_tokens: int = Field(None, description="最大生成长度")
        system_prompt: str = Field(None, description="系统提示词")

    @app.post("/load")
    async def load_model():
        result = model_instance.load()
        return JSONResponse(result)

    @app.post("/unload")
    async def unload_model():
        result = model_instance.unload()
        return JSONResponse(result)

    @app.get("/status")
    async def get_status():
        return JSONResponse(model_instance.get_status())

    @app.post("/generate")
    async def generate(req: GenerateRequest):
        if not model_instance.loaded:
            return JSONResponse(
                {"status": "error", "message": "模型未加载"},
                status_code=400,
            )

        def event_stream():
            try:
                # 使用 token_batcher 批量合并 token
                raw_gen = model_instance.generate(
                    prompt=req.prompt,
                    stream=req.stream,
                    max_tokens=req.max_tokens,
                    system_prompt=req.system_prompt,
                )
                for batch in token_batcher(raw_gen, batch_interval=0.05, max_batch_size=8):
                    yield f"data: {json.dumps({'token': batch, 'status': 'generating'})}\n\n"
                yield f"data: {json.dumps({'status': 'done'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def main():
    """Worker 入口"""
    try:
        from backend.config import get_config
        config = get_config()
    except Exception:
        import yaml
        config_path = ROOT_DIR / "backend" / "config" / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

    port = find_free_port()
    model_instance = ModelInstance(config)
    app = create_worker_app(model_instance)

    # 将端口和 PID 写入持久化文件（reload 后 Manager 可重连）
    write_worker_info(port, os.getpid())

    # 同时打印到 stdout（兼容旧版 Manager）
    print(json.dumps({"port": port, "pid": os.getpid()}), flush=True)

    logger.info(f"Worker 启动完毕，端口: {port}, PID: {os.getpid()}")

    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )

    # Worker 退出时清理信息文件
    cleanup_worker_info()


if __name__ == "__main__":
    main()