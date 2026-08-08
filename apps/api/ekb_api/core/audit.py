from __future__ import annotations

import hashlib
from typing import Any

from fastapi import Request

# 审计结果枚举（对应安全与合规设计 6.2 节）。
RESULT_SUCCESS = "SUCCESS"
RESULT_FAILURE = "FAILURE"
RESULT_DENIED = "DENIED"
RESULT_DEGRADED = "DEGRADED"

# 必须脱敏的字段名（小写匹配），任何层级的同名键都被替换为 [REDACTED]。
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "refresh_token",
        "access_token",
        "secret",
        "api_key",
        "mfa_code",
        "authorization",
    }
)


def hash_fingerprint(value: str) -> str:
    """对 IP / User-Agent 等指纹做单向哈希，避免审计日志泄露可识别信息。"""
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏 metadata 中的敏感字段，返回可安全持久化的副本。"""
    redacted: dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in _SENSITIVE_KEYS:
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = redact_metadata(value)
        else:
            redacted[key] = value
    return redacted


def extract_fingerprints(request: Request) -> tuple[str, str]:
    """从请求中提取并哈希 IP 和 User-Agent，供审计日志使用。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    return hash_fingerprint(ip), hash_fingerprint(user_agent)
