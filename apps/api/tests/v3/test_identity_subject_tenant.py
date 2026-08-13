from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ekb_api import models
from ekb_api.core.auth import create_access_token
from ekb_api.core.config import get_settings
from ekb_api.core.db import get_engine, get_session_local
from ekb_api.core.security import sign_payload
from ekb_api.domain import new_id, utc_now
from tests.v3 import ensure_v3_fixture_roles, install_v3_fixture_memberships

API = "/api/v1"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def subject_fixture() -> dict[str, str]:
    now = utc_now()
    tenant_a = models.Tenant(
        id=new_id(),
        name="Live identity tenant",
        role="OWNER",
        policy_version=7,
        created_at=now,
        updated_at=now,
    )
    tenant_b = models.Tenant(
        id=new_id(),
        name="Suspended identity tenant",
        role="OWNER",
        policy_version=1,
        created_at=now,
        updated_at=now,
    )
    customer = models.User(
        id=new_id(),
        name="Live customer",
        email=f"live-customer-{tenant_a.id}@example.com",
        tenant_id=tenant_a.id,
        role="CUSTOMER",
        created_at=now,
        updated_at=now,
    )
    owner = models.User(
        id=new_id(),
        name="Live owner",
        email=f"live-owner-{tenant_a.id}@example.com",
        tenant_id=tenant_a.id,
        role="OWNER",
        created_at=now,
        updated_at=now,
    )
    suspended = models.User(
        id=new_id(),
        name="Suspended member",
        email=f"suspended-{tenant_b.id}@example.com",
        tenant_id=tenant_b.id,
        role="MEMBER",
        created_at=now,
        updated_at=now,
    )
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.add_all([tenant_a, tenant_b, customer, owner, suspended])
        session.commit()
    ensure_v3_fixture_roles([tenant_a.id], ["member"])
    install_v3_fixture_memberships(
        [
            (tenant_a.id, customer.id, "legacy_customer", "ACTIVE"),
            (tenant_a.id, owner.id, "owner", "ACTIVE"),
            (tenant_b.id, suspended.id, "member", "SUSPENDED"),
        ]
    )
    with get_session_local()() as session:
        session.execute(
            models.Tenant.__table__.update()
            .where(models.Tenant.id == tenant_a.id)
            .values(policy_version=7)
        )
        session.execute(
            text(
                "UPDATE tenant_memberships SET status = 'SUSPENDED' "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": tenant_b.id, "user_id": suspended.id},
        )
        session.commit()
    return {
        "tenant_a": tenant_a.id,
        "tenant_b": tenant_b.id,
        "customer": customer.id,
        "owner": owner.id,
        "suspended": suspended.id,
    }


def test_me_uses_live_subject_membership_role_and_policy(
    client: TestClient, subject_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    token = create_access_token(
        subject_fixture["customer"],
        subject_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
        extra_capabilities=["team:user:manage"],
    )

    response = client.get(f"{API}/me", headers=_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["id"] == subject_fixture["customer"]
    assert body["user"]["role"] == "CUSTOMER"
    assert body["capabilities"] == ["kb:read", "qa:ask"]
    assert body["policy_version"] == 7
    assert body["tenant_id"] == subject_fixture["tenant_a"]
    assert response.headers["X-Request-Id"].startswith("req_")


def test_me_reflects_live_policy_change(
    client: TestClient, subject_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    token = create_access_token(
        subject_fixture["owner"], subject_fixture["tenant_a"], settings, tenant_role="MEMBER"
    )
    with get_session_local()() as session:
        session.execute(
            models.Tenant.__table__.update()
            .where(models.Tenant.id == subject_fixture["tenant_a"])
            .values(policy_version=11)
        )
        session.execute(
            text(
                "UPDATE tenant_memberships SET role_id = "
                "(SELECT id FROM tenant_roles WHERE tenant_id = :tenant_id AND slug = 'member') "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": subject_fixture["tenant_a"], "user_id": subject_fixture["owner"]},
        )
        session.commit()
    response = client.get(f"{API}/me", headers=_headers(token))
    assert response.status_code == 200
    assert response.json()["policy_version"] == 11
    assert response.json()["user"]["role"] == "MEMBER"
    assert response.json()["capabilities"] == ["audit:read", "kb:read", "qa:ask"]


def test_sqlite_business_connections_enforce_foreign_keys() -> None:
    engine = get_engine()
    with engine.connect() as first_connection:
        assert first_connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    with engine.connect() as second_connection:
        assert second_connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_me_rejects_missing_or_suspended_live_membership(
    client: TestClient, subject_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    missing_membership = create_access_token(
        new_id(),
        subject_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )
    suspended_membership = create_access_token(
        subject_fixture["suspended"],
        subject_fixture["tenant_b"],
        settings,
        tenant_role="OWNER",
    )

    missing = client.get(f"{API}/me", headers=_headers(missing_membership))
    suspended = client.get(f"{API}/me", headers=_headers(suspended_membership))

    assert missing.status_code in {401, 403}
    assert suspended.status_code == 403


def test_me_rejects_missing_bad_expired_and_refresh_tokens(
    client: TestClient, subject_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    path = f"{API}/me"
    expired = sign_payload(
        {
            "sub": subject_fixture["owner"],
            "tenant_id": subject_fixture["tenant_a"],
            "exp": int(time.time()) - 1,
        },
        settings.token_secret,
    )
    refresh = sign_payload(
        {
            "sub": subject_fixture["owner"],
            "tenant_id": subject_fixture["tenant_a"],
            "typ": "refresh",
            "exp": int(time.time()) + 60,
        },
        settings.token_secret,
    )
    for headers in (
        {},
        {"Authorization": "Bearer bad"},
        {"Authorization": f"Bearer {expired}"},
        {"Authorization": f"Bearer {refresh}"},
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert set(response.json()["error"]) == {"code", "message", "request_id", "details"}
