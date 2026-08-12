"""Apps service backed by the authoritative v3_006_apps_compat four-table schema.

Schema sources (see 02_spec.md §5.2 / §5.4):

* ``app_catalog`` — six immutable slug rows (CHECK whitelist enforced).
* ``app_installations`` — per-tenant lifecycle (INSTALLED → CONFIGURED →
  CONNECTED → UNINSTALLED), with composite FKs to tenants, users, catalog,
  and memberships.
* ``app_credentials`` — dedicated Fernet envelope: only a short ``prefix`` is
  ever returned by the list/read APIs; ``ciphertext`` / ``previous_ciphertext``
  never leave the server.  Write-once semantics: ``create_credential``
  returns the plaintext to the caller exactly once; subsequent reads surface
  only metadata.
* ``app_runs`` — SUCCEEDED / FAILED records for downstream provider calls.

The legacy ``installed_apps`` log table created by ``v3_004_apps`` is treated
as a read-only historic projection: we never write to it from this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from ekb_api.domain import utc_now
from ekb_api.store import get_session_local

# --- Legacy dataclasses preserved for routers/adapters that already depend on
# the old single-table contract.  Newer endpoints use the richer View types
# defined at the bottom of the module.


@dataclass(frozen=True)
class AppCatalogItem:
    id: str
    slug: str
    name: str
    category: str
    description: str
    icon_slug: str
    status: str  # AVAILABLE | INSTALLED | UNINSTALLED | ERROR
    installed_at: Optional[str] = None
    uninstalled_at: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class AppInstallResult:
    success: bool
    appId: str
    slug: str
    name: str
    status: str
    error: Optional[str] = None


@dataclass(frozen=True)
class AppUninstallResult:
    success: bool
    appId: str
    slug: str
    previousStatus: str
    error: Optional[str] = None


MAX_CATALOG = 50

_VALID_INSTALLATION_STATUSES = {"INSTALLED", "CONFIGURED", "CONNECTED", "UNINSTALLED"}
# The view status merges the authoritative installation lifecycle with the
# historical AVAILABLE/ERROR statuses expected by the v2 UI adapter.
_LEGACY_STATUS_MAP = {
    None: "AVAILABLE",
    "INSTALLED": "INSTALLED",
    "CONFIGURED": "INSTALLED",
    "CONNECTED": "INSTALLED",
    "UNINSTALLED": "UNINSTALLED",
}

# Stable icon-slug mapping per authoritative catalog entry (mapped in
# service; the new catalog stores semantic metadata instead of icon glyphs).
_SLUG_ICON = {
    "feishu": "chat-circle-dots",
    "wecom": "chat-circle-dots",
    "github": "file-text",
    "tencent-docs": "file-text",
    "analytics-pro": "chart-line",
    "audit": "shield-check",
}


# === Fernet master key derivation =========================================
#
# The credential envelope MUST have a dedicated master key — never shared
# with signing, session, or backup keys.  Operators supply
# EKB_APPS_MASTER_KEY; the default is explicitly non-secret so local/dev
# smoke can run but the warning is loud in tests.

_DEFAULT_MASTER_KEY_MATERIAL = "apps-dev-placeholder-key-do-not-use-in-production"
_KEY_VERSION = "ekb-apps-kms-v1"
_PREFIX_LENGTH = 8


def _master_key_material() -> bytes:
    value = os.environ.get("EKB_APPS_MASTER_KEY") or _DEFAULT_MASTER_KEY_MATERIAL
    return value.encode("utf-8")


def _fernet_key() -> bytes:
    """Derive a 32-byte Fernet key, URL-safe base64 encoded as required."""
    digest = hashlib.sha256(_master_key_material()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    from cryptography.fernet import Fernet, MultiFernet

    # A single key suffices now; MultiFernet is pre-wired so key rotation can
    # grow to (current, previous) without schema migrations — the
    # previous_ciphertext/previous_key_version/previous_expires_at columns
    # already carry the rotated envelope on credential rows.
    return MultiFernet([Fernet(_fernet_key())])


def _prefix_for(plaintext_secret: str) -> str:
    clean = (plaintext_secret or "").strip()
    if not clean:
        return ""
    if len(clean) <= _PREFIX_LENGTH:
        return clean
    return clean[:_PREFIX_LENGTH]


# -------- SQL helpers: upsert dialect branching for installations ----------


def _upsert_installation(
    session,
    *,
    installation_id: str,
    tenant_id: str,
    app_slug: str,
    status: str,
    config_json: str,
    installed_by: str,
    installed_at: str,
    updated_at: str,
    uninstalled_at: Optional[str],
) -> None:
    params = {
        "id": installation_id,
        "tenant": tenant_id,
        "slug": app_slug,
        "status": status,
        "config": config_json,
        "installed_by": installed_by,
        "installed_at": installed_at,
        "updated_at": updated_at,
        "uninstalled_at": uninstalled_at,
    }
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    if dialect == "postgresql":
        session.execute(
            text(
                """
                INSERT INTO app_installations (
                  id, tenant_id, app_slug, status, config, installed_by,
                  installed_at, updated_at, uninstalled_at
                ) VALUES (
                  :id, :tenant, :slug, :status, :config::JSONB, :installed_by,
                  :installed_at, :updated_at, :uninstalled_at
                ) ON CONFLICT (tenant_id, app_slug) DO UPDATE SET
                  status = EXCLUDED.status,
                  config = EXCLUDED.config,
                  updated_at = EXCLUDED.updated_at,
                  uninstalled_at = EXCLUDED.uninstalled_at
                """
            ),
            params,
        )
    else:
        # SQLite route: insert-or-replace only the non-FK mutable columns.
        session.execute(
            text(
                """
                INSERT OR REPLACE INTO app_installations (
                  id, tenant_id, app_slug, status, config, installed_by,
                  installed_at, updated_at, uninstalled_at
                ) VALUES (
                  COALESCE((SELECT id FROM app_installations WHERE tenant_id=:tenant AND app_slug=:slug), :id),
                  :tenant, :slug, :status, :config, :installed_by,
                  :installed_at, :updated_at, :uninstalled_at
                )
                """
            ),
            params,
        )


# ===========================================================================
# Legacy public API (read from new tables, match old contract)
# ===========================================================================


def _row_to_catalog_item(catalog_row, installation_row) -> AppCatalogItem:
    slug = str(catalog_row.slug)
    if installation_row is None:
        status = "AVAILABLE"
        installed_at = None
        uninstalled_at = None
        error_message = None
    else:
        raw_status = str(installation_row.status)
        status = _LEGACY_STATUS_MAP.get(raw_status, "ERROR")
        installed_at = installation_row.installed_at
        uninstalled_at = installation_row.uninstalled_at
        error_message = None
    return AppCatalogItem(
        id=str(installation_row.id) if installation_row else f"catalog:{slug}",
        slug=slug,
        name=str(catalog_row.display_name),
        category=str(catalog_row.category),
        description=str(catalog_row.description),
        icon_slug=_SLUG_ICON.get(slug, "plug"),
        status=status,
        installed_at=installed_at,
        uninstalled_at=uninstalled_at,
        error_message=error_message,
    )


def list_catalog(tenant_id: str) -> List[AppCatalogItem]:
    """Return the authoritative six-entry catalog joined with tenant state."""
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT c.slug, c.display_name, c.category, c.description, c.enabled,
                       i.id, i.status, i.installed_at, i.uninstalled_at
                FROM app_catalog c
                LEFT JOIN app_installations i
                       ON i.app_slug = c.slug AND i.tenant_id = :tenant
                WHERE c.enabled = 1
                ORDER BY c.recommended_rank ASC, c.display_name ASC
                """
            ),
            {"tenant": tenant_id},
        ).all()

        class _CatalogRow:
            def __init__(self, r):
                self.slug = r[0]; self.display_name = r[1]; self.category = r[2]
                self.description = r[3]; self.enabled = r[4]

        class _InstallationRow:
            def __init__(self, r):
                self.id = r[5]; self.status = r[6]; self.installed_at = r[7]; self.uninstalled_at = r[8]

        items: list[AppCatalogItem] = []
        for row in rows:
            catalog = _CatalogRow(row)
            installation = None if row[5] is None else _InstallationRow(row)
            items.append(_row_to_catalog_item(catalog, installation))
        return items


