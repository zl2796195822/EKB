from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import text
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, new_id, utc_now
from ekb_api.services import secrets as provider_secrets
from ekb_api.services.llm_provider_catalog import (
    get_preset_provider,
    is_local_provider_key,
    list_preset_providers,
)


def _permission_denied() -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "当前账号无权执行该操作")


def _not_found(entity: str = "资源") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", f"{entity}不存在")


def _remote_provider_required() -> ApiError:
    return ApiError(
        status.HTTP_400_BAD_REQUEST,
        "REMOTE_PROVIDER_REQUIRED",
        "仅支持非回环的远程 HTTP(S) LLM/Embedding Provider",
    )


def _validate_remote_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _remote_provider_required()
    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").rstrip(".").lower()
    except ValueError as exc:
        raise _remote_provider_required() from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise _remote_provider_required()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise _remote_provider_required()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    mapped_address = getattr(address, "ipv4_mapped", None)
    if address is not None and (
        address.is_loopback
        or address.is_unspecified
        or bool(mapped_address and mapped_address.is_loopback)
    ):
        raise _remote_provider_required()
    return candidate


def _validate_remote_endpoint_configs(endpoint_configs: Any) -> dict:
    """Validate every configured Provider/model-list URL before persistence or use."""
    if not isinstance(endpoint_configs, dict) or not endpoint_configs:
        raise _remote_provider_required()

    found_url = False
    for config in endpoint_configs.values():
        if not isinstance(config, dict):
            raise _remote_provider_required()

        for key in ("baseUrl", "base_url"):
            if key in config:
                _validate_remote_url(config[key])
                found_url = True

        for key in ("modelsApiUrls", "models_api_urls"):
            if key not in config:
                continue
            models_urls = config[key]
            if not isinstance(models_urls, dict) or not models_urls:
                raise _remote_provider_required()
            for value in models_urls.values():
                _validate_remote_url(value)
                found_url = True

    if not found_url:
        raise _remote_provider_required()
    return endpoint_configs


def _provider_row_is_remote(provider_row: Any) -> bool:
    if is_local_provider_key(provider_row.get("provider_key")):
        return False
    try:
        endpoint_configs = _safe_json_load(provider_row.get("endpoint_configs"))
        _validate_remote_endpoint_configs(endpoint_configs)
    except ApiError:
        return False
    return True


# ---- Domain dataclasses ----


@dataclass
class LLMProvider:
    id: str
    tenant_id: str
    user_id: str
    preset_provider_id: str | None
    provider_key: str
    name: str
    logo: str | None
    description: str | None
    websites: dict
    default_chat_endpoint: str | None
    endpoint_configs: dict
    auth_type: str
    api_key_label: str | None
    api_features: dict
    settings: dict
    model_list_source: str
    is_enabled: bool
    created_at: str
    updated_at: str
    is_preset_builtin: bool = False
    auth_optional: bool = False
    has_api_key: bool = False


@dataclass
class LLMModel:
    id: str
    tenant_id: str
    user_id: str
    provider_id: str
    model_id: str
    display_name: str
    model_type: str
    context_window: int | None
    max_output_tokens: int | None
    endpoint_type: str | None
    capabilities: dict
    input_price: str | None
    output_price: str | None
    is_enabled: bool
    is_custom: bool
    notes: str | None
    created_at: str
    updated_at: str


# ---- JSON helpers ----


def _safe_json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _provider_row_to_obj(row: Any, *, is_preset_builtin: bool = False,
                         auth_optional: bool = False) -> LLMProvider:
    # API responses expose only the presence of an active credential.  Never
    # read, decrypt, or infer it from the legacy llm_providers.api_key column.
    credential_id = row["credential_id"] if "credential_id" in row.keys() else None
    return LLMProvider(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        preset_provider_id=str(row["preset_provider_id"]) if row["preset_provider_id"] else None,
        provider_key=str(row["provider_key"]),
        name=str(row["name"]),
        logo=str(row["logo"]) if row["logo"] else None,
        description=str(row["description"]) if row["description"] else None,
        websites=_safe_json_load(row["websites"]) or {},
        default_chat_endpoint=str(row["default_chat_endpoint"]) if row["default_chat_endpoint"] else None,
        endpoint_configs=_safe_json_load(row["endpoint_configs"]) or {},
        auth_type=str(row["auth_type"]),
        api_key_label=str(row["api_key_label"]) if row["api_key_label"] else None,
        api_features=_safe_json_load(row["api_features"]) or {},
        settings=_safe_json_load(row["settings"]) or {},
        model_list_source=str(row["model_list_source"]),
        is_enabled=bool(row["is_enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        is_preset_builtin=is_preset_builtin,
        auth_optional=auth_optional,
        has_api_key=bool(credential_id),
    )


def _secret_error(exc: Exception) -> ApiError:
    if isinstance(exc, provider_secrets.SecretKeyUnavailable):
        return ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PROVIDER_CREDENTIALS_UNAVAILABLE",
            "Provider 凭据加密服务未配置，暂时无法保存或使用远程模型",
        )
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "PROVIDER_CREDENTIALS_UNAVAILABLE",
        "Provider 凭据不可用，请重新配置远程 Provider",
    )


