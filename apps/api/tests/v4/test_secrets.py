"""Unit tests for the v4 provider secret envelope (PH1 · T05).

These are real crypto round-trips: they require a provider master key, which
the test sets via ``EKB_PROVIDER_MASTER_KEY``.  They deliberately exercise the
fail-closed path too.
"""

from __future__ import annotations

import os

import pytest

from ekb_api.core.config import get_settings
from ekb_api.services import secrets


@pytest.fixture(autouse=True)
def _provider_key():
    os.environ["EKB_PROVIDER_MASTER_KEY"] = "test-provider-master-key-material"
    get_settings.cache_clear()
    yield
    os.environ.pop("EKB_PROVIDER_MASTER_KEY", None)
    get_settings.cache_clear()


def test_is_configured_reflects_key_presence() -> None:
    assert secrets.is_configured() is True
    os.environ.pop("EKB_PROVIDER_MASTER_KEY", None)
    get_settings.cache_clear()
    assert secrets.is_configured() is False


def test_encrypt_decrypt_roundtrip_preserves_plaintext() -> None:
    envelope = secrets.encrypt("sk-super-secret-value-1234")
    assert envelope.ciphertext != "sk-super-secret-value-1234"
    assert envelope.secret_last4 == "1234"
    assert envelope.key_version.startswith("ekb-provider-v1:")
    assert secrets.decrypt(envelope.ciphertext) == "sk-super-secret-value-1234"


def test_redact_keeps_scheme_and_tail_only() -> None:
    masked = secrets.redact("sk-super-secret-value-1234")
    assert masked.startswith("sk-")
    assert masked.endswith("1234")
    assert "super-secret" not in masked
    assert secrets.redact("") == ""


def test_fail_closed_without_master_key() -> None:
    os.environ.pop("EKB_PROVIDER_MASTER_KEY", None)
    get_settings.cache_clear()
    with pytest.raises(secrets.SecretKeyUnavailable):
        secrets.encrypt("anything")
    # Status helpers fail closed by returning an empty version, never a key.
    assert secrets.current_key_version() == ""
    assert secrets.is_configured() is False


def test_reencrypt_refreshes_key_version() -> None:
    envelope = secrets.encrypt("sk-another-secret-9999")
    rotated = secrets.reencrypt(envelope.ciphertext)
    assert rotated.ciphertext != envelope.ciphertext
    assert rotated.key_version == envelope.key_version
    assert secrets.decrypt(rotated.ciphertext) == "sk-another-secret-9999"