def list_installed(tenant_id: str) -> List[AppCatalogItem]:
    """Return only currently-installed apps (status in INSTALLED/CONFIGURED/CONNECTED)."""
    return [
        item for item in list_catalog(tenant_id)
        if item.status == "INSTALLED"
    ]


def install_app(
    tenant_id: str,
    slug: str,
    *,
    installed_by: Optional[str] = None,
) -> AppInstallResult:
    """Install an app for the tenant.  Idempotent against the authoritative catalog.

    The ``installed_by`` user is validated to be a valid member of the tenant
    via the composite FK on ``app_installations``; callers pass the live
    auth.actor_id so the ledger remains auditable.
    """
    if not slug or not slug.strip():
        return AppInstallResult(False, "", slug, "", "ERROR", error="APP_SLUG_REQUIRED")
    if not installed_by:
        installed_by = tenant_id
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        # Catalog existence + enabled
        catalog = session.execute(
            text("SELECT slug, display_name FROM app_catalog WHERE slug = :slug AND enabled = 1"),
            {"slug": slug},
        ).first()
        if not catalog:
            return AppInstallResult(
                False, "", slug, slug, "ERROR", error=f"App {slug} not in authoritative catalog"
            )
        display_name = str(catalog[1])
        # Existing installation (any status) → use its id and upgrade status.
        existing = session.execute(
            text(
                """
                SELECT id, status FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        installation_id = str(existing[0]) if existing else str(_uuid.uuid4())
        # If already in one of the installed states, no-op success.
        if existing and str(existing[1]) in {"INSTALLED", "CONFIGURED", "CONNECTED"}:
            return AppInstallResult(True, installation_id, slug, display_name, "INSTALLED")
        _upsert_installation(
            session,
            installation_id=installation_id,
            tenant_id=tenant_id,
            app_slug=slug,
            status="INSTALLED",
            config_json="{}",
            installed_by=installed_by,
            installed_at=now,
            updated_at=now,
            uninstalled_at=None,
        )
        session.commit()
    return AppInstallResult(True, installation_id, slug, display_name, "INSTALLED")


def uninstall_app(
    tenant_id: str,
    slug: str,
    *,
    actor_id: Optional[str] = None,
) -> AppUninstallResult:
    """Mark an installation as UNINSTALLED.  Revokes ACTIVE credentials atomically."""
    if not slug or not slug.strip():
        return AppUninstallResult(False, "", slug, "NOT_FOUND", error="APP_SLUG_REQUIRED")
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                """
                SELECT id, status FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        if not existing:
            return AppUninstallResult(
                False, "", slug, "NOT_FOUND", error=f"App {slug} not installed"
            )
        installation_id = str(existing[0])
        previous = str(existing[1])
        if previous == "UNINSTALLED":
            return AppUninstallResult(
                False, installation_id, slug, previous, error=f"App {slug} already uninstalled"
            )
        # Atomically revoke active credentials + flip status.
        session.execute(
            text(
                """
                UPDATE app_credentials SET
                  status = 'REVOKED', revoked_at = :now, updated_at = :now
                WHERE tenant_id = :tenant AND installation_id = :iid AND status = 'ACTIVE'
                """
            ),
            {"now": now, "tenant": tenant_id, "iid": installation_id},
        )
        session.execute(
            text(
                """
                UPDATE app_installations SET
                  status = 'UNINSTALLED', uninstalled_at = :now, updated_at = :now
                WHERE id = :iid AND tenant_id = :tenant
                """
            ),
            {"now": now, "iid": installation_id, "tenant": tenant_id},
        )
        session.commit()
    return AppUninstallResult(True, installation_id, slug, previous)


# ===========================================================================
# New authoritative Phase 4 APIs (installations, credentials, runs)
# ===========================================================================


@dataclass(frozen=True)
class AppDetailView:
    slug: str
    display_name: str
    provider_name: str
    description: str
    category: str
    capabilities: List[str]
    recommended_rank: int
    enabled: bool
    installation: Optional[Dict[str, Any]] = None
    credentials: List[Dict[str, Any]] = field(default_factory=list)
    runs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CredentialCreateResult:
    success: bool
    credential_id: str
    credential_name: str
    prefix: str
    status: str
    key_version: str
    # IMPORTANT: this field is populated ONLY on create.  All list/detail APIs
    # return None for plaintext.  Callers (routers, adapters, UIs) must not
    # log, audit, or persist this value.
    plaintext_once: Optional[str] = None
    error: Optional[str] = None


def get_app_detail(tenant_id: str, slug: str) -> Optional[AppDetailView]:
    """Catalog row + installation summary + redacted credentials + recent runs.

    Ciphertext is deliberately excluded even though it's encrypted — the
    client never needs to handle opaque blobs, and keeping them off the wire
    avoids accidental leakage.
    """
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        catalog = session.execute(
            text(
                """
                SELECT slug, display_name, provider_name, description, category,
                       capabilities, recommended_rank, enabled
                FROM app_catalog WHERE slug = :slug AND enabled = 1
                """
            ),
            {"slug": slug},
        ).first()
        if not catalog:
            return None
        capabilities_raw = catalog[5]
        if isinstance(capabilities_raw, str):
            try:
                capabilities = list(json.loads(capabilities_raw))
            except Exception:
                capabilities = []
        elif hasattr(capabilities_raw, "__iter__"):
            capabilities = list(capabilities_raw)
        else:
            capabilities = []
        installation_row = session.execute(
            text(
                """
                SELECT id, status, config, installed_by, installed_at, updated_at, uninstalled_at
                FROM app_installations WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        installation = None
        credentials: list[dict] = []
        runs: list[dict] = []
        if installation_row:
            iid = str(installation_row[0])
            config_raw = installation_row[2]
            if isinstance(config_raw, str):
                try:
                    config = json.loads(config_raw)
                except Exception:
                    config = {}
            else:
                config = dict(config_raw) if config_raw else {}
            installation = {
                "id": iid,
                "status": str(installation_row[1]),
                "config": config,
                "installed_by": installation_row[3],
                "installed_at": installation_row[4],
                "updated_at": installation_row[5],
                "uninstalled_at": installation_row[6],
            }
            cred_rows = session.execute(
                text(
                    """
                    SELECT id, credential_name, prefix, key_version, status, created_at, updated_at, revoked_at
                    FROM app_credentials
                    WHERE tenant_id = :tenant AND installation_id = :iid
                    ORDER BY created_at DESC
                    """
                ),
                {"tenant": tenant_id, "iid": iid},
            ).all()
            credentials = [
                {
                    "id": str(r[0]),
                    "name": str(r[1]),
                    "prefix": str(r[2]),
                    "key_version": str(r[3]),
                    "status": str(r[4]),
                    "created_at": r[5],
                    "updated_at": r[6],
                    "revoked_at": r[7],
                }
                for r in cred_rows
            ]
            run_rows = session.execute(
                text(
                    """
                    SELECT id, run_type, status, sync_source_id, result_redacted,
                           started_at, completed_at, error_code, error_message
                    FROM app_runs
                    WHERE tenant_id = :tenant AND installation_id = :iid
                    ORDER BY started_at DESC
                    LIMIT 50
                    """
                ),
                {"tenant": tenant_id, "iid": iid},
            ).all()
            for r in run_rows:
                res_raw = r[4]
                if isinstance(res_raw, str):
                    try:
                        result_redacted = json.loads(res_raw)
                    except Exception:
                        result_redacted = {}
                else:
                    result_redacted = dict(res_raw) if res_raw else {}
                runs.append(
                    {
                        "id": str(r[0]),
                        "run_type": str(r[1]),
                        "status": str(r[2]),
                        "sync_source_id": r[3],
                        "result_redacted": result_redacted,
                        "started_at": r[5],
                        "completed_at": r[6],
                        "error_code": r[7],
                        "error_message": r[8],
                    }
                )
        return AppDetailView(
            slug=str(catalog[0]),
            display_name=str(catalog[1]),
            provider_name=str(catalog[2]),
            description=str(catalog[3]),
            category=str(catalog[4]),
            capabilities=capabilities,
            recommended_rank=int(catalog[6] or 0),
            enabled=bool(catalog[7] if isinstance(catalog[7], int) else catalog[7]),
            installation=installation,
            credentials=credentials,
            runs=runs,
        )


def configure_installation(
    tenant_id: str,
    slug: str,
    config: Dict[str, Any],
    *,
    actor_id: Optional[str] = None,
) -> Optional[dict]:
    """Patch installation config and promote status → CONFIGURED.

    Returns the updated installation dict (id/status/config/updated_at) or
    None when the slug has no installation row.
    """
    if config is None:
        config = {}
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                """
                SELECT id, status, config FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        if not existing:
            return None
        iid = str(existing[0])
        previous_status = str(existing[1])
        previous_config_raw = existing[2]
        if isinstance(previous_config_raw, str):
            try:
                previous_config = json.loads(previous_config_raw)
            except Exception:
                previous_config = {}
        else:
            previous_config = dict(previous_config_raw) if previous_config_raw else {}
        merged_config = {**previous_config, **config}
        new_status = (
            previous_status
            if previous_status in {"CONNECTED", "UNINSTALLED"}
            else "CONFIGURED"
        )
        session.execute(
            text(
                """
                UPDATE app_installations SET
                  config = :config, status = :status, updated_at = :now
                WHERE id = :iid AND tenant_id = :tenant
                """
            ),
            {
                "config": json.dumps(merged_config, ensure_ascii=False),
                "status": new_status,
                "now": now,
                "iid": iid,
                "tenant": tenant_id,
            },
        )
        session.commit()
    return {
        "id": iid,
        "slug": slug,
        "status": new_status,
        "config": merged_config,
        "updated_at": now,
    }


def connect_installation(
    tenant_id: str,
    slug: str,
    *,
    actor_id: Optional[str] = None,
) -> Optional[dict]:
    """Mark installation CONNECTED and record a synthetic run.  The run row is
    created synchronously so a failing connection-check call can write its
    error_message atomically instead of silently.
    """
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                """
                SELECT id, status FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        if not existing:
            return None
        iid = str(existing[0])
        previous = str(existing[1])
        if previous == "UNINSTALLED":
            return {
                "id": iid, "slug": slug, "status": previous, "connected": False,
                "error": "installation is UNINSTALLED; install before connecting",
            }
        if previous == "INSTALLED":
            return {
                "id": iid, "slug": slug, "status": previous, "connected": False,
                "error": "installation must be CONFIGURED before CONNECTED",
            }
        session.execute(
            text(
                """
                UPDATE app_installations SET status = 'CONNECTED', updated_at = :now
                WHERE id = :iid AND tenant_id = :tenant
                """
            ),
            {"now": now, "iid": iid, "tenant": tenant_id},
        )
        run_id = str(_uuid.uuid4())
        result_json = json.dumps(
            {"step": "connect", "actor": actor_id, "connected_at": now},
            ensure_ascii=False,
        )
        session.execute(
            text(
                """
                INSERT INTO app_runs (
                  id, tenant_id, installation_id, run_type, status,
                  result_redacted, started_at, completed_at
                ) VALUES (
                  :id, :tenant, :iid, 'CONNECTIVITY_CHECK', 'SUCCEEDED',
                  :result, :now, :now
                )
                """
            ),
            {
                "id": run_id,
                "tenant": tenant_id,
                "iid": iid,
                "result": result_json,
                "now": now,
            },
        )
        session.commit()
    return {"id": iid, "slug": slug, "status": "CONNECTED", "connected": True}


def create_credential(
    tenant_id: str,
    slug: str,
    credential_name: str,
    plaintext_secret: str,
    *,
    actor_id: Optional[str] = None,
) -> CredentialCreateResult:
    """Encrypt and persist a credential.  Returns the plaintext exactly once.

    The composite UNIQUE (tenant_id, installation_id, credential_name) makes
    duplicate names deterministic — callers see an error instead of silent
    overwrites because that would silently rotate an active secret.
    """
    name = (credential_name or "").strip()
    if not name:
        return CredentialCreateResult(False, "", "", "", "ERROR", "", error="CREDENTIAL_NAME_REQUIRED")
    secret = plaintext_secret or ""
    if not secret.strip():
        return CredentialCreateResult(False, "", name, "", "ERROR", "", error="SECRET_REQUIRED")
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing_inst = session.execute(
            text(
                """
                SELECT id, status FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        if not existing_inst:
            return CredentialCreateResult(
                False, "", name, "", "ERROR", "",
                error=f"installation missing for slug {slug}",
            )
        iid = str(existing_inst[0])
        if str(existing_inst[1]) == "UNINSTALLED":
            return CredentialCreateResult(
                False, "", name, "", "ERROR", "",
                error="installation must be installed to create credentials",
            )
        # Duplicate name guard: the SQL UNIQUE covers persistence, but preflighting
        # the error keeps the Fernet key from doing unnecessary work.
        duplicate = session.execute(
            text(
                """
                SELECT 1 FROM app_credentials
                WHERE tenant_id = :tenant AND installation_id = :iid AND credential_name = :name
                """
            ),
            {"tenant": tenant_id, "iid": iid, "name": name},
        ).first()
        if duplicate:
            return CredentialCreateResult(
                False, "", name, "", "ERROR", "",
                error=f"credential name {name} already exists for this installation",
            )
        now = utc_now()
        fernet = _fernet()
        ciphertext = fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
        prefix = _prefix_for(secret)
        credential_id = str(_uuid.uuid4())
        session.execute(
            text(
                """
                INSERT INTO app_credentials (
                  id, tenant_id, installation_id, credential_name, prefix,
                  ciphertext, key_version, status, created_at, updated_at
                ) VALUES (
                  :id, :tenant, :iid, :name, :prefix, :ciphertext, :kver,
                  'ACTIVE', :now, :now
                )
                """
            ),
            {
                "id": credential_id,
                "tenant": tenant_id,
                "iid": iid,
                "name": name,
                "prefix": prefix,
                "ciphertext": ciphertext,
                "kver": _KEY_VERSION,
                "now": now,
            },
        )
        session.commit()
    return CredentialCreateResult(
        success=True,
        credential_id=credential_id,
        credential_name=name,
        prefix=prefix,
        status="ACTIVE",
        key_version=_KEY_VERSION,
        plaintext_once=plaintext_secret,
    )


def revoke_credential(
    tenant_id: str,
    credential_id: str,
    *,
    actor_id: Optional[str] = None,
) -> Optional[dict]:
    """Mark a credential REVOKED and record revocation timestamp.

    Returns the updated credential metadata (prefix + status) or None when
    the id does not belong to the tenant.
    """
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                """
                SELECT id, credential_name, prefix, status
                FROM app_credentials
                WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": credential_id, "tenant": tenant_id},
        ).first()
        if not existing:
            return None
        session.execute(
            text(
                """
                UPDATE app_credentials SET
                  status = 'REVOKED', revoked_at = :now, updated_at = :now
                WHERE id = :id AND tenant_id = :tenant AND status != 'REVOKED'
                """
            ),
            {"now": now, "id": credential_id, "tenant": tenant_id},
        )
        session.commit()
    return {
        "id": credential_id,
        "name": str(existing[1]),
        "prefix": str(existing[2]),
        "status": "REVOKED",
        "revoked_at": now,
    }


def record_run(
    tenant_id: str,
    slug: str,
    *,
    run_type: str,
    status: str,
    result_redacted: Dict[str, Any],
    started_at: str,
    completed_at: Optional[str] = None,
    sync_source_id: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[str]:
    """Append a provider run record.  Used by routers or background sync workers.

    Returns the created run id or None when no matching installed installation.
    """
    if status not in {"SUCCEEDED", "FAILED"}:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        installation = session.execute(
            text(
                """
                SELECT id FROM app_installations
                WHERE tenant_id = :tenant AND app_slug = :slug AND status != 'UNINSTALLED'
                """
            ),
            {"tenant": tenant_id, "slug": slug},
        ).first()
        if not installation:
            return None
        run_id = str(_uuid.uuid4())
        session.execute(
            text(
                """
                INSERT INTO app_runs (
                  id, tenant_id, installation_id, run_type, status, sync_source_id,
                  result_redacted, started_at, completed_at, error_code, error_message
                ) VALUES (
                  :id, :tenant, :iid, :run_type, :status, :sync_source_id,
                  :result, :started_at, :completed_at, :error_code, :error_message
                )
                """
            ),
            {
                "id": run_id,
                "tenant": tenant_id,
                "iid": str(installation[0]),
                "run_type": run_type,
                "status": status,
                "sync_source_id": sync_source_id,
                "result": json.dumps(result_redacted or {}, ensure_ascii=False),
                "started_at": started_at,
                "completed_at": completed_at,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        session.commit()
    return run_id
