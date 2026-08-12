"""Unified secret envelope for v4 provider credentials (PH1 · T05).

Provider secrets are encrypted-first: the plaintext never lands in the
database, only a Fernet ciphertext, a stable ``key_version`` and a 4-char
``secret_last4`` redaction prefix.

The active master key is read from ``settings.provider_master_key``
(``EKB_PROVIDER_MASTER_KEY``).  The configuration intentionally has **no
development default** — an empty key means the provider credential path is
unavailable and callers must fail closed rather than fall back to a
placeholder.  A managed/production KMS wiring is a documented BLOCKED gate;
the crypto primitives here are real and unit-testable once a key is supplied.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass

from ekb_api.core.config import get_settings


class SecretKeyUnavailable(RuntimeError):
    """Raised when no provider master key is configured (fail-closed)."""


class SecretEnvelopeError(ValueError):
    """Raised when an envelope is malformed or fails decryption."""


_KEY_VERSION_PREFIX = "ekb-provider-v1:"


def _master_key_material() -> bytes:
    """Return the raw provider master key material (fail-closed if absent)."""
    material = (get_settings().provider_master_key or "").encode("utf-8")
    if not material:
        raise SecretKeyUnavailable(
            "provider master key is not configured; set EKB_PROVIDER_MASTER_KEY "
            "(production must use a managed KMS, currently BLOCKED in this env)"
        )
    return material


def _fernet_key() -> bytes:
    """Derive a 32-byte URL-safe base64 Fernet key from the master material."""
    digest = hashlib.sha256(_master_key_material()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    from cryptography.fernet import Fernet, MultiFernet

    # MultiFernet is pre-wired so key rotation can later carry (current,
    # previous) keys without a schema change; the provider_credentials row
    # already stores key_version for re-encryption bookkeeping.
    return MultiFernet([Fernet(_fernet_key())])


def current_key_version() -> str:
    """A stable, non-secret identifier of the active key material.

    Derived from the master material so rotation is detectable without
    exposing the key itself.  Returns an empty string when unconfigured.
    """
    material = (get_settings().provider_master_key or "").encode("utf-8")
    if not material:
        return ""
    return _KEY_VERSION_PREFIX + hashlib.sha256(material).hexdigest()[:16]


def is_configured() -> bool:
    """True only when a provider master key is present."""
    return bool(get_settings().provider_master_key)


def redact(plaintext: str, *, last4_length: int = 4) -> str:
    """Produce a non-reversible display prefix of a secret.

    Keeps the leading scheme token (e.g. ``sk-``) and the last ``last4_length``
    characters, replacing the middle with a fixed bullet run.  Returns ``""``
    for empty input.
    """
    clean = (plaintext or "").strip()
    if not clean:
        return ""
    match = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*?-)", clean)
    scheme = match.group(1) if match else ""
    body = clean[len(scheme):]
    if len(body) <= last4_length:
        # Too short to safely reveal; return only the scheme token.
        return f"{scheme}{'•' * max(4, len(body))}"
    tail = body[-last4_length:]
    return f"{scheme}{'•' * (len(body) - last4_length)}{tail}"


@dataclass(frozen=True)
class SecretEnvelope:
    ciphertext: str
    key_version: str
    secret_last4: str


def encrypt(plaintext: str) -> SecretEnvelope:
    """Encrypt a provider secret into a ciphertext envelope.

    The plaintext is never persisted; only the envelope is stored.  Raises
    :class:`SecretKeyUnavailable` when no master key is configured.
    """
    if not plaintext:
        raise SecretEnvelopeError("cannot encrypt an empty secret")
    material = _master_key_material()
    fernet = _fernet()
    ciphertext = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    last4 = (plaintext or "")[-4:]
    return SecretEnvelope(
        ciphertext=ciphertext,
        key_version=_KEY_VERSION_PREFIX + hashlib.sha256(material).hexdigest()[:16],
        secret_last4=last4,
    )


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored ciphertext back to plaintext.

    Used only on the trusted write-once return path; never in list/read APIs.
    Raises :class:`SecretKeyUnavailable` or :class:`SecretEnvelopeError`.
    """
    if not ciphertext:
        raise SecretEnvelopeError("cannot decrypt an empty ciphertext")
    try:
        plaintext = _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - normalize all crypto failures
        raise SecretEnvelopeError(f"ciphertext decryption failed: {exc}") from exc
    return plaintext


def reencrypt(ciphertext: str) -> SecretEnvelope:
    """Re-encrypt an existing ciphertext under the current key version.

    The operator path for key rotation: decrypt with the available key ring
    and re-encrypt.  With a single active key this is a round-trip that
    refreshes ``key_version`` metadata; a managed KMS rotation would swap the
    underlying material here (BLOCKED in this environment).
    """
    plaintext = decrypt(ciphertext)
    return encrypt(plaintext)


__all__ = [
    "SecretEnvelope",
    "SecretEnvelopeError",
    "SecretKeyUnavailable",
    "current_key_version",
    "decrypt",
    "encrypt",
    "is_configured",
    "redact",
    "reencrypt",
]
