"""
配置加载模块

从 config.yaml 中读取配置，提供统一的配置访问接口。

支持环境变量展开：配置值中可写 `${VAR}` 或 `${VAR:-default}`。
用于把「运行时数据目录」参数化，例如 `${KB_DATA_DIR:-data}`：
  - 默认落在项目内 `data/`（随项目一起迁移，自包含）；
  - 受限环境（如 TRAE 沙箱）可用 KB_DATA_DIR 指向工作区外的可写目录。
"""

import os
import re
import yaml
from pathlib import Path


# 项目根目录（backend 的父目录）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# 默认配置文件路径
DEFAULT_CONFIG_PATH = ROOT_DIR / "backend" / "config" / "config.yaml"

# 匹配 ${VAR} 或 ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _expand_env_str(value: str) -> str:
    """展开字符串中的 ${VAR} / ${VAR:-default}"""
    def _repl(match: "re.Match") -> str:
        name, default = match.group(1), match.group(2)
        return os.environ.get(name, default if default is not None else "")
    return _ENV_PATTERN.sub(_repl, value)


def _expand_env(node):
    """递归展开配置树中的所有字符串值"""
    if isinstance(node, dict):
        return {k: _expand_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env(v) for v in node]
    if isinstance(node, str):
        return _expand_env_str(node)
    return node


def _resolve_sqlite_url(url: str, root_dir: Path) -> str:
    """把 sqlite:///<相对路径> 解析为绝对路径"""
    prefix = "sqlite:///"
    if isinstance(url, str) and url.startswith(prefix):
        raw = url[len(prefix):]
        if raw and not os.path.isabs(raw):
            raw = str(root_dir / raw)
        return prefix + raw.replace("\\", "/")
    return url


def load_config(config_path: str | Path = None) -> dict:
    """加载 YAML 配置文件

    Args:
        config_path: 配置文件路径，默认为 backend/config/config.yaml

    Returns:
        配置字典（相对路径已转为绝对路径，环境变量已展开）
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 先展开环境变量，再把相对路径转绝对路径
    config = _expand_env(config)
    _resolve_paths(config, ROOT_DIR)

    return config


def _resolve_paths(config: dict, root_dir: Path):
    """将配置中的相对路径转换为绝对路径"""
    path_keys = {
        "model": ["path"],
        "embedding": ["model_dir", "cache_path"],
        "chroma": ["persist_directory"],
        "retrieval": ["bm25_index_path"],
        "upload": ["docs_dir"],
        "static": ["images_dir"],
        "logging": ["file"],
        "datasource": ["path"],
    }

    for section, keys in path_keys.items():
        if section in config:
            for key in keys:
                if key in config[section]:
                    val = config[section][key]
                    if isinstance(val, str) and not os.path.isabs(val):
                        config[section][key] = str(root_dir / val)

    # database.url 单独处理（sqlite:/// 前缀 + 相对路径）
    db = config.get("database")
    if isinstance(db, dict) and "url" in db:
        db["url"] = _resolve_sqlite_url(db["url"], root_dir)


# 全局配置实例（懒加载）
_config = None


def get_config() -> dict:
    """获取全局配置（单例）"""
    global _config
    if _config is None:
        _config = load_config()
    return _config