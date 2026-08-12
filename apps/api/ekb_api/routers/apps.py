"""Installed-apps endpoints for the workspace app center (Phase 4 · authoritative).

Legacy endpoints (``/catalog``, ``/installed``, ``/install``, ``/uninstall``)
preserve the snake_case/shape contract used by the v2 Vue adapter.  The
following additive endpoints power the Phase 4 "credentials + run ledger" UI:

* ``GET  /apps/{slug}``                — app detail (capabilities, redacted
                                        credentials, recent runs).
* ``PUT  /apps/{slug}/configure``      — patch installation config and
                                        promote status → CONFIGURED.
* ``POST /apps/{slug}/connect``        — promote → CONNECTED + write
                                        synthetic CONNECTIVITY_CHECK run.
* ``POST /apps/{slug}/credentials``    — write-once Fernet credential;
                                        returns plaintext exactly once.
* ``GET  /apps/{slug}/credentials``    — list credentials with prefix only;
                                        ciphertext/plaintext never returned.
* ``POST /apps/credentials/{cred_id}/revoke`` — mark credential REVOKED.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_READ, CAP_KB_WRITE, assert_capability
from ekb_api.domain import AuthContext
from ekb_api.services.v3_apps import (
    MAX_CATALOG,
    AppInstallResult,
    AppUninstallResult,
    configure_installation,
    connect_installation,
    create_credential,
    get_app_detail,
    install_app,
    list_catalog,
    list_installed,
    record_run,
    revoke_credential,
    uninstall_app,
)

router = APIRouter(prefix="/apps", tags=["apps"])


# ======================= Request models (new Phase 4 endpoints) =====================


class ConfigureAppRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)


class ConnectAppRequest(BaseModel):
    pass


class CreateCredentialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    secret: str = Field(min_length=1)


class RecordRunRequest(BaseModel):
    run_type: str = Field(min_length=1, max_length=64)
    status: str = Field(pattern=r"^(SUCCEEDED|FAILED)$")
    result_redacted: Dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(min_length=1, max_length=32)
    completed_at: Optional[str] = Field(default=None, max_length=32)
    sync_source_id: Optional[str] = Field(default=None, max_length=36)
    error_code: Optional[str] = Field(default=None, max_length=64)
    error_message: Optional[str] = Field(default=None)


# ======================= Legacy endpoints (unchanged contract) =====================


@router.get("/catalog")
def read_catalog(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    limit: int = Query(default=MAX_CATALOG, ge=1, le=MAX_CATALOG),
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    items = list_catalog(auth.tenant_id)[:limit]
    return {
        "items": [
            {
                "id": item.id,
                "slug": item.slug,
                "name": item.name,
                "category": item.category,
                "description": item.description,
                "icon_slug": item.icon_slug,
                "status": item.status,
                "installed_at": item.installed_at,
                "uninstalled_at": item.uninstalled_at,
                "error_message": item.error_message,
            }
            for item in items
        ],
        "total": len(items),
    }


@router.get("/installed")
def read_installed(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    items = list_installed(auth.tenant_id)
    return {
        "items": [
            {
                "id": item.id,
                "slug": item.slug,
                "name": item.name,
                "category": item.category,
                "description": item.description,
                "icon_slug": item.icon_slug,
                "status": item.status,
                "installed_at": item.installed_at,
            }
            for item in items
        ],
        "count": len(items),
    }


@router.post("/install/{slug}")
def do_install(
    slug: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    result: AppInstallResult = install_app(
        auth.tenant_id, slug, installed_by=auth.actor_id
    )
    if not result.success:
        raise HTTPException(status_code=400, detail={"code": "APP_INSTALL_FAILED", "message": result.error})
    return {
        "success": result.success,
        "app_id": result.appId,
        "slug": result.slug,
        "name": result.name,
        "status": result.status,
        "error": result.error,
    }


@router.post("/uninstall/{slug}")
def do_uninstall(
    slug: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    result: AppUninstallResult = uninstall_app(
        auth.tenant_id, slug, actor_id=auth.actor_id
    )
    if not result.success:
        raise HTTPException(status_code=400, detail={"code": "APP_UNINSTALL_FAILED", "message": result.error})
    return {
        "success": result.success,
        "app_id": result.appId,
        "slug": result.slug,
        "previous_status": result.previousStatus,
        "error": result.error,
    }


# ================== Phase 4 · detail / configure / connect ==================


def _slug_or_404(auth: AuthContext, slug: str):
    detail = get_app_detail(auth.tenant_id, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "APP_NOT_FOUND", "message": f"app {slug} not found"})
    return detail


@router.get("/{slug}")
def read_app_detail(
    slug: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """App detail view — intentionally never serialises ciphertext or plaintext."""
    assert_capability(auth, CAP_KB_READ)
    detail = _slug_or_404(auth, slug)
    return {
        "slug": detail.slug,
        "display_name": detail.display_name,
        "provider_name": detail.provider_name,
        "description": detail.description,
        "category": detail.category,
        "capabilities": detail.capabilities,
        "recommended_rank": detail.recommended_rank,
        "installation": detail.installation,
        "credentials": detail.credentials,
        "runs": detail.runs,
    }


@router.put("/{slug}/configure")
def put_configure(
    slug: str,
    payload: ConfigureAppRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    _slug_or_404(auth, slug)
    result = configure_installation(
        auth.tenant_id, slug, dict(payload.config), actor_id=auth.actor_id
    )
    if result is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "APP_NOT_INSTALLED", "message": "install app before configuring"},
        )
    return result


@router.post("/{slug}/connect")
def post_connect(
    slug: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    _slug_or_404(auth, slug)
    result = connect_installation(auth.tenant_id, slug, actor_id=auth.actor_id)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "APP_NOT_INSTALLED", "message": "install app before connecting"},
        )
    if not result.get("connected", False):
        raise HTTPException(
            status_code=400,
            detail={"code": "APP_CONNECT_FAILED", "message": result.get("error")},
        )
    return result


# ======================== Phase 4 · credentials (zero-plaintext reads) =======================


@router.post("/{slug}/credentials")
def post_credential(
    slug: str,
    payload: CreateCredentialRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Create a credential.  **Returns the plaintext exactly once.**

    The response payload intentionally carries ``plaintext_once`` alongside
    the stable ``prefix``; the UI MUST surface this to the user once and
    then discard it.  All subsequent reads via ``/credentials`` or
    ``/{slug}`` return metadata only.
    """
    assert_capability(auth, CAP_KB_WRITE)
    _slug_or_404(auth, slug)
    result = create_credential(
        auth.tenant_id, slug, payload.name, payload.secret, actor_id=auth.actor_id
    )
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail={"code": "CREDENTIAL_CREATE_FAILED", "message": result.error},
        )
    return {
        "success": True,
        "credential_id": result.credential_id,
        "name": result.credential_name,
        "prefix": result.prefix,
        "status": result.status,
        "key_version": result.key_version,
        "plaintext_once": result.plaintext_once,
    }


