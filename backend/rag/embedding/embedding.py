"""
ONNX Embedding - bge-small-zh-v1.5

使用 onnxruntime CPUExecutionProvider，禁止引入 PyTorch。
单例模式：模型只加载一次。
"""
import json
import logging
import math
import os
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class ONNXEmbedding:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, model_dir: Optional[str] = None,
                 model_path: Optional[str] = None,
                 tokenizer_path: Optional[str] = None,
                 config_path: Optional[str] = None):
        if self._initialized:
            return

        import onnxruntime

        self.model_dir = model_dir or os.environ.get(
            "EMBEDDING_MODEL_DIR",
            os.path.join(os.path.dirname(__file__), "models", "bge-small-zh-v1.5")
        )
        # 自动发现模型文件（支持 onnx/ 子目录等常见布局）
        if model_path:
            self.model_path = model_path
        else:
            candidates = [
                os.path.join(self.model_dir, "model.onnx"),
                os.path.join(self.model_dir, "model_q4.onnx"),
                os.path.join(self.model_dir, "model_quantized.onnx"),
                os.path.join(self.model_dir, "onnx", "model.onnx"),
                os.path.join(self.model_dir, "onnx", "model_q4.onnx"),
                os.path.join(self.model_dir, "onnx", "model_quantized.onnx"),
            ]
            self.model_path = None
            for c in candidates:
                if os.path.isfile(c):
                    self.model_path = c
                    break
            if self.model_path is None:
                self.model_path = candidates[0]  # 默认第一个

        self.tokenizer_path = tokenizer_path or os.path.join(self.model_dir, "tokenizer.json")
        self.config_path = config_path or os.path.join(self.model_dir, "config.json")
        self.batch_size = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))

        self.config = {}
        if os.path.isfile(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)

        self.max_length = self.config.get("max_position_embeddings", 512)
        self.hidden_size = self.config.get("hidden_size", 384)

        logger.info("Loading ONNX model from: %s", self.model_path)
        self.session = onnxruntime.InferenceSession(
            self.model_path, providers=["CPUExecutionProvider"]
        )
        logger.info("ONNX model loaded. Input names: %s, Output names: %s",
                     [inp.name for inp in self.session.get_inputs()],
                     [out.name for out in self.session.get_outputs()])

        self._load_tokenizer()
        self._initialized = True

    def _load_tokenizer(self):
        if os.path.isfile(self.tokenizer_path):
            with open(self.tokenizer_path, "r", encoding="utf-8") as f:
                self.tokenizer_data = json.load(f)
            logger.info("Tokenizer loaded from: %s", self.tokenizer_path)
        else:
            logger.warning("Tokenizer not found at %s", self.tokenizer_path)
            self.tokenizer_data = None

    def encode(self, texts: List[str], normalize: bool = True,
               show_progress: bool = False) -> np.ndarray:
        if not texts:
            return np.empty((0, self.hidden_size), dtype=np.float32)

        all_embeddings = []
        num_batches = math.ceil(len(texts) / self.batch_size)
        iterator = range(num_batches)

        if show_progress:
            try:
                from tqdm import tqdm
                iterator = tqdm(iterator, desc="Encoding batches")
            except ImportError:
                pass

        for i in iterator:
            batch_texts = texts[i * self.batch_size:(i + 1) * self.batch_size]
            batch_emb = self._encode_batch(batch_texts)
            all_embeddings.append(batch_emb)

        result = np.concatenate(all_embeddings, axis=0)
        if normalize:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            result = result / norms
        return result

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        input_ids, attention_mask, token_type_ids = self._tokenize(texts)
        outputs = self.session.run(
            output_names=["last_hidden_state"],
            input_feed={
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        last_hidden_state = outputs[0]
        mask = attention_mask.astype(np.float32)
        mask_expanded = np.expand_dims(mask, axis=-1)
        sum_emb = np.sum(last_hidden_state * mask_expanded, axis=1)
        sum_mask = np.sum(mask_expanded, axis=1)
        sum_mask = np.where(sum_mask == 0, 1e-9, sum_mask)
        embeddings = sum_emb / sum_mask
        return embeddings

    def _tokenize(self, texts: List[str]) -> tuple:
        if self.tokenizer_data:
            try:
                from tokenizers import Tokenizer
                tokenizer = Tokenizer.from_file(self.tokenizer_path)
                tokenizer.enable_padding(pad_id=self.tokenizer_data.get("pad_id", 0),
                                          pad_token="[PAD]", length=self.max_length)
                tokenizer.enable_truncation(max_length=self.max_length)
                batch = tokenizer.encode_batch(texts)
                input_ids = np.array([enc.ids for enc in batch], dtype=np.int64)
                attention_mask = np.array([enc.attention_mask for enc in batch], dtype=np.int64)
                token_type_ids = np.zeros_like(input_ids)
                return input_ids, attention_mask, token_type_ids
            except ImportError:
                pass
        return self._tokenize_simple(texts)

    def _tokenize_simple(self, texts: List[str]) -> tuple:
        batch_size = len(texts)
        input_ids = np.zeros((batch_size, self.max_length), dtype=np.int64)
        attention_mask = np.zeros((batch_size, self.max_length), dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)
        cls_id, sep_id, pad_id = 101, 102, 0
        for i, text in enumerate(texts):
            ids = [cls_id]
            for j, ch in enumerate(text[:self.max_length - 2]):
                ids.append((ord(ch) % 1000) + 200)
            ids.append(sep_id)
            seq_len = min(len(ids), self.max_length)
            input_ids[i, :seq_len] = ids[:seq_len]
            attention_mask[i, :seq_len] = 1
        return input_ids, attention_mask, token_type_ids

    @property
    def embedding_dim(self) -> int:
        return self.hidden_size