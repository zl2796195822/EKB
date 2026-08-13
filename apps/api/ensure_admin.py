#!/usr/bin/env python3
"""Ensure admin/admin account exists with the right password hash.

Run as `su ekb -c "/opt/ekb/api/venv/bin/python /opt/ekb/api/current/ensure_admin.py"`
after deploying code to a release + env + venv.

Idempotent:
  * If users table has no row with email='admin': create tenant + user (OWNER)
    with password_hash = hash_password('admin').
  * If a row with email='admin' already exists: UPDATE password_hash = hash_password('admin'),
    updated_at = now (reset password back to the deploy-agreed 'admin' so every release
    we guarantee the credential contract for manual-acceptance testing).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

# Make sure current release is on the Python path (in case script placed elsewhere)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sqlalchemy import create_engine, text  # noqa: E402
from ekb_api.core.config import get_settings  # noqa: E402
from ekb_api.core.security import hash_password  # noqa: E402


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url, future=True)
    ADMIN_EMAIL = getattr(settings, "dev_user_email", "admin") or "admin"
    ADMIN_NAME  = getattr(settings, "dev_user_name", "管理员") or "管理员"
    ADMIN_PASS  = getattr(settings, "dev_password", "admin") or "admin"

    new_hash = hash_password(ADMIN_PASS)
    now = _now_iso()

    with engine.begin() as conn:
        # Check existing admin user
        row = conn.execute(
            text("SELECT id, tenant_id FROM users WHERE email = :e LIMIT 1"),
            {"e": ADMIN_EMAIL},
        ).fetchone()

        if row is None:
            # --- Fresh seed: tenant + user + role + membership ---
            t_id = _uuid()
            u_id = _uuid()
            r_id = _uuid()
            m_id = _uuid()

            print(f"[ensure_admin] seed new tenant/user role=OWNER email={ADMIN_EMAIL!r}")

            conn.execute(
                text(
                    """
                    INSERT INTO tenants (id,name,role,policy_version,model_routing_key,
                                         egress_policy,quota_daily_qa,quota_storage_docs,
                                         quota_storage_bytes_per_file,created_at,updated_at)
                    VALUES (:i,:n,'ADMIN',1,'default','ALLOW',1000,100000,52428800,:t,:t)
                    """
                ),
                {"i": t_id, "n": f"{ADMIN_NAME} 工作区", "t": now},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO users (id,name,email,password_hash,tenant_id,role,created_at,updated_at)
                    VALUES (:i,:n,:e,:h,:t_id,'OWNER',:t,:t)
                    """
                ),
                {"i": u_id, "n": ADMIN_NAME, "e": ADMIN_EMAIL, "h": new_hash, "t_id": t_id, "t": now},
            )
            # Built-in owner role (if not already present — otherwise IGNORE)
            dialect = engine.dialect.name
            if dialect == "postgresql":
                ins_role = """
                    INSERT INTO tenant_roles (id,tenant_id,slug,display_name,is_builtin,
                                              is_system_protected,created_at,updated_at)
                    VALUES (:r,:t,'owner','Owner',1,0,:t2,:t2)
                    ON CONFLICT DO NOTHING
                """
            else:
                ins_role = """
                    INSERT OR IGNORE INTO tenant_roles
                        (id,tenant_id,slug,display_name,is_builtin,
                         is_system_protected,created_at,updated_at)
                    VALUES (:r,:t,'owner','Owner',1,0,:t2,:t2)
                """
            conn.execute(text(ins_role), {"r": r_id, "t": t_id, "t2": now})

            if dialect == "postgresql":
                ins_mbr = """
                    INSERT INTO tenant_memberships
                        (id,tenant_id,user_id,role_id,status,joined_at,created_at,updated_at)
                    VALUES (:m,:t,:u,:r,'ACTIVE',:t2,:t2,:t2)
                    ON CONFLICT DO NOTHING
                """
            else:
                ins_mbr = """
                    INSERT OR IGNORE INTO tenant_memberships
                        (id,tenant_id,user_id,role_id,status,joined_at,created_at,updated_at)
                    VALUES (:m,:t,:u,:r,'ACTIVE',:t2,:t2,:t2)
                """
            conn.execute(text(ins_mbr), {
                "m": m_id, "t": t_id, "u": u_id, "r": r_id, "t2": now,
            })
            print(f"[ensure_admin] done (seeded). tenant={t_id[:8]}.. user={u_id[:8]}..")
            return 0
        else:
            u_id, t_id = row.id, row.tenant_id
            print(f"[ensure_admin] user email={ADMIN_EMAIL!r} exists; resetting password_hash.")
            conn.execute(
                text("UPDATE users SET password_hash = :h, updated_at = :t WHERE id = :u"),
                {"h": new_hash, "t": now, "u": u_id},
            )
            print(f"[ensure_admin] done (password reset to '{ADMIN_PASS}').")
            return 0


if __name__ == "__main__":
    sys.exit(main())
