"""
Model Manager - 模型管理器

负责管理模型工作子进程的生命周期：
- 启动/停止 Worker 子进程（支持 reload 后重连已有 Worker）
- 模型懒加载、全局单实例
- 空闲 180 秒自动卸载
- 请求锁（确保单次只有一个请求占用模型）
- 对外提供统一的生成/加载/卸载/状态接口
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import httpx

logger = logging.getLogger("model_manager")

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent

WORKER_INFO_FILE = ROOT_DIR / "data" / "worker_info.json"


def _check_worker_alive(port: int, pid: int) -> bool:
    """检查指定端口和 PID 的 Worker 是否存活"""
    # 检查进程是否存在
    try:
        import psutil
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        # 检查是否监听着正确的端口
        for conn in proc.connections():
            if conn.laddr.port == port:
                return True
        return False
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except ImportError:
        # 没有 psutil 时，简单的进程存在性检查
        try:
            os.kill(pid, 0)  # 信号 0 用于检测进程是否存在
            return True
        except OSError:
            return False


def _read_worker_info() -> Optional[dict]:
    """从持久化文件中读取 Worker 信息"""
    try:
        if WORKER_INFO_FILE.exists():
            with open(str(WORKER_INFO_FILE), "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


class ModelManager:
    """模型管理器（单例）"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        self._worker_process: Optional[subprocess.Popen] = None
        self._worker_port: Optional[int] = None
        self._worker_pid: Optional[int] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

        # 空闲计时
        self._last_activity_time: float = 0
        self._idle_timeout: int = 180  # 秒
        self._idle_task: Optional[asyncio.Task] = None
        self._is_running = True

        # 模型状态
        self._loaded: bool = False
        self._generating: bool = False
        self._load_error: Optional[str] = None
        self._memory_mb: float = 0

        # 锁
        self._generate_lock = asyncio.Lock()

    # ---- Worker 进程管理 ----

    async def _reconnect_to_existing_worker(self, info: dict) -> bool:
        """尝试重连到已有的 Worker 进程"""
        port = info.get("port")
        pid = info.get("pid")

        if not port or not pid:
            return False

        if not _check_worker_alive(port, pid):
            logger.info(f"已有 Worker (PID={pid}) 已不存在，需新建")
            return False

        logger.info(f"发现存活 Worker: 端口={port}, PID={pid}，正在重连...")

        try:
            client = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=httpx.Timeout(300.0, connect=5.0),
            )
            # 测试连接
            resp = await client.get("/status")
            if resp.status_code == 200:
                self._worker_port = port
                self._worker_pid = pid
                self._http_client = client

                # 查询模型是否已加载
                status = resp.json()
                self._loaded = status.get("loaded", False)
                self._memory_mb = status.get("memory_mb", 0)
                if self._loaded:
                    self._update_activity()

                # 启动空闲检测任务
                self._idle_task = asyncio.create_task(self._idle_checker())

                logger.info(f"Worker 重连成功！模型已加载={self._loaded}")
                return True
            await client.aclose()
        except Exception as e:
            logger.warning(f"Worker 重连失败: {e}")
            if self._http_client:
                await self._http_client.aclose()
            self._http_client = None

        return False

    async def start_worker(self) -> bool:
        """启动 Worker 子进程

        支持 reload 安全：
        1. 先检查 data/worker_info.json 中是否有存活的 Worker
        2. 有则重连，无则新建
        3. 新建 Worker 使用 DETACHED_PROCESS 避免被父进程杀死
        """
        # 步骤 1：尝试重连已有 Worker
        existing = _read_worker_info()
        if existing:
            logger.info("发现 Worker 信息文件，尝试重连...")
            if await self._reconnect_to_existing_worker(existing):
                return True
            logger.info("重连失败，将启动新 Worker")

        # 步骤 2：启动新 Worker
        if self._worker_process and self._worker_process.poll() is None:
            logger.info("Worker 已在运行中")
            return True

        logger.info("启动新的 Worker 子进程...")

        # 使用项目虚拟环境的 Python
        venv_python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
        python_path = str(venv_python) if venv_python.exists() else sys.executable

        worker_script = ROOT_DIR / "backend" / "llm_core" / "model_worker" / "worker.py"

        try:
            # Windows 下使用 DETACHED_PROCESS 标志
            # 使其独立于父进程，父进程退出/重启后 Worker 继续运行
            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # 0x00000200 | 0x00000008

            self._worker_process = subprocess.Popen(
                [python_path, str(worker_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(ROOT_DIR),
                text=True,
                bufsize=1,
                creationflags=creation_flags,
            )

            # 读取握手信息（从 stdout 读取第一行 JSON）
            line = self._worker_process.stdout.readline().strip()
            handshake = json.loads(line)

            self._worker_port = handshake["port"]
            self._worker_pid = handshake.get("pid", self._worker_process.pid)
            logger.info(f"Worker 启动成功，端口: {self._worker_port}, PID: {self._worker_pid}")

            # 创建 HTTP 客户端
            self._http_client = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{self._worker_port}",
                timeout=httpx.Timeout(300.0, connect=10.0),
            )

            # 启动后台任务
            self._idle_task = asyncio.create_task(self._idle_checker())

            return True

        except Exception as e:
            logger.error(f"Worker 启动失败: {e}")
            if self._worker_process:
                try:
                    self._worker_process.kill()
                except Exception:
                    pass
            self._worker_process = None
            return False

    async def stop_worker(self):
        """停止 Worker 子进程

        正常关闭时停止 Worker；如果是 reload 场景，Worker 会继续保持运行
        """
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None

        self._is_running = False

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        # 只有当我们有 Worker 进程引用（即是我们启动的）时才终止它
        if self._worker_process:
            logger.info("停止 Worker 子进程...")
            try:
                if os.name == "nt":
                    self._worker_process.terminate()
                else:
                    self._worker_process.send_signal(signal.SIGTERM)
                self._worker_process.wait(timeout=5)
            except Exception:
                try:
                    self._worker_process.kill()
                except Exception:
                    pass
            self._worker_process = None
            logger.info("Worker 已停止")

        self._loaded = False
        self._worker_port = None

    @property
    def is_worker_alive(self) -> bool:
        if self._http_client:
            return True
        if self._worker_process and self._worker_process.poll() is None:
            return True
        return False

    # ---- 空闲检测 ----

    async def _idle_checker(self):
        """后台任务：定期检查空闲超时，自动卸载"""
        while self._is_running:
            await asyncio.sleep(10)
            if not self._loaded:
                continue

            idle_seconds = time.time() - self._last_activity_time
            if idle_seconds >= self._idle_timeout:
                logger.info(f"模型空闲 {idle_seconds:.0f}s，超时 {self._idle_timeout}s，自动卸载")
                await self._unload_internal()
                import gc
                gc.collect()

    def _update_activity(self):
        """更新最后活动时间"""
        self._last_activity_time = time.time()

    # ---- 模型操作 ----

    async def load_model(self) -> dict:
        """加载模型"""
        async with self._lock:
            if not self.is_worker_alive:
                ok = await self.start_worker()
                if not ok:
                    return {"status": "error", "message": "无法启动 Worker 进程"}

            try:
                resp = await self._http_client.post("/load")
                result = resp.json()
                if result.get("status") == "ok":
                    self._loaded = True
                    self._load_error = None
                    self._update_activity()
                else:
                    self._load_error = result.get("message")
                return result
            except Exception as e:
                self._load_error = str(e)
                return {"status": "error", "message": str(e)}

    async def _unload_internal(self):
        """内部卸载（不加 generate_lock）"""
        if not self._loaded:
            return
        try:
            await self._http_client.post("/unload")
        except Exception:
            pass
        self._loaded = False
        self._memory_mb = 0

    async def unload_model(self) -> dict:
        """手动卸载模型"""
        async with self._lock:
            if not self._loaded:
                return {"status": "ok", "message": "模型未加载"}
            try:
                resp = await self._http_client.post("/unload")
                result = resp.json()
                self._loaded = False
                self._memory_mb = 0
                return result
            except Exception as e:
                return {"status": "error", "message": str(e)}

    async def get_status(self) -> dict:
        """获取模型状态（含 Worker 状态）"""
        status = {
            "loaded": self._loaded,
            "generating": self._generating,
            "worker_alive": self.is_worker_alive,
            "error": self._load_error,
        }

        if self.is_worker_alive and self._http_client:
            try:
                resp = await self._http_client.get("/status")
                worker_status = resp.json()
                status["memory_mb"] = worker_status.get("memory_mb", 0)
                status["worker_pid"] = worker_status.get("pid")
            except Exception:
                pass

        if self._loaded:
            idle_seconds = time.time() - self._last_activity_time
            status["idle_seconds"] = round(idle_seconds, 1)
            status["remaining_before_unload"] = max(0, round(self._idle_timeout - idle_seconds, 1))

        return status

    async def generate(self, prompt: str, max_tokens: int = None, system_prompt: str = None) -> AsyncGenerator[str, None]:
        """流式生成回复

        Args:
            prompt: 用户输入
            max_tokens: 最大 token 数
            system_prompt: 系统提示词

        Yields:
            生成的文本片段（通过 SSE data 事件）
        """
        if not self._loaded:
            yield "data: " + json.dumps({"status": "error", "message": "模型未加载"}) + "\n\n"
            return

        async with self._generate_lock:
            self._generating = True
            self._update_activity()

            try:
                params = {
                    "prompt": prompt,
                    "stream": True,
                }
                if max_tokens is not None:
                    params["max_tokens"] = max_tokens
                if system_prompt is not None:
                    params["system_prompt"] = system_prompt

                async with self._http_client.stream("POST", "/generate", json=params, timeout=300.0) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            yield line + "\n\n"
                            try:
                                data = json.loads(line[6:])
                                if data.get("status") == "generating":
                                    self._update_activity()
                            except json.JSONDecodeError:
                                pass

            except httpx.TimeoutException:
                yield "data: " + json.dumps({"status": "error", "message": "生成超时"}) + "\n\n"
            except Exception as e:
                logger.error(f"生成请求失败: {e}")
                yield "data: " + json.dumps({"status": "error", "message": str(e)}) + "\n\n"
            finally:
                self._generating = False
                self._update_activity()

    async def update_config(self, config: dict):
        """更新配置（如 idle_timeout 等）"""
        if "model" in config and "idle_timeout" in config["model"]:
            self._idle_timeout = config["model"]["idle_timeout"]
            logger.info(f"空闲超时更新为 {self._idle_timeout}s")


# 全局单例
_model_manager = None


def get_model_manager() -> ModelManager:
    """获取模型管理器全局单例"""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager