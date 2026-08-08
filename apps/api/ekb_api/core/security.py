from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


def hash_for_audit(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---- 口令哈希（M1-2 真实鉴权）----
# 使用标准库 pbkdf2_hmac（SHA-256，加盐多次迭代），不引入第三方依赖；
# 格式 "pbkdf2_sha256$<iterations>$<salt>$<hash>"（urlsafe base64）。
# 注：本机 Python 构建未编译 scrypt，改用 pbkdf2_hmac（同等强度、纯标准库）。

_PBKDF2_DIGEST = "sha256"
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_DKLEN = 32


def hash_password(password: str) -> str:
    """对明文口令做加盐 PBKDF2-HMAC-SHA256 哈希，返回可存储的字符串。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_DIGEST, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, _PBKDF2_DKLEN
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """恒定时间校验明文口令与存储哈希是否匹配。"""
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, iter_s, salt_b64, hash_b64 = stored.split("$")
        iterations = int(iter_s)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_DIGEST, password.encode("utf-8"), salt, iterations, _PBKDF2_DKLEN
    )
    return hmac.compare_digest(dk, expected)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_signed_payload(token: str, secret: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 2:
        return None

    body, signature = parts
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64decode(signature)
    except ValueError:
        return None

    if not hmac.compare_digest(expected, provided):
        return None

    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(time.time()):
        return None

    return payload