@router.get("/{slug}/credentials")
def list_credentials(
    slug: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """List credentials for an installation.  Zero plaintext; zero ciphertext."""
    assert_capability(auth, CAP_KB_READ)
    detail = _slug_or_404(auth, slug)
    credentials: List[Dict[str, Any]] = list(detail.credentials)
    for cred in credentials:
        # Belt-and-braces: drop any field that could ever leak keys even if
        # the service layer changes.
        for bad_key in (
            "plaintext",
            "plaintext_once",
            "ciphertext",
            "previous_ciphertext",
            "key_version",  # keep only server-internal; OK to re-add if UI
        ):
            # NOTE: key_version IS spec-visible from detail endpoint; the
            # credentials list drops it to keep the UI surface minimal.
            cred.pop(bad_key, None)
    return {
        "slug": slug,
        "count": len(credentials),
        "items": credentials,
    }


@router.post("/credentials/{credential_id}/revoke")
def post_revoke(
    credential_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    result = revoke_credential(auth.tenant_id, credential_id, actor_id=auth.actor_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CREDENTIAL_NOT_FOUND", "message": "credential does not exist"},
        )
    return {
        "success": True,
        "id": result["id"],
        "name": result["name"],
        "prefix": result["prefix"],
        "status": result["status"],
        "revoked_at": result["revoked_at"],
    }


# =========================== Phase 4 · runs ledger ===========================


@router.post("/{slug}/runs")
def post_run(
    slug: str,
    payload: RecordRunRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Append a run record.  Used by UI-triggered provider tasks or sync workers."""
    assert_capability(auth, CAP_KB_WRITE)
    _slug_or_404(auth, slug)
    run_id = record_run(
        auth.tenant_id,
        slug,
        run_type=payload.run_type,
        status=payload.status,
        result_redacted=dict(payload.result_redacted),
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        sync_source_id=payload.sync_source_id,
        error_code=payload.error_code,
        error_message=payload.error_message,
    )
    if run_id is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "RUN_RECORD_REJECTED",
                "message": "installation must be installed to record runs",
            },
        )
    return {"success": True, "run_id": run_id}
