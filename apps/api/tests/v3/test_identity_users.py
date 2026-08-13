from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from ekb_api import models
from ekb_api.core.auth import create_access_token, create_refresh_token
from ekb_api.core.config import get_settings
from ekb_api.core.db import get_session_local
from ekb_api.core.security import sign_payload
from ekb_api.domain import new_id, utc_now
from tests.v3 import install_v3_fixture_memberships

API = "/api/v1"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def identity_fixture() -> dict[str, str]:
    now = utc_now()
    tenant_a = models.Tenant(
        id=new_id(),
        name="Northwind Research",
        role="OWNER",
        policy_version=7,
        created_at=now,
        updated_at=now,
    )
    tenant_b = models.Tenant(
        id=new_id(),
        name="Contoso Support",
        role="OWNER",
        policy_version=3,
        created_at=now,
        updated_at=now,
    )
    owner_a = models.User(
        id=new_id(),
        name="Northwind owner",
        email=f"owner-{tenant_a.id}@example.com",
        tenant_id=tenant_a.id,
        role="OWNER",
        created_at=now,
        updated_at=now,
    )
    customer_a = models.User(
        id=new_id(),
        name="Northwind customer",
        email=f"customer-{tenant_a.id}@example.com",
        tenant_id=tenant_a.id,
        role="CUSTOMER",
        created_at=now,
        updated_at=now,
    )
    member_b = models.User(
        id=new_id(),
        name="Contoso member",
        email=f"member-{tenant_b.id}@example.com",
        tenant_id=tenant_b.id,
        role="MEMBER",
        created_at=now,
        updated_at=now,
    )
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.add_all([tenant_a, tenant_b, owner_a, customer_a, member_b])
        session.commit()
    install_v3_fixture_memberships(
        [
            (tenant_a.id, owner_a.id, "owner", "ACTIVE"),
            (tenant_a.id, customer_a.id, "legacy_customer", "ACTIVE"),
            (tenant_b.id, member_b.id, "member", "ACTIVE"),
        ]
    )
    return {
        "tenant_a": tenant_a.id,
        "tenant_b": tenant_b.id,
        "owner_a": owner_a.id,
        "customer_a": customer_a.id,
        "member_b": member_b.id,
        "owner_email": owner_a.email,
        "customer_email": customer_a.email,
    }


