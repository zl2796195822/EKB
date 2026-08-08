from __future__ import annotations

import os
import tempfile

# 必须在导入 app 之前设定测试环境，避免加载 .env 中的外部 API 配置，并使用独立测试库。
os.environ["EKB_ENV"] = "test"
os.environ["EKB_DEV_PASSWORD"] = "test-password"
os.environ["EKB_DEV_IS_PLATFORM_ADMIN"] = "true"

_TEST_DB = os.path.join(tempfile.gettempdir(), "ekb_m2_test.db")
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ["EKB_DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ekb_api.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def dev_token(client: TestClient) -> str:
    """平台管理员（dev 账号）令牌，用于开通租户/邀请成员。"""
    return login(client, "admin@example.com", "test-password")
