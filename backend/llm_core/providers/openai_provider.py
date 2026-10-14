"""OpenAI 兼容协议 Provider（远程模型，流式）

只实现一种协议：OpenAI `/chat/completions` + `stream=true`（SSE）。
可覆盖：OpenAI、DeepSeek、Moonshot、智谱、通义兼容模式、
以及本地/自建的 Ollama、vLLM、LM Studio 等兼容端点。

产出与 LocalProvider 一致的事件 dict：
  {"status": "generating", "token": "..."}
  {"status": "error", "message": "..."}
  {"status": "done"}
"""
import json
import logging
from typing import AsyncGenerator, Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def normalize_base_url(url: str) -> str:
    """规整 base_url：去空白/尾斜杠，并容错用户误填的 /chat/completions 后缀。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        return DEFAULT_BASE_URL
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


def build_sse(obj: dict) -> str:
    """事件 dict → SSE 字符串（ensure_ascii=False 保留中文可读性）"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


async def _aiter(lines):
    """兼容同步可迭代对象（list/tuple，便于单测）与异步可迭代对象（httpx.aiter_lines）"""
    if hasattr(lines, "__aiter__"):
        async for item in lines:
            yield item
    else:
        for item in lines:
            yield item


async def iter_openai_sse(lines: Iterable) -> AsyncGenerator[dict, None]:
    """把 OpenAI 兼容的 SSE 行序列解析为事件 dict。

    lines 可以是 list[str]（单测）或 httpx 的 aiter_lines()（生产）。
    遇到 `[DONE]` 或含 error 字段的对象即结束。
    """
    async for raw in _aiter(lines):
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("忽略无法解析的 SSE 行: %s", line[:120])
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("error"):
            err = obj["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            yield {"status": "error", "message": msg or "远程模型返回错误"}
            return
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        token = delta.get("content")
        if token:
            yield {"status": "generating", "token": token}


class OpenAICompatibleProvider:
    """OpenAI 兼容协议的远程模型"""

    name = "openai"

    def __init__(self, api_key: str, base_url: str = "", model: str = "",
                 temperature: float = 0.7, max_tokens: int = 1024, timeout: float = 120.0):
        self.api_key = (api_key or "").strip()
        self.base_url = normalize_base_url(base_url)
        self.model = (model or "").strip() or "gpt-4o-mini"
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout = float(timeout)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _messages(self, prompt: str, system_prompt: Optional[str] = None) -> list:
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _payload(self, prompt: str, system_prompt: Optional[str] = None,
                 stream: bool = True, max_tokens: Optional[int] = None) -> dict:
        return {
            "model": self.model,
            "messages": self._messages(prompt, system_prompt),
            "temperature": self.temperature,
            "max_tokens": int(max_tokens if max_tokens else self.max_tokens),
            "stream": stream,
        }

    def is_ready(self) -> bool:
        # 远程模型无需本地加载，只要配置齐全即可用
        return bool(self.api_key)

    async def load_model(self) -> dict:
        return {"status": "ok", "message": "远程模型（OpenAI 兼容）无需加载本地权重"}

    async def unload_model(self) -> dict:
        return {"status": "ok", "message": "远程模型（OpenAI 兼容）无需卸载本地权重"}

    async def get_status(self) -> dict:
        return {
            "loaded": True,
            "generating": False,
            "worker_alive": False,
            "remote": True,
            "model": self.model,
            "base_url": self.base_url,
            "memory_mb": 0,
        }

    @staticmethod
    def _error_message(status_code: int, body: str) -> str:
        text = (body or "").strip()[:300]
        if status_code == 401:
            return f"远程模型鉴权失败（401）：请检查 API Key 是否正确。{text}"
        if status_code == 403:
            return f"远程模型拒绝访问（403）：请检查 API Key 权限或模型开通情况。{text}"
        if status_code == 404:
            return f"远程模型接口不存在（404）：请检查 Base URL 与模型名。{text}"
        if status_code == 429:
            return f"远程模型限流或额度不足（429）：请稍后重试。{text}"
        return f"远程模型请求失败（HTTP {status_code}）：{text}"

    async def generate(self, prompt: str, max_tokens: int = None,
                       system_prompt: str = None) -> AsyncGenerator[dict, None]:
        payload = self._payload(prompt, system_prompt, stream=True, max_tokens=max_tokens)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
                async with client.stream("POST", self.endpoint,
                                         headers=self._headers(), json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "ignore")
                        yield {"status": "error",
                               "message": self._error_message(response.status_code, body)}
                        return
                    async for event in iter_openai_sse(response.aiter_lines()):
                        yield event
        except httpx.TimeoutException:
            yield {"status": "error", "message": "远程模型生成超时"}
            return
        except Exception as e:                                  # 连接失败 / DNS / TLS 等
            logger.warning("远程模型请求异常: %s", e)
            yield {"status": "error", "message": f"远程模型连接失败：{e}"}
            return
        yield {"status": "done"}

    async def test_connection(self) -> dict:
        """测试连接：发一个 stream=False、max_tokens=1 的最小请求"""
        payload = self._payload("ping", system_prompt=None, stream=False, max_tokens=1)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                resp = await client.post(self.endpoint, headers=self._headers(), json=payload)
                if resp.status_code == 200:
                    return {"status": "ok", "message": "连接成功",
                            "model": self.model, "endpoint": self.endpoint}
                return {"status": "error",
                        "message": self._error_message(resp.status_code, resp.text)}
        except Exception as e:
            return {"status": "error", "message": f"连接失败：{e}"}