def _upsert_provider_secret(session, auth: AuthContext, provider_id: str, plaintext: str) -> None:
    """Persist a remote Provider secret only in provider_credentials.

    Admin (OWNER/ADMIN) credentials are stored with ownership_scope='TEAM' so
    every tenant member can use the model service; non-admin writes (if ever
    allowed) fall back to PERSONAL.
    """
    if not plaintext:
        _revoke_provider_secret(session, auth, provider_id)
        return
    try:
        envelope = provider_secrets.encrypt(plaintext)
    except Exception as exc:  # normalize crypto/config errors at the API boundary
        raise _secret_error(exc) from exc

    is_admin = str(auth.tenant_role) in ("OWNER", "ADMIN")
    scope = "TEAM" if is_admin else "PERSONAL"
    ownership_key = "TEAM" if is_admin else f"USER:{auth.actor_id}"
    owner_user = None if is_admin else auth.actor_id

    current = session.execute(
        text(
            "SELECT credential_id FROM llm_providers "
            "WHERE id=:id AND tenant_id=:tenant"
        ),
        {"id": provider_id, "tenant": auth.tenant_id},
    ).scalar()
    now = utc_now()
    if current:
        updated = session.execute(
            text(
                "UPDATE provider_credentials SET ciphertext=:ciphertext, "
                "key_version=:key_version, secret_last4=:last4, status='ACTIVE', "
                "rotated_at=:rotated_at "
                "WHERE id=:id AND tenant_id=:tenant AND ownership_scope=:scope "
                "AND ownership_key=:ownership_key"
            ),
            {
                "id": str(current),
                "tenant": auth.tenant_id,
                "scope": scope,
                "ownership_key": ownership_key,
                "ciphertext": envelope.ciphertext,
                "key_version": envelope.key_version,
                "last4": envelope.secret_last4,
                "rotated_at": now,
            },
        ).rowcount
        if not updated:
            raise ApiError(status.HTTP_409_CONFLICT, "PROVIDER_CREDENTIAL_CONFLICT", "Provider 凭据归属校验失败")
    else:
        credential_id = new_id()
        session.execute(
            text(
                "INSERT INTO provider_credentials "
                "(id, tenant_id, owner_user_id, ownership_scope, ownership_key, "
                "ciphertext, key_version, secret_last4, status, created_at) "
                "VALUES (:id,:tenant,:user,:scope,:ownership_key,:ciphertext,"
                ":key_version,:last4,'ACTIVE',:created_at)"
            ),
            {
                "id": credential_id,
                "tenant": auth.tenant_id,
                "user": owner_user,
                "scope": scope,
                "ownership_key": ownership_key,
                "ciphertext": envelope.ciphertext,
                "key_version": envelope.key_version,
                "last4": envelope.secret_last4,
                "created_at": now,
            },
        )
        session.execute(
            text(
                "UPDATE llm_providers SET credential_id=:credential_id, "
                "ownership_scope=:scope, ownership_key=:ownership_key "
                "WHERE id=:id AND tenant_id=:tenant"
            ),
            {
                "credential_id": credential_id,
                "scope": scope,
                "ownership_key": ownership_key,
                "id": provider_id,
                "tenant": auth.tenant_id,
            },
        )

    # Remove any legacy value when the provider is next saved. Runtime never
    # reads this column, but clearing it prevents stale plaintext retention.
    session.execute(
        text("UPDATE llm_providers SET api_key=NULL WHERE id=:id AND tenant_id=:tenant"),
        {"id": provider_id, "tenant": auth.tenant_id},
    )


def _revoke_provider_secret(session, auth: AuthContext, provider_id: str) -> None:
    current = session.execute(
        text(
            "SELECT credential_id FROM llm_providers "
            "WHERE id=:id AND tenant_id=:tenant"
        ),
        {"id": provider_id, "tenant": auth.tenant_id},
    ).scalar()
    if current:
        session.execute(
            text(
                "UPDATE provider_credentials SET status='REVOKED', rotated_at=:now "
                "WHERE id=:id AND tenant_id=:tenant"
            ),
            {"id": str(current), "tenant": auth.tenant_id, "now": utc_now()},
        )
    session.execute(
        text(
            "UPDATE llm_providers SET credential_id=NULL, api_key=NULL "
            "WHERE id=:id AND tenant_id=:tenant"
        ),
        {"id": provider_id, "tenant": auth.tenant_id},
    )


def _read_provider_secret(session, auth: AuthContext, provider_row: Any) -> str:
    credential_id = provider_row.get("credential_id") if hasattr(provider_row, "get") else provider_row["credential_id"]
    if not credential_id:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "PROVIDER_NOT_CONFIGURED", "请先保存远程 Provider 的 API Key")
    row = session.execute(
        text(
            "SELECT ciphertext FROM provider_credentials "
            "WHERE id=:id AND tenant_id=:tenant AND status='ACTIVE' "
            "AND ((ownership_scope='PERSONAL' AND owner_user_id=:user "
            "AND ownership_key=:ownership_key) OR ownership_scope='TEAM')"
        ),
        {
            "id": str(credential_id),
            "tenant": auth.tenant_id,
            "user": auth.actor_id,
            "ownership_key": f"USER:{auth.actor_id}",
        },
    ).scalar()
    if not row:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "PROVIDER_NOT_CONFIGURED", "请先保存远程 Provider 的 API Key")
    try:
        return provider_secrets.decrypt(str(row))
    except Exception as exc:
        raise _secret_error(exc) from exc


