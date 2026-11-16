"""pytest 入口：暴露项目根目录，并提供真实 PowerContext 服务上的客户端夹具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 测试使用独立的本地运行目录，避免污染演示数据；PowerContext 仍是真实服务。
os.environ.setdefault("RECALL_DATA_DIR", str(ROOT / "data" / "test-runtime"))
os.environ.setdefault("RECALL_LOG_DIR", str(ROOT / "var" / "test-log"))


@pytest.fixture(scope="session")
def live_settings():
    from app.config import load_settings

    settings = load_settings()
    if os.environ.get("RECALL_SKIP_LIVE") == "1":
        pytest.skip("按需跳过真实服务测试")
    return settings


@pytest.fixture(scope="session")
def live_app(live_settings):
    """真实应用实例：需要注入故障或读取内部状态时使用。"""
    from app.main import create_app

    return create_app(live_settings)


@pytest.fixture(scope="session")
def live_client(live_app):
    """连接真实 PowerContext Server 的测试客户端；服务缺失时立即失败而非伪造。"""
    from fastapi.testclient import TestClient

    with TestClient(live_app) as client:
        response = client.get("/api/context")
        if response.status_code != 200:
            pytest.fail(
                "PowerContext Server 未就绪，真实验收需要先启动服务：scripts/start_powercontext.sh\n"
                f"HTTP {response.status_code}: {response.text[:400]}"
            )
        yield client
