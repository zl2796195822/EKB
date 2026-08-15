from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette import status

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import assert_team_user_manage
from ekb_api.domain import AuthContext
from ekb_api.services.llm_provider_catalog import list_preset_providers
from ekb_api.services.v3_llm import (
    LLMModel,
    LLMProvider,
    create_llm_model,
    create_llm_provider,
    delete_llm_model,
    delete_llm_provider,
    enable_llm_provider,
    get_llm_model,
    get_llm_provider,
    list_llm_models,
    list_llm_providers,
    sync_models_from_provider,
    update_llm_model,
    update_llm_provider,
)

router = APIRouter(tags=["llm"])


# ---- Provider request schemas ----


class CreateLLMProviderRequest(BaseModel):
    preset_provider_id: Optional[str] = None
    provider_key: Optional[str] = None
    name: Optional[str] = None
    logo: Optional[str] = None
    description: Optional[str] = None
    websites: Optional[dict] = None
    default_chat_endpoint: Optional[str] = None
    endpoint_configs: Optional[dict] = None
    auth_type: Optional[str] = None
    api_key: Optional[str] = None
    api_key_label: Optional[str] = None
    api_features: Optional[dict] = None
    settings: Optional[dict] = None
    model_list_source: Optional[str] = None
    is_enabled: Optional[bool] = None


class PatchLLMProviderRequest(BaseModel):
    name: Optional[str] = None
    logo: Optional[str] = None
    description: Optional[str] = None
    websites: Optional[dict] = None
    default_chat_endpoint: Optional[str] = None
    endpoint_configs: Optional[dict] = None
    auth_type: Optional[str] = None
    api_key: Optional[str] = None
    api_key_label: Optional[str] = None
    api_features: Optional[dict] = None
    settings: Optional[dict] = None
    model_list_source: Optional[str] = None
    is_enabled: Optional[bool] = None


class EnableLLMProviderRequest(BaseModel):
    enabled: bool


# ---- Model request schemas ----


class CreateLLMModelRequest(BaseModel):
    model_id: str
    display_name: str
    model_type: Optional[str] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    endpoint_type: Optional[str] = None
    capabilities: Optional[dict] = None
    input_price: Optional[str] = None
    output_price: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_custom: Optional[bool] = None
    notes: Optional[str] = None


class PatchLLMModelRequest(BaseModel):
    model_id: Optional[str] = None
    display_name: Optional[str] = None
    model_type: Optional[str] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    endpoint_type: Optional[str] = None
    capabilities: Optional[dict] = None
    input_price: Optional[str] = None
    output_price: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_custom: Optional[bool] = None
    notes: Optional[str] = None


# ---- Response helpers ----


def _provider_dict(p: LLMProvider, *, is_admin: bool = True) -> dict[str, Any]:
    d = {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "user_id": p.user_id,
        "preset_provider_id": p.preset_provider_id,
        "provider_key": p.provider_key,
        "name": p.name,
        "logo": p.logo,
        "description": p.description,
        "websites": p.websites,
        "default_chat_endpoint": p.default_chat_endpoint,
        "endpoint_configs": p.endpoint_configs,
        "auth_type": p.auth_type,
        "api_key_label": p.api_key_label,
        "api_features": p.api_features,
        "settings": p.settings,
        "model_list_source": p.model_list_source,
        "is_enabled": p.is_enabled,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "is_preset_builtin": p.is_preset_builtin,
        "auth_optional": p.auth_optional,
        "has_api_key": p.has_api_key,
    }
    # 成员（非管理员）只能看到 provider 名称与可选模型，不能看到模型服务配置
    # （endpoint / api_key 标签 / 鉴权方式 / 自定义设置）。
    if not is_admin:
        d.pop("endpoint_configs", None)
        d.pop("api_key_label", None)
        d.pop("auth_type", None)
        d.pop("default_chat_endpoint", None)
        d.pop("settings", None)
    return d