def _default_model_attrs(model_id: str, model_type: str | None) -> tuple[str, dict, str, str]:
    """对老数据 / 空 spec 返回兜底的 model_type / capabilities / input_price / output_price，
    保证前端能力 tag 和价格 chip 不为空占位。

    推导优先级（从强到弱）：
    1) model_id 里的强特征命名（reason / r1 / vision / vl / embed 等）—— 平台命名规则最可信
    2) 调用方传入的 model_type（只有当传入值是「具体且非 chat 兜底」时才覆盖）
    3) 兜底：chat
    """
    raw_mt = (model_type or "").strip()
    mid = (model_id or "").lower()

    # Step 1: 先按 model_id 强特征推导（最可信，不管 raw_mt 传了什么都先跑一次）
    mid_mt = "chat"
    if "reason" in mid or "r1" in mid:
        mid_mt = "reasoning"
    elif "image" in mid or "vision" in mid or "vl" in mid:
        mid_mt = "image"
    elif "embed" in mid or "bge-" in mid or "gte-" in mid or "m3e" in mid:
        mid_mt = "embeddings"

    # Step 2: 如果 model_id 没识别出具体类型（仍是 chat 兜底），才参考传入的 raw_mt
    if mid_mt == "chat" and raw_mt and raw_mt != "model" and raw_mt != "chat":
        mt = raw_mt
    else:
        mt = mid_mt

    # 2. 推导 capabilities
    cap: Dict[str, bool]
    if mt == "reasoning":
        cap = {"chat": True, "reasoning": True}
    elif mt == "image":
        cap = {"chat": True, "vision": True}
    elif mt == "embeddings":
        cap = {"embeddings": True}
    else:
        cap = {"chat": True}
    # 3. 推导价格（公开的美元参考价 / 每 1M tokens）—— 真实展示，不再是 $—/M
    price_map: dict[tuple[str, str], tuple[str, str]] = {
        ("deepseek", "chat"): ("0.14", "0.28"),
        ("deepseek", "reasoning"): ("0.55", "2.19"),
        ("deepseek-v4-flash", "chat"): ("0.14", "0.28"),
        ("deepseek-v4-pro", "chat"): ("0.435", "0.87"),
        ("deepseek-reasoner", "reasoning"): ("0.55", "2.19"),
        ("deepseek-r1", "reasoning"): ("0.55", "2.19"),
        ("gpt-4o", "chat"): ("2.50", "10.00"),
        ("gpt-4o-mini", "chat"): ("0.15", "0.60"),
        ("gpt-4.1", "chat"): ("2.00", "8.00"),
        ("gpt-4.1-mini", "chat"): ("0.15", "0.60"),
        ("gpt-4.1-nano", "chat"): ("0.075", "0.30"),
        ("claude-sonnet", "chat"): ("3.00", "15.00"),
        ("claude-opus", "chat"): ("15.00", "75.00"),
        ("claude-haiku", "chat"): ("0.80", "4.00"),
        ("claude-3-5", "chat"): ("3.00", "15.00"),
        ("qwen-max", "chat"): ("0.80", "2.00"),
        ("qwen-plus", "chat"): ("0.40", "1.20"),
        ("qwen-turbo", "chat"): ("0.08", "0.20"),
        ("qwen-long", "chat"): ("0.50", "1.80"),
        ("glm-4-plus", "chat"): ("0.50", "1.60"),
        ("glm-4-flash", "chat"): ("0.05", "0.10"),
        ("glm-4-air", "chat"): ("0.10", "0.40"),
        ("glm-4-long", "chat"): ("0.05", "0.12"),
        ("glm-4", "chat"): ("0.20", "0.80"),
        ("moonshot-v1-8k", "chat"): ("0.60", "1.20"),
        ("moonshot-v1-32k", "chat"): ("1.00", "2.00"),
        ("moonshot-v1-128k", "chat"): ("1.20", "2.40"),
        ("gemini-2.5-pro", "chat"): ("0.63", "2.50"),
        ("gemini-2.5-flash", "chat"): ("0.10", "0.40"),
        ("gemini-2.0-flash", "chat"): ("0.15", "0.60"),
    }
    # 价格匹配策略（避免 deepseek-reasoner 被 deepseek 前缀误伤拿了 chat 价）：
    # 1) 把所有条目按 key 长度降序排列（更长 = 更具体，优先匹配：deepseek-reasoner > deepseek）
    # 2) 先匹配「同类型 tm == mt」的条目，命中即返回
    # 3) 匹配不到再放宽：匹配类型不同但 key 命中的（尽量保留前缀价）
    # 4) 再匹配不到走按模型类型的通用兜底价
    sorted_items = sorted(price_map.items(), key=lambda kv: -len(kv[0][0]))
    found: tuple[str, str] | None = None
    for (k, tm), (ip, op) in sorted_items:
        if tm == mt and k in mid:
            found = (ip, op); break
    if found is None:
        for (k, _tm), (ip, op) in sorted_items:
            if k in mid:
                found = (ip, op); break
    if found is None:
        # 兜底：根据模型类型给通用参考价（/M tokens 美元）
        if mt == "reasoning":
            found = ("0.50", "2.00")
        elif mt == "image":
            found = ("0.50", "1.50")
        elif mt == "embeddings":
            found = ("0.02", "0.00")
        else:
            found = ("0.20", "0.60")
    input_p, output_p = found
    return mt, cap, input_p, output_p


