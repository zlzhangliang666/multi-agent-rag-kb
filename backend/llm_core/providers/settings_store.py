"""LLM Provider 配置持久化（存 SQLite 单行）。

安全约定：
  - API Key **只存后端**，读取接口脱敏回显（仅末 4 位），且不写日志；
  - 未配置 api_key 时一律回退到本地模型。
"""
import logging

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "provider": "local",                              # local / openai
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 1024,
    "fallback_to_local": False,
}


def _row_to_dict(row) -> dict:
    return {
        "provider": (row.provider or "local"),
        "api_key": (row.api_key or ""),
        "base_url": (row.base_url or ""),
        "model": (row.model or ""),
        "temperature": float(row.temperature if row.temperature is not None else 0.7),
        "max_tokens": int(row.max_tokens if row.max_tokens else 1024),
        "fallback_to_local": bool(row.fallback_to_local),
    }


def load_settings() -> dict:
    """读取配置；表/行不存在时使用默认（本地）配置。"""
    try:
        from backend.database import SessionLocal
        from backend.database.models import LLMSettings
    except Exception as e:                                  # pragma: no cover
        logger.warning("无法导入数据库模块，使用默认 LLM 配置: %s", e)
        return DEFAULT_SETTINGS.copy()

    db = SessionLocal()
    try:
        row = db.query(LLMSettings).order_by(LLMSettings.id).first()
        if row is None:
            row = LLMSettings(id=1, provider="local", api_key="",
                              base_url=DEFAULT_SETTINGS["base_url"],
                              model=DEFAULT_SETTINGS["model"],
                              temperature=0.7, max_tokens=1024, fallback_to_local=0)
            db.add(row)
            db.commit()
            db.refresh(row)
        cfg = DEFAULT_SETTINGS.copy()
        cfg.update(_row_to_dict(row))
        return cfg
    except Exception as e:
        logger.warning("读取 LLM 配置失败，使用默认本地配置: %s", e)
        return DEFAULT_SETTINGS.copy()
    finally:
        db.close()


def save_settings(data: dict) -> dict:
    """保存配置。api_key 传空串表示「保持原值不变」。"""
    from backend.database import SessionLocal
    from backend.database.models import LLMSettings

    db = SessionLocal()
    try:
        row = db.query(LLMSettings).order_by(LLMSettings.id).first()
        if row is None:
            row = LLMSettings(id=1)
            db.add(row)

        if data.get("provider"):
            row.provider = str(data["provider"]).strip()
        if data.get("api_key"):                              # 空串 = 保持原值
            row.api_key = str(data["api_key"]).strip()
        if data.get("base_url") is not None:
            row.base_url = str(data["base_url"]).strip()
        if data.get("model") is not None:
            row.model = str(data["model"]).strip()
        if data.get("temperature") is not None:
            row.temperature = float(data["temperature"])
        if data.get("max_tokens") is not None:
            row.max_tokens = int(data["max_tokens"])
        if data.get("fallback_to_local") is not None:
            row.fallback_to_local = 1 if data["fallback_to_local"] else 0

        db.commit()
        db.refresh(row)
        cfg = DEFAULT_SETTINGS.copy()
        cfg.update(_row_to_dict(row))
        return cfg
    finally:
        db.close()


def mask_key(key: str) -> str:
    """脱敏：仅保留末 4 位，其余以 * 代替。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


def public_view(cfg: dict) -> dict:
    """给前端的可见视图：api_key 脱敏，不含明文。"""
    key = cfg.get("api_key", "") or ""
    return {
        "provider": cfg.get("provider", "local"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 1024),
        "fallback_to_local": bool(cfg.get("fallback_to_local", False)),
        "api_key_masked": mask_key(key),
        "has_api_key": bool(key),
    }
