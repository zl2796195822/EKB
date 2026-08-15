"""PH3 external-boundary contract: every unconfigured provider fails closed.

The acceptance rule for PH3–PH6 is that an absent object store / embedding
provider must raise a typed ``*Unavailable`` error. It must never return a
fabricated success, and it must never degrade into a silent no-op that would let
a document look ingested while carrying no vectors.

Path normalization is tested here too because it is the other place where a
permissive fallback would be a security bug (directory traversal into another
tenant's prefix).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ekb_api.services.embedding import EmbeddingUnavailable, build_embedding_client
from ekb_api.services.parsers.registry import (
    PATH_INVALID,
    PATH_TOO_DEEP,
    is_retryable,
    sanitize_detail,
)
from ekb_api.services.storage import (
    MAX_PATH_DEPTH,
    LocalFilesystemStorageClient,
    ObjectStorageUnavailable,
    PathValidationError,
    S3CompatibleStorageClient,
    build_storage_client,
    normalize_relative_path,
)


def test_object_storage_fails_closed_when_unconfigured() -> None:
    with pytest.raises(ObjectStorageUnavailable):
        build_storage_client()


def test_local_development_storage_is_durable_and_confined(tmp_path: Path) -> None:
    client = LocalFilesystemStorageClient(tmp_path / "objects")
    object_key = "uploads/tenant-1/kb-1/item-1"
    body = b"real local object"

    head = client.write_object(tenant_id="tenant-1", object_key=object_key, data=body)

    assert client.get_object_bytes(tenant_id="tenant-1", object_key=object_key) == body
    assert client.head_object(tenant_id="tenant-1", object_key=object_key) == head
    assert (tmp_path / "objects" / "tenant-1" / object_key).read_bytes() == body
    with pytest.raises(ObjectStorageUnavailable):
        client.get_object_bytes(tenant_id="tenant-1", object_key="../tenant-2/secret")


def test_s3_presign_is_remote_and_does_not_expose_secret() -> None:
    client = S3CompatibleStorageClient(
        endpoint="https://objects.example.invalid",
        bucket="ekb-test",
        region="us-east-1",
        access_key="ACCESS_KEY",
        secret_key="SECRET_VALUE",
    )
    presigned = client.put_presigned(
        tenant_id="tenant-1",
        object_key="uploads/tenant-1/kb-1/item-1",
        byte_size=4,
        method="SINGLE",
        part_size=None,
        expires_in_seconds=60,
    )
    assert presigned.upload_urls[0].startswith("https://objects.example.invalid/")
    assert "SECRET_VALUE" not in presigned.upload_urls[0]
    assert "X-Amz-Signature=" in presigned.upload_urls[0]


def test_s3_uses_private_endpoint_for_server_side_object_operations(monkeypatch) -> None:
    client = S3CompatibleStorageClient(
        endpoint="https://objects.example.invalid",
        internal_endpoint="http://172.17.0.1:2443",
        bucket="ekb-test",
        region="us-east-1",
        access_key="ACCESS_KEY",
        secret_key="SECRET_VALUE",
    )
    public = client.put_presigned(
        tenant_id="tenant-1",
        object_key="uploads/tenant-1/kb-1/item-1",
        byte_size=4,
        method="SINGLE",
        part_size=None,
        expires_in_seconds=60,
    )
    assert public.upload_urls[0].startswith("https://objects.example.invalid/")

    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b"server-side bytes"

    class _Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _Response()

    def _build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    import urllib.request

    monkeypatch.setattr(urllib.request, "build_opener", _build_opener)
    result = client.get_object_bytes(
        tenant_id="tenant-1", object_key="uploads/tenant-1/kb-1/item-1"
    )
    assert result == b"server-side bytes"
    assert str(captured["url"]).startswith("http://172.17.0.1:2443/")
    assert captured["timeout"] == 60
    assert len(captured["handlers"]) == 1


def test_embedding_fails_closed_when_unconfigured() -> None:
    with pytest.raises(EmbeddingUnavailable):
        build_embedding_client()


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "\\\\server\\share",
        "../../secrets.txt",
        "docs/../../escape.txt",
        "docs/\x00null.txt",
        "",
        "CON",
        "docs/PRN.txt",
    ],
)
def test_path_normalization_rejects_unsafe_inputs(raw: str) -> None:
    with pytest.raises(PathValidationError) as excinfo:
        normalize_relative_path(raw)
    assert excinfo.value.code == PATH_INVALID


def test_path_depth_limit_is_enforced() -> None:
    too_deep = "/".join(["d"] * (MAX_PATH_DEPTH + 1)) + "/f.txt"
    with pytest.raises(PathValidationError) as excinfo:
        normalize_relative_path(too_deep)
    assert excinfo.value.code == PATH_TOO_DEEP


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("docs/a.txt", "docs/a.txt"),
        ("docs\\a.txt", "docs/a.txt"),
        ("./docs//a.txt", "docs/a.txt"),
        ("docs/./sub/a.txt", "docs/sub/a.txt"),
    ],
)
def test_path_normalization_is_stable(raw: str, expected: str) -> None:
    assert normalize_relative_path(raw) == expected


def test_error_taxonomy_marks_only_transient_codes_retryable() -> None:
    assert is_retryable("EMBEDDING_RATE_LIMITED") is True
    assert is_retryable("PARSER_ENCRYPTED") is False
    assert is_retryable("FILE_TOO_LARGE") is False
    assert is_retryable("UNKNOWN_CODE_FROM_PROVIDER") is False


def test_sanitize_detail_drops_unknown_and_sensitive_keys() -> None:
    cleaned = sanitize_detail(
        {
            "retry_after_seconds": 30,
            "api_key": "sk-should-never-persist",
            "provider_trace": "stack/trace/with/paths",
            "stderr": "libreoffice crashed at /tmp/xyz",
        }
    )
    assert "api_key" not in cleaned
    assert "provider_trace" not in cleaned
    assert "stderr" not in cleaned
    assert sanitize_detail(None) == {}