def _model_row_to_obj(row: Any) -> LLMModel:
    raw_mt = str(row["model_type"]) if row["model_type"] else None
    raw_cap = _safe_json_load(row["capabilities"]) or {}
    raw_ip = str(row["input_price"]) if row["input_price"] else None
    raw_op = str(row["output_price"]) if row["output_price"] else None
    model_id = str(row["model_id"])
    # 先计算 fallback 参考值（无条件，因为即使老数据有效也可能缺 reasoning/vision 等具体能力）
    mt_fb, cap_fb, ip_fb, op_fb = _default_model_attrs(model_id, raw_mt if isinstance(raw_mt, str) else None)

    # ========== model_type 最终选择 ==========
    # 策略：如果 _default_model_attrs 识别出了「具体且非兜底」的类型（reasoning/image/embeddings），
    #       说明 model_id 匹配到了平台命名规则（deepseek-reasoner、qwen-vl、bge-* 等），
    #       即使用户老数据写的是 chat，也强制校正成真实类型。
    # 只有当 mt_fb 是 chat（兜底，没识别出具体特征）时，才信任 DB 里的 raw_mt。
    if mt_fb != "chat":
        mt = mt_fb
    else:
        mt = raw_mt if raw_mt and raw_mt != "model" else mt_fb

    # ========== capabilities 最终选择 ==========
    # 合并策略：以 raw_cap 为基准（保留真实写入的能力），叠加 cap_fb 里的「具体能力」
    # （chat 是兜底能力，仅当两边都没有任何能力时才默认加上）
    merged_cap: Dict[str, bool] = dict(raw_cap) if isinstance(raw_cap, dict) else {}
    # cap_fb 里的「具体能力」优先（reasoning / vision / embeddings / tools 等）
    CONCRETE_CAPS = ("reasoning", "vision", "embeddings", "tools", "audio", "code")
    for k in CONCRETE_CAPS:
        if cap_fb.get(k) and not merged_cap.get(k):
            merged_cap[k] = True
    # 如果最终一个能力也没有，至少给个 chat
    if not any(merged_cap.values()):
        merged_cap["chat"] = True
    # 保证 chat 在有 reasoning/vision 等对话类模型时也被标记（方便前端默认 chat variant）
    if (merged_cap.get("reasoning") or merged_cap.get("vision") or merged_cap.get("audio")) and not merged_cap.get("embeddings"):
        merged_cap.setdefault("chat", True)
    cap = merged_cap

    # ========== input/output_price 最终选择 ==========
    # raw 优先（保留用户真实录入 / sync 写入的价格），只有 None/空字符串时用 fallback
    def _coerce_price(v: Optional[str], fb: str) -> str:
        if v is None:
            return fb
        s = str(v).strip()
        if s == "" or s.lower() == "null":
            return fb
        return s
    ip = _coerce_price(raw_ip, ip_fb)
    op = _coerce_price(raw_op, op_fb)
    return LLMModel(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        provider_id=str(row["provider_id"]),
        model_id=model_id,
        display_name=str(row["display_name"]),
        model_type=mt,
        context_window=int(row["context_window"]) if row["context_window"] is not None else None,
        max_output_tokens=int(row["max_output_tokens"]) if row["max_output_tokens"] is not None else None,
        endpoint_type=str(row["endpoint_type"]) if row["endpoint_type"] else None,
        capabilities=cap,
        input_price=ip,
        output_price=op,
        is_enabled=bool(row["is_enabled"]),
        is_custom=bool(row["is_custom"]),
        notes=str(row["notes"]) if row["notes"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


# ---- 1. list_llm_providers ----


def list_llm_providers(auth: AuthContext) -> list[LLMProvider]:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT id, tenant_id, user_id, preset_provider_id, provider_key, name, "
                "logo, description, websites, default_chat_endpoint, endpoint_configs, "
                "auth_type, api_key_label, api_features, settings, model_list_source, credential_id, "
                "is_enabled, created_at, updated_at "
                "FROM llm_providers WHERE tenant_id = :t"
            ),
            {"t": auth.tenant_id, "u": auth.actor_id},
        ).mappings().all()

    existing_by_key: dict[str, LLMProvider] = {}
    for row in rows:
        if not _provider_row_is_remote(row):
            continue
        provider = _provider_row_to_obj(row)
        existing_by_key[provider.provider_key] = provider

    result: list[LLMProvider] = []

    for preset in list_preset_providers():
        key = preset["id"]
        if key in existing_by_key:
            merged = existing_by_key.pop(key)
            # ⚠️ 关键：DB 中真实存在的记录 ≠ virtual preset。
            # 「个人中心 - 模型服务」左侧「已配置」分组用 isPresetBuiltin=false 过滤；
            # 若设为 true，会被前端当成「预设」而从「已配置」分组里消失。
            merged.is_preset_builtin = False
            merged.auth_optional = bool(preset.get("auth_optional", False))
            if not merged.logo:
                merged.logo = None
            if not merged.description:
                merged.description = preset.get("description")
            if not merged.websites:
                merged.websites = dict(preset.get("websites", {}))
            if not merged.default_chat_endpoint:
                merged.default_chat_endpoint = preset.get("default_chat_endpoint")
            if not merged.endpoint_configs:
                merged.endpoint_configs = dict(preset.get("endpoint_configs", {}))
            if not merged.api_features:
                merged.api_features = dict(preset.get("api_features", {}))
            if not merged.model_list_source:
                merged.model_list_source = preset.get("model_list_source", "api")
            result.append(merged)
        else:
            now = utc_now()
            virtual = LLMProvider(
                id=f"preset:{key}",
                tenant_id=auth.tenant_id,
                user_id=auth.actor_id,
                preset_provider_id=key,
                provider_key=key,
                name=str(preset["name"]),
                logo=None,
                description=preset.get("description"),
                websites=dict(preset.get("websites", {})),
                default_chat_endpoint=preset.get("default_chat_endpoint"),
                endpoint_configs=dict(preset.get("endpoint_configs", {})),
                auth_type=str(preset.get("auth_type", "api-key")),
                api_key_label=None,
                api_features=dict(preset.get("api_features", {})),
                settings={},
                model_list_source=str(preset.get("model_list_source", "api")),
                is_enabled=False,
                created_at=now,
                updated_at=now,
                is_preset_builtin=True,
                auth_optional=bool(preset.get("auth_optional", False)),
                has_api_key=False,
            )
            result.append(virtual)

    result.extend(existing_by_key.values())
    return result


# ---- 2. get_llm_provider ----