def _model_dict(m: LLMModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "tenant_id": m.tenant_id,
        "user_id": m.user_id,
        "provider_id": m.provider_id,
        "model_id": m.model_id,
        "display_name": m.display_name,
        "model_type": m.model_type,
        "context_window": m.context_window,
        "max_output_tokens": m.max_output_tokens,
        "endpoint_type": m.endpoint_type,
        "capabilities": m.capabilities,
        "input_price": m.input_price,
        "output_price": m.output_price,
        "is_enabled": m.is_enabled,
        "is_custom": m.is_custom,
        "notes": m.notes,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


# ================ Provider API ================


@router.get("/llm/providers")
def get_me_llm_providers(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    is_admin = str(auth.tenant_role) in ("OWNER", "ADMIN")
    providers = list_llm_providers(auth)
    return {"items": [_provider_dict(p, is_admin=is_admin) for p in providers]}


@router.get("/llm/providers/catalog")
def get_me_llm_providers_catalog(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    items = list_preset_providers()
    return {"items": items}


@router.get("/llm/providers/{provider_id}")
def get_me_llm_provider(
    provider_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    is_admin = str(auth.tenant_role) in ("OWNER", "ADMIN")
    provider = get_llm_provider(auth, provider_id)
    if provider is None:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "LLM Provider 不存在")
    return _provider_dict(provider, is_admin=is_admin)


@router.post("/llm/providers")
def post_me_llm_provider(
    payload: CreateLLMProviderRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    provider = create_llm_provider(auth, payload.model_dump(exclude_unset=True))
    return _provider_dict(provider)


@router.patch("/llm/providers/{provider_id}")
def patch_me_llm_provider(
    provider_id: str,
    payload: PatchLLMProviderRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    provider = update_llm_provider(auth, provider_id, payload.model_dump(exclude_unset=True))
    return _provider_dict(provider)


@router.delete("/llm/providers/{provider_id}")
def delete_me_llm_provider(
    provider_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, str]:
    assert_team_user_manage(auth)
    ok = delete_llm_provider(auth, provider_id)
    if not ok:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "LLM Provider 不存在")
    return {"status": "ok"}


@router.patch("/llm/providers/{provider_id}/enable")
def patch_me_llm_provider_enable(
    provider_id: str,
    payload: EnableLLMProviderRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    provider = enable_llm_provider(auth, provider_id, payload.enabled)
    return _provider_dict(provider)


# ================ Model API ================


@router.get("/llm/providers/{provider_id}/models")
def get_me_llm_provider_models(
    provider_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    models = list_llm_models(auth, provider_id)
    return {"items": [_model_dict(m) for m in models]}


@router.get("/llm/models/{model_id}")
def get_me_llm_model(
    model_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    model = get_llm_model(auth, model_id)
    if model is None:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "LLM Model 不存在")
    return _model_dict(model)


@router.post("/llm/providers/{provider_id}/models")
def post_me_llm_provider_model(
    provider_id: str,
    payload: CreateLLMModelRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    model = create_llm_model(auth, provider_id, payload.model_dump(exclude_unset=True))
    return _model_dict(model)


@router.patch("/llm/models/{model_id}")
def patch_me_llm_model(
    model_id: str,
    payload: PatchLLMModelRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    model = update_llm_model(auth, model_id, payload.model_dump(exclude_unset=True))
    return _model_dict(model)


@router.delete("/llm/models/{model_id}")
def delete_me_llm_model(
    model_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, str]:
    assert_team_user_manage(auth)
    ok = delete_llm_model(auth, model_id)
    if not ok:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "LLM Model 不存在")
    return {"status": "ok"}


@router.post("/llm/providers/{provider_id}/models/sync")
def post_me_llm_provider_models_sync(
    provider_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_team_user_manage(auth)
    result = sync_models_from_provider(auth, provider_id)
    return result
