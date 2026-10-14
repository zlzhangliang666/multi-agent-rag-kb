"""API 冒烟测试：健康检查与根路由（不触发 lifespan，不拉起模型子进程）。"""
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_root_returns_service_info():
    r = client.get("/")
    assert r.status_code == 200
    assert "docs" in r.json()