def test_tenant_user_list_is_scoped_and_has_v3_envelope(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    """The tracer route returns only the live request tenant's safe user fields."""
    settings = get_settings()
    token = create_access_token(
        identity_fixture["owner_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )

    response = client.get(
        f"{API}/tenants/{identity_fixture['tenant_a']}/users?page_size=20",
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"items", "next_cursor", "page_size"}
    assert response.json()["page_size"] == 20
    assert {item["email"] for item in response.json()["items"]} == {
        identity_fixture["owner_email"],
        identity_fixture["customer_email"],
    }
    assert identity_fixture["member_b"] not in response.text
    assert all(
        set(item)
        == {
            "id",
            "email",
            "display_name",
            "department",
            "role",
            "role_id",
            "status",
            "joined_at",
            "updated_at",
        }
        for item in response.json()["items"]
    )


def test_healthz_keeps_ok_and_exposes_pgvector_state(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["pgvector"]) == {"required", "available", "status"}


def test_tenant_user_list_rejects_wrong_tenant_and_fake_claim_capability(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    """URL tenant and live role/capability, not token claims, control access."""
    settings = get_settings()
    token = create_access_token(
        identity_fixture["customer_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
        extra_capabilities=["team:user:read"],
    )

    wrong_tenant = client.get(
        f"{API}/tenants/{identity_fixture['tenant_b']}/users",
        headers=_headers(token),
    )
    platform_token = create_access_token(
        identity_fixture["owner_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
        platform_role="PLATFORM_ADMIN",
    )
    platform_wrong_tenant = client.get(
        f"{API}/tenants/{identity_fixture['tenant_b']}/users",
        headers=_headers(platform_token),
    )
    nonexistent_tenant = client.get(
        f"{API}/tenants/{new_id()}/users",
        headers=_headers(platform_token),
    )
    insufficient = client.get(
        f"{API}/tenants/{identity_fixture['tenant_a']}/users",
        headers=_headers(token),
    )

    assert wrong_tenant.status_code == 403
    assert platform_wrong_tenant.status_code == 403
    assert nonexistent_tenant.status_code == 403
    assert identity_fixture["member_b"] not in wrong_tenant.text
    assert insufficient.status_code == 403
    fake_platform_route = client.get(
        f"{API}/admin/tenants",
        headers=_headers(platform_token),
    )
    assert fake_platform_route.status_code == 403


def test_tenant_user_list_filters_pages_and_binds_cursor(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    token = create_access_token(
        identity_fixture["owner_a"], identity_fixture["tenant_a"], settings, tenant_role="MEMBER"
    )
    path = f"{API}/tenants/{identity_fixture['tenant_a']}/users"

    first = client.get(path, headers=_headers(token), params={"page_size": 1})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"]

    second = client.get(
        path,
        headers=_headers(token),
        params={"page_size": 1, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    assert {item["id"] for item in first_body["items"]}.isdisjoint(
        item["id"] for item in second.json()["items"]
    )

    filtered = client.get(
        path,
        headers=_headers(token),
        params={"role": "legacy_customer", "query": "customer", "page_size": 100},
    )
    assert filtered.status_code == 200, filtered.text
    assert [item["email"] for item in filtered.json()["items"]] == [
        identity_fixture["customer_email"]
    ]

    tampered = first_body["next_cursor"][:-1] + (
        "A" if first_body["next_cursor"][-1] != "A" else "B"
    )
    assert client.get(path, headers=_headers(token), params={"cursor": tampered}).status_code == 400
    assert (
        client.get(
            path,
            headers=_headers(token),
            params={"cursor": first_body["next_cursor"], "query": "owner"},
        ).status_code
        == 400
    )


def test_tenant_user_list_auth_errors_and_safe_fields(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    path = f"{API}/tenants/{identity_fixture['tenant_a']}/users"
    refresh_token = create_refresh_token(
        identity_fixture["owner_a"], identity_fixture["tenant_a"], settings, role="OWNER"
    )
    expired_token = sign_payload(
        {
            "sub": identity_fixture["owner_a"],
            "tenant_id": identity_fixture["tenant_a"],
            "exp": int(time.time()) - 1,
        },
        settings.token_secret,
    )
    for authorization in (
        None,
        "Bearer malformed",
        f"Bearer {expired_token}",
        f"Bearer {refresh_token}",
    ):
        headers = {} if authorization is None else {"Authorization": authorization}
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert set(response.json()["error"]) == {"code", "message", "request_id", "details"}

    owner_token = create_access_token(
        identity_fixture["owner_a"], identity_fixture["tenant_a"], settings, tenant_role="OWNER"
    )
    response = client.get(path, headers=_headers(owner_token))
    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "secret_hash" not in response.text
    assert "token" not in response.text.lower()

    validation = client.get(path, headers=_headers(owner_token), params={"page_size": 101})
    assert validation.status_code == 400
    assert set(validation.json()["error"]) == {"code", "message", "request_id", "details"}


def test_existing_admin_user_create_path_remains_registered(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    token = create_access_token(
        identity_fixture["owner_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )

    owner_invite = client.post(
        f"{API}/admin/users",
        headers=_headers(token),
        json={
            "email": f"compat-owner-{identity_fixture['tenant_a']}@example.com",
            "name": "V3 compatibility",
            "password": "owner-invite-secret",
            "role": "MEMBER",
        },
    )
    assert owner_invite.status_code == 201, owner_invite.text
    invited_email = owner_invite.json()["email"]

    with get_session_local()() as session:
        tables = set(inspect(session.get_bind()).get_table_names())
        role_id = None
        if {"tenant_roles", "tenant_memberships", "user_profiles"}.issubset(tables):
            role_id = session.execute(
                text(
                    "SELECT id FROM tenant_roles "
                    "WHERE tenant_id = :tenant_id AND slug = 'member'"
                ),
                {"tenant_id": identity_fixture["tenant_a"]},
            ).scalar_one_or_none()

    if role_id is not None:
        listed = client.get(
            f"{API}/tenants/{identity_fixture['tenant_a']}/users",
            headers=_headers(token),
            params={"query": invited_email},
        )
        assert listed.status_code == 200, listed.text
        assert [item["email"] for item in listed.json()["items"]] == [invited_email]
    else:
        with get_session_local()() as session:
            legacy_count = session.execute(
                text(
                    "SELECT COUNT(*) FROM users "
                    "WHERE tenant_id = :tenant_id AND email = :email"
                ),
                {
                    "tenant_id": identity_fixture["tenant_a"],
                    "email": invited_email,
                },
            ).scalar_one()
        assert legacy_count == 1

    with get_session_local()() as session:
        audit = session.execute(
            text(
                "SELECT metadata_redacted FROM audit_logs "
                "WHERE action = 'user.invite' AND target_id = :target_id"
            ),
            {"target_id": owner_invite.json()["id"]},
        ).scalar_one()
    assert "owner-invite-secret" not in str(audit)

    admin_invite = client.post(
        f"{API}/admin/users",
        headers=_headers(token),
        json={
            "email": f"compat-admin-{identity_fixture['tenant_a']}@example.com",
            "name": "Local Admin",
            "password": "admin-invite-secret",
            "role": "ADMIN",
        },
    )
    assert admin_invite.status_code == 201, admin_invite.text
    admin_login = client.post(
        f"{API}/auth/login",
        json={
            "email": admin_invite.json()["email"],
            "password": "admin-invite-secret",
        },
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_token = admin_login.json()["access_token"]
    admin_created = client.post(
        f"{API}/admin/users",
        headers=_headers(admin_token),
        json={
            "email": f"compat-admin-created-{identity_fixture['tenant_a']}@example.com",
            "name": "Admin Created",
            "password": "member-invite-secret",
            "role": "MEMBER",
        },
    )
    assert admin_created.status_code == 201, admin_created.text

    member_login = client.post(
        f"{API}/auth/login",
        json={
            "email": invited_email,
            "password": "owner-invite-secret",
        },
    )
    assert member_login.status_code == 200, member_login.text
    member_denied = client.post(
        f"{API}/admin/users",
        headers=_headers(member_login.json()["access_token"]),
        json={
            "email": f"denied-{identity_fixture['tenant_a']}@example.com",
            "name": "Denied",
            "password": "must-not-leak-secret",
            "role": "MEMBER",
        },
    )
    assert member_denied.status_code == 403, member_denied.text
    assert member_denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert "must-not-leak-secret" not in member_denied.text

    with get_session_local()() as session:
        tenant_b_count = session.execute(
            text(
                "SELECT COUNT(*) FROM users "
                "WHERE tenant_id = :tenant_id AND email = :email"
            ),
            {"tenant_id": identity_fixture["tenant_b"], "email": invited_email},
        ).scalar_one()
    assert tenant_b_count == 0


def test_admin_user_invite_rejects_invalid_input_without_password_echo(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    owner_token = create_access_token(
        identity_fixture["owner_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )
    response = client.post(
        f"{API}/admin/users",
        headers=_headers(owner_token),
        json={
            "email": "not-an-email",
            "name": "",
            "password": "short-secret",
            "role": "OWNER",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "short-secret" not in response.text
    with get_session_local()() as session:
        audit = session.execute(
            text(
                "SELECT metadata_redacted FROM audit_logs "
                "WHERE trace_id = :trace_id"
            ),
            {"trace_id": response.headers["X-Request-Id"]},
        ).scalar_one()
    assert "short-secret" not in str(audit)


def test_authenticated_user_list_denials_are_audited_redacted(
    client: TestClient, identity_fixture: dict[str, str]
) -> None:
    settings = get_settings()
    customer_token = create_access_token(
        identity_fixture["customer_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )
    owner_token = create_access_token(
        identity_fixture["owner_a"],
        identity_fixture["tenant_a"],
        settings,
        tenant_role="OWNER",
    )
    path = f"{API}/tenants/{identity_fixture['tenant_a']}/users"
    denied_same_tenant = client.get(
        path,
        headers=_headers(customer_token),
        params={"query": "private@example.com"},
    )
    denied_cross_tenant = client.get(
        f"{API}/tenants/{identity_fixture['tenant_b']}/users",
        headers=_headers(owner_token),
    )
    denied_bad_cursor = client.get(
        path,
        headers=_headers(owner_token),
        params={"cursor": "bad"},
    )

    for response in (denied_same_tenant, denied_cross_tenant, denied_bad_cursor):
        assert response.status_code in {400, 403}
        assert set(response.json()["error"]) == {"code", "message", "request_id", "details"}
        assert response.headers["X-Request-Id"] == response.json()["error"]["request_id"]

    trace_ids = [
        response.json()["error"]["request_id"]
        for response in (denied_same_tenant, denied_cross_tenant, denied_bad_cursor)
    ]
    with get_session_local()() as session:
        rows = session.execute(
            text(
                "SELECT result, metadata_redacted FROM audit_logs "
                "WHERE action = 'team.users.list' AND trace_id IN "
                "(:trace_one, :trace_two, :trace_three)"
            ),
            {
                "trace_one": trace_ids[0],
                "trace_two": trace_ids[1],
                "trace_three": trace_ids[2],
            },
        ).mappings().all()
    assert len(rows) == 3
    for row in rows:
        assert row["result"] == "DENIED"
        assert "private@example.com" not in str(row["metadata_redacted"])