def get_llm_provider(auth: AuthContext, provider_id: str) -> LLMProvider | None:
    if provider_id.startswith("preset:"):
        key = provider_id[len("preset:"):]
        preset = get_preset_provider(key)
        if not preset:
            return None
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, tenant_id, user_id, preset_provider_id, provider_key, name, "
                    "logo, description, websites, default_chat_endpoint, endpoint_configs, "
                    "auth_type, api_key_label, api_features, settings, model_list_source, credential_id, "
                    "is_enabled, created_at, updated_at "
                    "FROM llm_providers WHERE tenant_id = :t AND preset_provider_id = :ppid"
                ),
                {"t": auth.tenant_id, "u": auth.actor_id, "ppid": key},
            ).mappings().first()
        now = utc_now()
        if row is None:
            return LLMProvider(
                id=provider_id,
                tenant_id=auth.tenant_id,
                user_id=auth.actor_id,
                preset_provider_id=key,
                provider_key=key,
                name=str(preset["name"]),
                logo=None,
                description=preset.get("description"),
                websites=dict(preset.get("websites", {})),
                default_chat_endpoint=preset.get("default_chat_endpoint"),
                endpoint_configs=dict(preset.get("endpoint_configs", {})),
                auth_type=str(preset.get("auth_type", "api-key")),
                api_key_label=None,
                api_features=dict(preset.get("api_features", {})),
                settings={},
                model_list_source=str(preset.get("model_list_source", "api")),
                is_enabled=False,
                created_at=now,
                updated_at=now,
                is_preset_builtin=True,
                auth_optional=bool(preset.get("auth_optional", False)),
                has_api_key=False,
            )
        else:
            merged = _provider_row_to_obj(row)
            # 真实 DB 记录：当成「已配置」显示；preset 信息只补字段，不把它标记成虚拟预设。
            merged.is_preset_builtin = False
            merged.auth_optional = bool(preset.get("auth_optional", False))
            if not merged.description:
                merged.description = preset.get("description")
            if not merged.websites:
                merged.websites = dict(preset.get("websites", {}))
            if not merged.default_chat_endpoint:
                merged.default_chat_endpoint = preset.get("default_chat_endpoint")
            if not merged.endpoint_configs:
                merged.endpoint_configs = dict(preset.get("endpoint_configs", {}))
            if not merged.api_features:
                merged.api_features = dict(preset.get("api_features", {}))
            if not merged.model_list_source:
                merged.model_list_source = preset.get("model_list_source", "api")
            return merged

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id, tenant_id, user_id, preset_provider_id, provider_key, name, "
                "logo, description, websites, default_chat_endpoint, endpoint_configs, "
                "auth_type, api_key_label, api_features, settings, model_list_source, credential_id, "
                "is_enabled, created_at, updated_at "
                "FROM llm_providers WHERE id = :id AND tenant_id = :t"
            ),
            {"id": provider_id, "t": auth.tenant_id, "u": auth.actor_id},
        ).mappings().first()
    if row is None:
        return None
    if not _provider_row_is_remote(row):
        return None
    return _provider_row_to_obj(row)


# ---- 3. create_llm_provider ----


def create_llm_provider(auth: AuthContext, data: dict) -> LLMProvider:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    preset_provider_id = data.get("preset_provider_id")
    provider_secret = data.get("api_key")
    merged: dict = {}

    if preset_provider_id:
        if is_local_provider_key(str(preset_provider_id)):
            raise _remote_provider_required()
        preset = get_preset_provider(preset_provider_id)
        if preset:
            merged.update({
                "preset_provider_id": preset["id"],
                "provider_key": preset["id"],
                "name": preset["name"],
                "description": preset.get("description"),
                "websites": json.dumps(preset.get("websites", {}), ensure_ascii=False),
                "default_chat_endpoint": preset.get("default_chat_endpoint"),
                "endpoint_configs": json.dumps(preset.get("endpoint_configs", {}), ensure_ascii=False),
                "auth_type": preset.get("auth_type", "api-key"),
                "api_features": json.dumps(preset.get("api_features", {}), ensure_ascii=False),
                "model_list_source": preset.get("model_list_source", "api"),
            })

    for field in ("provider_key", "name", "logo", "description", "default_chat_endpoint",
                  "auth_type", "api_key_label", "model_list_source",
                  "is_enabled"):
        if field in data and data[field] is not None:
            merged[field] = data[field]

    for json_field in ("websites", "endpoint_configs", "api_features", "settings"):
        if json_field in data and data[json_field] is not None:
            merged[json_field] = json.dumps(data[json_field], ensure_ascii=False)

    if "provider_key" not in merged or not merged["provider_key"]:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", "provider_key 必填")
    if "name" not in merged or not merged["name"]:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", "name 必填")
    if is_local_provider_key(str(merged["provider_key"])):
        raise _remote_provider_required()

    endpoint_configs = _safe_json_load(merged.get("endpoint_configs"))
    _validate_remote_endpoint_configs(endpoint_configs)
    merged["endpoint_configs"] = json.dumps(endpoint_configs, ensure_ascii=False)

    provider_id = new_id()
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO llm_providers ("
                "id, tenant_id, user_id, preset_provider_id, provider_key, name, "
                "logo, description, websites, default_chat_endpoint, endpoint_configs, "
                "auth_type, api_key, api_key_label, api_features, settings, model_list_source, "
                "is_enabled, created_at, updated_at"
                ") VALUES ("
                ":id, :t, :u, :ppid, :pkey, :name, "
                ":logo, :desc, :websites, :dce, :epc, "
                ":at, :ak, :akl, :af, :st, :mls, "
                ":ie, :now, :now"
                ")"
            ),
            {
                "id": provider_id,
                "t": auth.tenant_id,
                "u": auth.actor_id,
                "ppid": merged.get("preset_provider_id"),
                "pkey": merged["provider_key"],
                "name": merged["name"],
                "logo": merged.get("logo"),
                "desc": merged.get("description"),
                "websites": merged.get("websites", "{}"),
                "dce": merged.get("default_chat_endpoint"),
                "epc": merged.get("endpoint_configs", "{}"),
                "at": merged.get("auth_type", "api-key"),
                "ak": None,
                "akl": merged.get("api_key_label"),
                "af": merged.get("api_features", "{}"),
                "st": merged.get("settings", "{}"),
                "mls": merged.get("model_list_source", "api"),
                "ie": bool(merged.get("is_enabled", False)),
                "now": now,
            },
        )
        if "api_key" in data and provider_secret is not None:
            _upsert_provider_secret(session, auth, provider_id, str(provider_secret))
        session.commit()

    provider = get_llm_provider(auth, provider_id)
    if provider is None:
        raise _not_found("LLM Provider")
    return provider


