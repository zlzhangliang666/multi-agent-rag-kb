"""
ONNX Cross-Encoder Reranker - bge-reranker-base

用途：对 RRF 融合后的候选片段做精排，提升引用正确率。
约束：只依赖 onnxruntime + tokenizers，不引入 PyTorch。
"""
import logging
import os
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "models/bge-reranker-base-onnx"


class ONNXReranker:
    def __init__(self, model_dir: Optional[str] = None,
                 model_path: Optional[str] = None,
                 tokenizer_path: Optional[str] = None,
                 max_length: int = 512,
                 batch_size: int = 16):
        import onnxruntime

        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        if not model_path:
            candidates = [
                os.path.join(self.model_dir, "onnx", "model_quantized.onnx"),
                os.path.join(self.model_dir, "onnx", "model.onnx"),
                os.path.join(self.model_dir, "model.onnx"),
            ]
            for c in candidates:
                if os.path.isfile(c):
                    model_path = c
                    break
        if not model_path or not os.path.isfile(model_path):
            raise FileNotFoundError(f"reranker model not found: {model_path or self.model_dir}")

        self.tokenizer_path = tokenizer_path or os.path.join(self.model_dir, "tokenizer.json")
        if not os.path.isfile(self.tokenizer_path):
            raise FileNotFoundError(f"reranker tokenizer not found: {self.tokenizer_path}")

        logger.info("Loading reranker ONNX model: %s", model_path)
        self.session = onnxruntime.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.output_names = [o.name for o in self.session.get_outputs()]

        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(self.tokenizer_path)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.pad_id = self.tokenizer.token_to_id("<pad>")
        if self.pad_id is None:
            self.pad_id = 1
        self.max_length = max_length
        self.batch_size = max(1, int(batch_size))
        logger.info("Reranker loaded. inputs=%s outputs=%s",
                    sorted(self.input_names), self.output_names)

    def score(self, query: str, passages: Sequence[str]) -> List[float]:
        """返回每个 passage 与 query 的相关性分数（sigmoid 后的 0~1）"""
        if not passages:
            return []
        scores: List[float] = []
        for start in range(0, len(passages), self.batch_size):
            batch = list(passages[start:start + self.batch_size])
            encodings = [self.tokenizer.encode(query, p) for p in batch]
            max_len = max(len(e.ids) for e in encodings)
            input_ids = np.full((len(encodings), max_len), self.pad_id, dtype=np.int64)
            attention_mask = np.zeros_like(input_ids)
            for row, enc in enumerate(encodings):
                length = len(enc.ids)
                input_ids[row, :length] = enc.ids
                attention_mask[row, :length] = enc.attention_mask

            feed = {"input_ids": input_ids, "attention_mask": attention_mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)
            logits = self.session.run(None, feed)[0].reshape(-1)
            probs = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))
            scores.extend(float(x) for x in probs)
        return scores


def build_reranker(cfg: Optional[dict] = None) -> Optional[ONNXReranker]:
    """按配置构建 reranker；关闭或加载失败时返回 None（检索自动退回 RRF）"""
    cfg = cfg or {}
    if not cfg.get("enabled", False):
        return None
    try:
        return ONNXReranker(
            model_dir=cfg.get("model_dir", DEFAULT_MODEL_DIR),
            max_length=int(cfg.get("max_length", 512) or 512),
            batch_size=int(cfg.get("batch_size", 16) or 16),
        )
    except Exception as e:
        logger.warning("Reranker disabled (load failed): %s", e)
        return None