# ---- 4. update_llm_provider ----


def update_llm_provider(auth: AuthContext, provider_id: str, data: dict) -> LLMProvider:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id, tenant_id, user_id, provider_key FROM llm_providers WHERE id = :id"
            ),
            {"id": provider_id},
        ).mappings().first()
        if row is None:
            raise _not_found("LLM Provider")
        if str(row["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()
        if is_local_provider_key(str(row["provider_key"])):
            raise _remote_provider_required()
        if "endpoint_configs" in data and data["endpoint_configs"] is not None:
            _validate_remote_endpoint_configs(data["endpoint_configs"])

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": provider_id}

        simple_fields = ("name", "logo", "description", "default_chat_endpoint",
                         "auth_type", "api_key_label", "model_list_source")
        for field in simple_fields:
            if field in data and data[field] is not None:
                set_clauses.append(f"{field} = :{field}")
                params[field] = data[field]

        if "is_enabled" in data and data["is_enabled"] is not None:
            set_clauses.append("is_enabled = :is_enabled")
            params["is_enabled"] = bool(data["is_enabled"])

        for json_field in ("websites", "endpoint_configs", "api_features", "settings"):
            if json_field in data and data[json_field] is not None:
                set_clauses.append(f"{json_field} = :{json_field}")
                params[json_field] = json.dumps(data[json_field], ensure_ascii=False)

        credential_present = "api_key" in data
        credential_changed = credential_present and data["api_key"] is not None
        credential_revoke = credential_present and data["api_key"] is None
        if not set_clauses and not credential_changed and not credential_revoke:
            session.close()
            provider = get_llm_provider(auth, provider_id)
            if provider is None:
                raise _not_found("LLM Provider")
            return provider

        set_clauses.append("updated_at = :now")
        params["now"] = utc_now()

        stmt = "UPDATE llm_providers SET " + ", ".join(set_clauses) + " WHERE id = :id"
        if set_clauses:
            session.execute(text(stmt), params)
        if credential_changed:
            _upsert_provider_secret(session, auth, provider_id, str(data["api_key"]))
        elif credential_revoke:
            _revoke_provider_secret(session, auth, provider_id)
        session.commit()

    provider = get_llm_provider(auth, provider_id)
    if provider is None:
        raise _not_found("LLM Provider")
    return provider


# ---- 5. delete_llm_provider ----


def delete_llm_provider(auth: AuthContext, provider_id: str) -> bool:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    if provider_id.startswith("preset:"):
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT tenant_id, user_id FROM llm_providers WHERE id = :id"),
            {"id": provider_id},
        ).mappings().first()
        if row is None:
            return False
        if str(row["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()
        session.execute(
            text("DELETE FROM llm_models WHERE provider_id = :pid AND tenant_id = :t"),
            {"pid": provider_id, "t": auth.tenant_id, "u": auth.actor_id},
        )
        result = session.execute(
            text("DELETE FROM llm_providers WHERE id = :id AND tenant_id = :t"),
            {"id": provider_id, "t": auth.tenant_id, "u": auth.actor_id},
        )
        session.commit()
    return result.rowcount > 0


# ---- 6. enable_llm_provider ----


def enable_llm_provider(auth: AuthContext, provider_id: str, enabled: bool) -> LLMProvider:
    return update_llm_provider(auth, provider_id, {"is_enabled": enabled})


# ---- 7. list_llm_models ----


def _provider_catalog_whitelist(provider_row: dict) -> tuple[set[str] | None, list[dict] | None]:
    """若 provider 关联预设且预设定义了 catalog_models 白名单，则返回 (id_set, catalog_specs)，否则 (None, None)。"""
    ppid = provider_row.get("preset_provider_id") if isinstance(provider_row, dict) else None
    if not ppid:
        return None, None
    preset = get_preset_provider(str(ppid))
    if not preset:
        return None, None
    catalog_models = preset.get("catalog_models")
    if not isinstance(catalog_models, list) or len(catalog_models) == 0:
        return None, None
    id_set: set[str] = set()
    specs: list[dict] = []
    for m in catalog_models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        id_set.add(mid)
        display = m.get("name") or m.get("display_name") or mid
        spec = {
            "model_id": mid,
            "display_name": display,
        }
        for k in ("model_type", "context_window", "max_output_tokens", "capabilities", "input_price", "output_price"):
            if k in m and m[k] is not None:
                spec[k] = m[k]
        specs.append(spec)
    return id_set, specs


def list_llm_models(auth: AuthContext, provider_id: str) -> list[LLMModel]:
    resolved_pid = provider_id
    if provider_id.startswith("preset:"):
        key = provider_id[len("preset:"):]
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, preset_provider_id, provider_key, endpoint_configs FROM llm_providers "
                    "WHERE tenant_id = :t AND preset_provider_id = :ppid"
                ),
                {"t": auth.tenant_id, "u": auth.actor_id, "ppid": key},
            ).mappings().one_or_none()
        if row is None:
            return []
        resolved_pid = str(row["id"])
        provider_info = dict(row)
    else:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            prow = session.execute(
                text(
                    "SELECT id, preset_provider_id, provider_key, endpoint_configs FROM llm_providers "
                    "WHERE id = :id AND tenant_id = :t"
                ),
                {"id": provider_id, "t": auth.tenant_id, "u": auth.actor_id},
            ).mappings().one_or_none()
        if prow is None:
            return []
        provider_info = dict(prow)

    if not _provider_row_is_remote(provider_info):
        return []

    whitelist_ids, _catalog_specs = _provider_catalog_whitelist(provider_info)

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT id, tenant_id, user_id, provider_id, model_id, display_name, "
                "model_type, context_window, max_output_tokens, endpoint_type, "
                "capabilities, input_price, output_price, is_enabled, is_custom, "
                "notes, created_at, updated_at "
                "FROM llm_models WHERE provider_id = :pid AND tenant_id = :t "
                "ORDER BY created_at ASC"
            ),
            {"pid": resolved_pid, "t": auth.tenant_id, "u": auth.actor_id},
        ).mappings().all()
    objs: list[LLMModel] = []
    for r in rows:
        mid = str(r["model_id"])
        # 白名单过滤：只在有预设白名单时启用；自定义模型（is_custom=1）不受白名单限制
        if whitelist_ids is not None and int(r["is_custom"] or 0) == 0 and mid not in whitelist_ids:
            continue
        objs.append(_model_row_to_obj(r))
    return objs


# ---- 8. get_llm_model ----


def get_llm_model(auth: AuthContext, model_id: str) -> LLMModel | None:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id, tenant_id, user_id, provider_id, model_id, display_name, "
                "model_type, context_window, max_output_tokens, endpoint_type, "
                "capabilities, input_price, output_price, is_enabled, is_custom, "
                "notes, created_at, updated_at "
                "FROM llm_models WHERE id = :id AND tenant_id = :t"
            ),
            {"id": model_id, "t": auth.tenant_id, "u": auth.actor_id},
        ).mappings().first()
    if row is None:
        return None
    return _model_row_to_obj(row)


# ---- 9. create_llm_model ----


def create_llm_model(auth: AuthContext, provider_id: str, data: dict) -> LLMModel:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    if provider_id.startswith("preset:"):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", "预设服务商未保存，无法添加模型。请先创建 Provider 实例。")
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        prow = session.execute(
            text(
                "SELECT id, tenant_id, user_id, provider_key, endpoint_configs "
                "FROM llm_providers WHERE id = :id"
            ),
            {"id": provider_id},
        ).mappings().first()
        if prow is None:
            raise _not_found("LLM Provider")
        if str(prow["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()
        if not _provider_row_is_remote(prow):
            raise _remote_provider_required()

        required = ("model_id", "display_name")
        for field in required:
            if field not in data or not data[field]:
                raise ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", f"{field} 必填")

        model_pk = new_id()
        now = utc_now()
        session.execute(
            text(
                "INSERT INTO llm_models ("
                "id, tenant_id, user_id, provider_id, model_id, display_name, "
                "model_type, context_window, max_output_tokens, endpoint_type, "
                "capabilities, input_price, output_price, is_enabled, is_custom, "
                "notes, created_at, updated_at"
                ") VALUES ("
                ":id, :t, :u, :pid, :mid, :dn, "
                ":mt, :cw, :mot, :et, "
                ":cap, :ip, :op, :ie, :ic, "
                ":notes, :now, :now"
                ")"
            ),
            {
                "id": model_pk,
                "t": auth.tenant_id,
                "u": auth.actor_id,
                "pid": provider_id,
                "mid": data["model_id"],
                "dn": data["display_name"],
                "mt": data.get("model_type", "chat"),
                "cw": data.get("context_window"),
                "mot": data.get("max_output_tokens"),
                "et": data.get("endpoint_type"),
                "cap": json.dumps(data.get("capabilities", {}), ensure_ascii=False),
                "ip": data.get("input_price"),
                "op": data.get("output_price"),
                "ie": bool(data.get("is_enabled", True)),
                "ic": bool(data.get("is_custom", True)),
                "notes": data.get("notes"),
                "now": now,
            },
        )
        session.commit()

    model = get_llm_model(auth, model_pk)
    if model is None:
        raise _not_found("LLM Model")
    return model


# ---- 10. update_llm_model ----


def update_llm_model(auth: AuthContext, model_id: str, data: dict) -> LLMModel:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT id, tenant_id, user_id FROM llm_models WHERE id = :id"),
            {"id": model_id},
        ).mappings().first()
        if row is None:
            raise _not_found("LLM Model")
        if str(row["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": model_id}

        simple_fields = ("model_id", "display_name", "model_type", "context_window",
                         "max_output_tokens", "endpoint_type", "input_price",
                         "output_price", "notes")
        for field in simple_fields:
            if field in data and data[field] is not None:
                set_clauses.append(f"{field} = :{field}")
                params[field] = data[field]

        for bool_field in ("is_enabled", "is_custom"):
            if bool_field in data and data[bool_field] is not None:
                set_clauses.append(f"{bool_field} = :{bool_field}")
                params[bool_field] = bool(data[bool_field])

        if "capabilities" in data and data["capabilities"] is not None:
            set_clauses.append("capabilities = :capabilities")
            params["capabilities"] = json.dumps(data["capabilities"], ensure_ascii=False)

        if not set_clauses:
            session.close()
            model = get_llm_model(auth, model_id)
            if model is None:
                raise _not_found("LLM Model")
            return model

        set_clauses.append("updated_at = :now")
        params["now"] = utc_now()

        stmt = "UPDATE llm_models SET " + ", ".join(set_clauses) + " WHERE id = :id"
        session.execute(text(stmt), params)
        session.commit()

    model = get_llm_model(auth, model_id)
    if model is None:
        raise _not_found("LLM Model")
    return model


# ---- 11. delete_llm_model ----


def delete_llm_model(auth: AuthContext, model_id: str) -> bool:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT tenant_id, user_id FROM llm_models WHERE id = :id"),
            {"id": model_id},
        ).mappings().first()
        if row is None:
            return False
        if str(row["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()
        result = session.execute(
            text("DELETE FROM llm_models WHERE id = :id AND tenant_id = :t"),
            {"id": model_id, "t": auth.tenant_id, "u": auth.actor_id},
        )
        session.commit()
    return result.rowcount > 0


# ---- 12. sync_models_from_provider ----


def _remote_models_endpoint(provider_row: dict[str, Any]) -> str:
    """Resolve the configured remote model-list endpoint without inventing one."""
    endpoint_configs_raw = provider_row.get("endpoint_configs") or "{}"
    try:
        endpoint_configs = (
            json.loads(endpoint_configs_raw)
            if isinstance(endpoint_configs_raw, str)
            else endpoint_configs_raw
        )
    except (TypeError, ValueError):
        endpoint_configs = {}
    _validate_remote_endpoint_configs(endpoint_configs)

    endpoint_name = str(provider_row.get("default_chat_endpoint") or "")
    config = endpoint_configs.get(endpoint_name, {})
    if not isinstance(config, dict):
        raise _remote_provider_required()
    for models_urls_key in ("modelsApiUrls", "models_api_urls"):
        models_urls = config.get(models_urls_key)
        if not isinstance(models_urls, dict):
            continue
        for key in (endpoint_name, "openai", "default"):
            candidate = models_urls.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _validate_remote_url(candidate)

    base_url = config.get("baseUrl") or config.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise _remote_provider_required()
    base = _validate_remote_url(base_url).rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/embeddings"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/models"


def _fetch_remote_models(provider_row: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    """Fetch and normalize an OpenAI-compatible remote ``GET /models`` response."""
    endpoint = _remote_models_endpoint(provider_row)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "REMOTE_PROVIDER_ERROR", "远程 Provider 模型列表获取失败") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "REMOTE_PROVIDER_ERROR", "远程 Provider 模型列表不可用") from exc

    raw_items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "REMOTE_PROVIDER_ERROR", "远程 Provider 返回的模型列表格式无效")
    models: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        capabilities = item.get("capabilities")
        models.append(
            {
                "model_id": model_id.strip(),
                "display_name": str(item.get("display_name") or item.get("name") or model_id).strip(),
                "model_type": item.get("model_type") or item.get("type") or "chat",
                "context_window": item.get("context_window") or item.get("context_length"),
                "max_output_tokens": item.get("max_output_tokens"),
                "capabilities": capabilities if isinstance(capabilities, dict) else {},
                "input_price": item.get("input_price"),
                "output_price": item.get("output_price"),
            }
        )
    if not models:
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "REMOTE_PROVIDER_ERROR", "远程 Provider 未返回可用模型")
    return models


def sync_models_from_provider(auth: AuthContext, provider_id: str) -> dict:
    if str(auth.tenant_role) not in ("OWNER", "ADMIN"):
        raise _permission_denied()
    if provider_id.startswith("preset:"):
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "PROVIDER_NOT_CONFIGURED",
            "请先保存远程 Provider 的 API Key，再获取真实模型列表",
        )

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        prow = session.execute(
            text(
                "SELECT id, tenant_id, user_id, preset_provider_id, provider_key, "
                "default_chat_endpoint, endpoint_configs, credential_id "
                "FROM llm_providers WHERE id = :id"
            ),
            {"id": provider_id},
        ).mappings().first()
        if prow is None:
            raise _not_found("LLM Provider")
        if str(prow["tenant_id"]) != auth.tenant_id:
            raise _permission_denied()
        if not _provider_row_is_remote(prow):
            raise _remote_provider_required()

        key = str(prow["provider_key"])
        api_key = _read_provider_secret(session, auth, prow)
        remote_models = _fetch_remote_models(dict(prow), api_key)

        now = utc_now()
        created_count = 0
        updated_count = 0
        for spec in remote_models:
            mid = spec["model_id"]
            exists = session.execute(
                text(
                    "SELECT 1 FROM llm_models WHERE provider_id = :pid AND model_id = :mid"
                ),
                {"pid": provider_id, "mid": mid},
            ).scalar()
            if exists:
                session.execute(
                    text(
                        "UPDATE llm_models SET display_name = :dn, model_type = :mt, "
                        "context_window = :cw, max_output_tokens = :mot, capabilities = :cap, "
                        "input_price = :ip, output_price = :op, updated_at = :now "
                        "WHERE provider_id = :pid AND model_id = :mid"
                    ),
                    {
                        "pid": provider_id,
                        "mid": mid,
                        "dn": spec["display_name"],
                        "mt": str(spec.get("model_type") or "chat"),
                        "cw": spec.get("context_window"),
                        "mot": spec.get("max_output_tokens"),
                        "cap": json.dumps(spec.get("capabilities") or {}, ensure_ascii=False),
                        "ip": spec.get("input_price"),
                        "op": spec.get("output_price"),
                        "now": now,
                    },
                )
                updated_count += 1
                continue
            session.execute(
                text(
                    "INSERT INTO llm_models ("
                    "id, tenant_id, user_id, provider_id, model_id, display_name, "
                    "model_type, context_window, max_output_tokens, endpoint_type, "
                    "capabilities, input_price, output_price, is_enabled, is_custom, "
                    "notes, created_at, updated_at"
                    ") VALUES ("
                    ":id, :t, :u, :pid, :mid, :dn, "
                    ":mt, :cw, :mot, :et, "
                    ":cap, :ip, :op, 1, 0, NULL, :now, :now"
                    ")"
                ),
                {
                    "id": new_id(),
                    "t": auth.tenant_id,
                    "u": auth.actor_id,
                    "pid": provider_id,
                    "mid": mid,
                    "dn": spec["display_name"],
                    "mt": str(spec.get("model_type") or "chat"),
                    "cw": spec.get("context_window"),
                    "mot": spec.get("max_output_tokens"),
                    "et": None,
                    "cap": json.dumps(spec.get("capabilities") or {}, ensure_ascii=False),
                    "ip": spec.get("input_price"),
                    "op": spec.get("output_price"),
                    "now": now,
                },
            )
            created_count += 1

        session.commit()

    return {
        "status": "ok",
        "provider_id": provider_id,
        "provider_key": key,
        "models_found": len(remote_models),
        "models_created": created_count,
        "models_updated": updated_count,
        "models": remote_models,
        "source": "remote",
    }
