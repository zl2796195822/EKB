"""Object storage boundary and the resumable upload protocol (PH3).

The object store is reached only through the :class:`StorageClient` Protocol.
A real implementation is injected at the boundary; when configuration is
missing the factory fails closed with :class:`ObjectStorageUnavailable` — there
is no mock success path.  Tests inject an in-memory fake implementation (DI, not
a mock) so the orchestration can be verified without external services.

Upload limits (04-knowledge-base.md §3.2) are enforced server-side on every
item and batch: 100 MB per file, 1000 files per batch, 5 GB per batch, depth
32.  Path normalization rejects traversal, absolute paths, NUL/control chars
and platform-reserved names; the normalized relative path is the document
identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from sqlalchemy import Engine, text

from ekb_api.core.db import get_engine
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now
from ekb_api.services.jobs import JobService, get_job_service

# Upload limits (04 §3.2).
MAX_FILE_BYTES = 104_857_600  # 100 MB
MAX_BATCH_FILES = 1000
MAX_BATCH_BYTES = 5_368_709_120  # 5 GB
MAX_PATH_DEPTH = 32
DEFAULT_SESSION_TTL_SECONDS = 3600

# Platform-reserved names (Windows device names) — must never be a path segment.
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class ObjectStorageUnavailable(RuntimeError):
    """Raised when no usable object store is configured; uploads fail closed."""


class PathValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- Storage client boundary (Protocol) -----------------------------------


@dataclass(frozen=True)
class PresignedUpload:
    provider_upload_id: Optional[str]
    upload_urls: list[str]
    part_size: Optional[int]


@dataclass(frozen=True)
class ObjectHead:
    byte_size: int
    sha256: Optional[str]


class LocalFilesystemStorageClient:
    """Durable local object store for development only.

    This is a real filesystem-backed adapter, not an in-memory test fake. It is
    enabled only when ``EKB_ENV=development`` and
    ``EKB_OBJECT_STORAGE_LOCAL_ROOT`` is explicitly set. The upload URL is a
    protected API route, so the browser never receives a filesystem path.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, tenant_id: str, object_key: str) -> Path:
        if not tenant_id or not object_key or ".." in Path(object_key).parts:
            raise ObjectStorageUnavailable("invalid local object key")
        candidate = (self.root / tenant_id / object_key).resolve()
        if self.root not in candidate.parents:
            raise ObjectStorageUnavailable("local object key escapes storage root")
        return candidate

    def put_presigned(
        self,
        *,
        tenant_id: str,
        object_key: str,
        byte_size: int,
        method: str,
        part_size: Optional[int],
        expires_in_seconds: int,
    ) -> PresignedUpload:
        if method != "SINGLE":
            raise ObjectStorageUnavailable("local development storage supports SINGLE uploads only")
        self._path(tenant_id, object_key)
        from urllib.parse import quote

        return PresignedUpload(
            provider_upload_id=None,
            upload_urls=[f"/api/v1/kb/uploads/objects?object_key={quote(object_key, safe='')}"],
            part_size=None,
        )

    def complete_multipart(
        self, *, tenant_id: str, object_key: str, provider_upload_id: str, parts: list[dict]
    ) -> None:
        raise ObjectStorageUnavailable(
            "local development storage does not support multipart uploads"
        )

    def abort(self, *, tenant_id: str, object_key: str, provider_upload_id: str) -> None:
        path = self._path(tenant_id, object_key)
        if path.exists():
            path.unlink()

    def get_object_url(self, *, tenant_id: str, object_key: str) -> str:
        from urllib.parse import quote

        return f"/api/v1/kb/uploads/objects?object_key={quote(object_key, safe='')}"

    def get_object_bytes(self, *, tenant_id: str, object_key: str) -> bytes:
        return self._path(tenant_id, object_key).read_bytes()

    def head_object(self, *, tenant_id: str, object_key: str) -> ObjectHead:
        data = self.get_object_bytes(tenant_id=tenant_id, object_key=object_key)
        return ObjectHead(byte_size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def write_object(self, *, tenant_id: str, object_key: str, data: bytes) -> ObjectHead:
        path = self._path(tenant_id, object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.uploading")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return ObjectHead(byte_size=len(data), sha256=hashlib.sha256(data).hexdigest())


class S3CompatibleStorageClient:
    """S3-compatible object storage client using AWS Signature Version 4.

    The client intentionally has no SDK dependency. Presigned PUT URLs keep
    file bytes out of the API process; worker reads use signed GET requests.
    The endpoint, bucket and credentials are supplied by the runtime only and
    are never serialized into an API response or persisted in the database.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        path_style: bool = True,
    ) -> None:
        from ekb_api.core.config import _is_remote_provider_url

        if not _is_remote_provider_url(endpoint):
            raise ObjectStorageUnavailable("object storage endpoint must be a remote HTTP URL")
        if not bucket or not access_key or not secret_key:
            raise ObjectStorageUnavailable("object storage bucket and credentials are required")
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.region = region or "us-east-1"
        self.access_key = access_key
        self.secret_key = secret_key
        self.path_style = bool(path_style)

    def _url(self, object_key: str) -> str:
        if not object_key or ".." in Path(object_key).parts:
            raise ObjectStorageUnavailable("invalid object key")
        parts = urlsplit(self.endpoint)
        encoded_key = quote(object_key, safe="/-_.~")
        if self.path_style:
            path = f"{parts.path.rstrip('/')}/{quote(self.bucket, safe='/-_.~')}/{encoded_key}"
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        host = f"{self.bucket}.{parts.netloc}"
        path = f"{parts.path.rstrip('/')}/{encoded_key}"
        return urlunsplit((parts.scheme, host, path, "", ""))

    @staticmethod
    def _payload_hash(data: bytes | None) -> str:
        return hashlib.sha256(data or b"").hexdigest()

    def _signature(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        payload_hash: str,
        query: list[tuple[str, str]] | None = None,
        presign_expires: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        short_date = now.strftime("%Y%m%d")
        parsed = urlsplit(url)
        query_items = list(query or [])
        host = parsed.netloc
        signed_headers = {
            key.lower(): " ".join(value.strip().split()) for key, value in headers.items()
        }
        signed_headers.setdefault("host", host)
        signed_names = ";".join(sorted(signed_headers))
        canonical_headers = "".join(
            f"{key}:{signed_headers[key]}\n" for key in sorted(signed_headers)
        )
        canonical_uri = quote(parsed.path or "/", safe="/-_.~")
        canonical_query = "&".join(
            f"{quote(str(key), safe='-_.~')}={quote(str(value), safe='-_.~')}"
            for key, value in sorted(query_items)
        )
        scope = f"{short_date}/{self.region}/s3/aws4_request"
        if presign_expires is not None:
            query_items.extend(
                [
                    ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
                    ("X-Amz-Credential", f"{self.access_key}/{scope}"),
                    ("X-Amz-Date", amz_date),
                    ("X-Amz-Expires", str(presign_expires)),
                    ("X-Amz-SignedHeaders", signed_names),
                ]
            )
            canonical_query = "&".join(
                f"{quote(str(key), safe='-_.~')}={quote(str(value), safe='-_.~')}"
                for key, value in sorted(query_items)
            )
            canonical_request = "\n".join(
                [
                    method,
                    canonical_uri,
                    canonical_query,
                    canonical_headers,
                    signed_names,
                    "UNSIGNED-PAYLOAD",
                ]
            )
        else:
            headers = {**headers, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash}
            signed_headers = {
                key.lower(): " ".join(value.strip().split()) for key, value in headers.items()
            }
            signed_headers.setdefault("host", host)
            signed_names = ";".join(sorted(signed_headers))
            canonical_headers = "".join(
                f"{key}:{signed_headers[key]}\n" for key in sorted(signed_headers)
            )
            canonical_request = "\n".join(
                [
                    method,
                    canonical_uri,
                    canonical_query,
                    canonical_headers,
                    signed_names,
                    payload_hash,
                ]
            )
        hashed_request = hashlib.sha256(canonical_request.encode()).hexdigest()
        string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashed_request])

        def signing_key() -> bytes:
            date_key = hmac.new(
                f"AWS4{self.secret_key}".encode(), short_date.encode(), hashlib.sha256
            ).digest()
            region_key = hmac.new(date_key, self.region.encode(), hashlib.sha256).digest()
            service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
            return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()

        signature = hmac.new(signing_key(), string_to_sign.encode(), hashlib.sha256).hexdigest()
        if presign_expires is not None:
            query_items.append(("X-Amz-Signature", signature))
            return urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urlencode(query_items), parsed.fragment)
            ), {}
        signed = dict(headers)
        signed["Host"] = host
        signed["x-amz-date"] = amz_date
        signed["x-amz-content-sha256"] = payload_hash
        signed["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}"
        )
        return url, signed

    def _request(
        self,
        method: str,
        *,
        object_key: str,
        data: bytes | None = None,
    ) -> bytes:
        import urllib.error
        import urllib.request

        url = self._url(object_key)
        payload = data or b""
        url, headers = self._signature(
            method=method,
            url=url,
            headers={"host": urlsplit(url).netloc},
            payload_hash=self._payload_hash(payload),
        )
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ObjectStorageUnavailable("object storage request failed") from exc

    def put_presigned(
        self,
        *,
        tenant_id: str,
        object_key: str,
        byte_size: int,
        method: str,
        part_size: Optional[int],
        expires_in_seconds: int,
    ) -> PresignedUpload:
        if method != "SINGLE":
            raise ObjectStorageUnavailable("multipart upload is not enabled for this endpoint")
        url = self._url(object_key)
        presigned, _ = self._signature(
            method="PUT",
            url=url,
            headers={"host": urlsplit(url).netloc},
            payload_hash="UNSIGNED-PAYLOAD",
            presign_expires=max(1, min(int(expires_in_seconds), 604800)),
        )
        return PresignedUpload(None, [presigned], None)

    def complete_multipart(
        self, *, tenant_id: str, object_key: str, provider_upload_id: str, parts: list[dict]
    ) -> None:
        raise ObjectStorageUnavailable("multipart upload is not enabled for this endpoint")

    def abort(self, *, tenant_id: str, object_key: str, provider_upload_id: str) -> None:
        self._request("DELETE", object_key=object_key)

    def get_object_url(self, *, tenant_id: str, object_key: str) -> str:
        return self.put_presigned(
            tenant_id=tenant_id,
            object_key=object_key,
            byte_size=0,
            method="SINGLE",
            part_size=None,
            expires_in_seconds=DEFAULT_SESSION_TTL_SECONDS,
        ).upload_urls[0]

    def get_object_bytes(self, *, tenant_id: str, object_key: str) -> bytes:
        return self._request("GET", object_key=object_key)

    def head_object(self, *, tenant_id: str, object_key: str) -> ObjectHead:
        # S3 ETags are not a portable SHA-256 (multipart ETags are not even a
        # content hash), so read and hash the bytes for an authoritative check.
        data = self.get_object_bytes(tenant_id=tenant_id, object_key=object_key)
        return ObjectHead(len(data), hashlib.sha256(data).hexdigest())

@runtime_checkable
class StorageClient(Protocol):
    def put_presigned(
        self,
        *,
        tenant_id: str,
        object_key: str,
        byte_size: int,
        method: str,
        part_size: Optional[int],
        expires_in_seconds: int,
    ) -> PresignedUpload: ...

    def complete_multipart(
        self, *, tenant_id: str, object_key: str, provider_upload_id: str, parts: list[dict]
    ) -> None: ...

    def abort(self, *, tenant_id: str, object_key: str, provider_upload_id: str) -> None: ...

    def get_object_url(self, *, tenant_id: str, object_key: str) -> str: ...

    def get_object_bytes(self, *, tenant_id: str, object_key: str) -> bytes: ...

    def head_object(self, *, tenant_id: str, object_key: str) -> ObjectHead: ...


def build_storage_client() -> StorageClient:
    """Construct the configured object store client.

    Fails closed with :class:`ObjectStorageUnavailable` when no S3-compatible
    endpoint is configured — uploads must never silently succeed.
    """
    from ekb_api.core.config import get_settings

    settings = get_settings()
    local_root = os.getenv("EKB_OBJECT_STORAGE_LOCAL_ROOT", "").strip()
    if settings.environment == "development" and local_root:
        return LocalFilesystemStorageClient(local_root)
    endpoint = getattr(settings, "object_storage_endpoint", None) or ""
    if not endpoint:
        raise ObjectStorageUnavailable(
            "object storage is not configured; uploads cannot be accepted"
        )
    try:
        return S3CompatibleStorageClient(
            endpoint=endpoint,
            bucket=str(getattr(settings, "object_storage_bucket", "") or ""),
            region=str(getattr(settings, "object_storage_region", "us-east-1") or "us-east-1"),
            access_key=str(getattr(settings, "object_storage_access_key", "") or ""),
            secret_key=str(getattr(settings, "object_storage_secret_key", "") or ""),
            path_style=bool(getattr(settings, "object_storage_path_style", True)),
        )
    except ObjectStorageUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - boundary must fail closed
        raise ObjectStorageUnavailable("object storage client construction failed") from exc


# ---- Path normalization ----------------------------------------------------


def normalize_relative_path(relative_path: str) -> str:
    """Normalize a client-supplied relative path into the logical identity.

    Rejects traversal (``..``), absolute paths, empty segments, NUL/control
    characters and platform-reserved names.  Unicode is NFC-normalized.  Raises
    :class:`PathValidationError` (code ``PATH_INVALID`` / ``PATH_TOO_DEEP``).
    """
    if relative_path is None:
        raise PathValidationError("PATH_INVALID", "路径不能为空")
    raw = unicodedata.normalize("NFC", str(relative_path))
    if not raw or raw.startswith("/") or raw.startswith("\\"):
        raise PathValidationError("PATH_INVALID", "绝对路径不被允许")
    # Normalize back-slashes to forward slashes.
    raw = raw.replace("\\", "/")
    segments = raw.split("/")
    normalized: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            raise PathValidationError("PATH_INVALID", "路径不允许包含上级目录引用")
        if "\x00" in segment:
            raise PathValidationError("PATH_INVALID", "路径包含非法字符")
        if any(ord(ch) < 0x20 for ch in segment):
            raise PathValidationError("PATH_INVALID", "路径包含控制字符")
        upper = segment.split(".")[0].upper()
        if upper in _RESERVED_NAMES:
            raise PathValidationError("PATH_INVALID", f"路径段是平台保留名: {segment}")
        normalized.append(segment)
    if not normalized:
        raise PathValidationError("PATH_INVALID", "路径不能为空")
    if len(normalized) > MAX_PATH_DEPTH:
        raise PathValidationError("PATH_TOO_DEEP", f"路径深度超过上限 {MAX_PATH_DEPTH}")
    return "/".join(normalized)


def _object_key(tenant_id: str, kb_id: str, item_id: str) -> str:
    return f"uploads/{tenant_id}/{kb_id}/{item_id}"


def _source_object_insert(dialect: str) -> str:
    """Idempotent source-object insert; the unique key is (tenant_id, object_key)."""

    statement = (
        "INSERT INTO source_objects (id, tenant_id, object_key, sha256, byte_size, "
        "detected_mime, ref_count, created_at) VALUES "
        "(:id, :tenant, :key, :sha, :size, :mime, 0, :now)"
    )
    if dialect == "postgresql":
        return statement + " ON CONFLICT (tenant_id, object_key) DO NOTHING"
    return statement.replace("INSERT INTO source_objects", "INSERT OR IGNORE INTO source_objects")


# ---- Result dataclasses ---------------------------------------------------


@dataclass
class PreflightResult:
    client_item_id: str
    accepted: bool
    normalized_relative_path: Optional[str]
    display_path: Optional[str]
    byte_size: int
    detected_mime: Optional[str]
    upload_item_id: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[dict] = None
    upload_session: Optional[dict] = None


@dataclass
class BatchResult:
    batch_id: str
    status: str
    created: bool
    items: list[PreflightResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.batch_id,
            "batch_id": self.batch_id,
            "status": self.status,
            "created": self.created,
            "items": [vars(item) for item in self.items],
        }


@dataclass
class CompleteResult:
    version_id: str
    document_id: str
    ingest_job_id: str
    is_new: bool
    status: str


@dataclass
class ItemProjection:
    id: str
    client_item_id: str
    relative_path: str
    status: str
    progress: Optional[dict]
    version_id: Optional[str]
    job_id: Optional[str]
    error: Optional[dict]
    source_status: str
    byte_size: int
    uploaded_bytes: int
    stage: Optional[str]
    attempt_id: Optional[str]
    attempt_no: Optional[int]
    attempts: int


@dataclass
class BatchProjection:
    id: str
    kb_id: str
    mode: str
    status: str
    item_count: int
    total_bytes: int
    created_at: str
    updated_at: str
    items: list[ItemProjection] = field(default_factory=list)
    source_status: str = ""
    counts: dict[str, int] = field(default_factory=dict)


# ---- Upload service -------------------------------------------------------


class UploadService:
    def __init__(
        self,
        engine: Optional[Engine] = None,
        *,
        storage_client: Optional[StorageClient] = None,
        job_service: Optional[JobService] = None,
    ) -> None:
        self.engine = engine or get_engine()
        if storage_client is None:
            storage_client = build_storage_client()
        self.storage: StorageClient = storage_client
        self.jobs = job_service or get_job_service(self.engine)

    # -- helpers --

    def _require_kb(self, connection, tenant_id: str, kb_id: str) -> dict:
        row = connection.execute(
            text(
                "SELECT id, tenant_id, embedding_profile_id, active_index_generation_id "
                "FROM knowledge_bases WHERE id=:kb AND tenant_id=:tenant"
            ),
            {"kb": kb_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "知识库不存在或不在当前租户")
        return row

    def _require_item(self, connection, tenant_id: str, item_id: str) -> dict:
        row = connection.execute(
            text(
                "SELECT ui.*, ub.tenant_id AS batch_tenant, ub.knowledge_base_id, "
                "ub.created_by AS created_by "
                "FROM upload_items AS ui "
                "JOIN upload_batches AS ub ON ub.id = ui.batch_id "
                "WHERE ui.id=:item AND ub.tenant_id=:tenant"
            ),
            {"item": item_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "上传项不存在或不在当前租户")
        return row

    # -- preflight --

    def preflight_item(
        self,
        *,
        tenant_id: str,
        client_item_id: str,
        relative_path: str,
        byte_size: int,
        browser_mime: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> PreflightResult:
        from ekb_api.services.parsers.registry import (
            FILE_EMPTY,
            FILE_TOO_LARGE,
            PATH_INVALID,
            PATH_TOO_DEEP,
        )

        if byte_size <= 0:
            return PreflightResult(
                client_item_id=client_item_id,
                accepted=False,
                normalized_relative_path=None,
                display_path=relative_path,
                byte_size=byte_size,
                detected_mime=browser_mime,
                error_code=FILE_EMPTY,
                error_detail={"byte_size": byte_size, "detected_mime": browser_mime},
            )
        if byte_size > MAX_FILE_BYTES:
            return PreflightResult(
                client_item_id=client_item_id,
                accepted=False,
                normalized_relative_path=None,
                display_path=relative_path,
                byte_size=byte_size,
                detected_mime=browser_mime,
                error_code=FILE_TOO_LARGE,
                error_detail={"byte_size": byte_size, "limit": MAX_FILE_BYTES},
            )
        try:
            normalized = normalize_relative_path(relative_path)
        except PathValidationError as exc:
            code = exc.code if exc.code in (PATH_TOO_DEEP,) else PATH_INVALID
            return PreflightResult(
                client_item_id=client_item_id,
                accepted=False,
                normalized_relative_path=None,
                display_path=relative_path,
                byte_size=byte_size,
                detected_mime=browser_mime,
                error_code=code,
                error_detail={"relative_path": relative_path[:512], "reason": exc.message},
            )
        return PreflightResult(
            client_item_id=client_item_id,
            accepted=True,
            normalized_relative_path=normalized,
            display_path=relative_path,
            byte_size=byte_size,
            detected_mime=browser_mime,
            error_code=None,
        )

    # -- batch create / idempotency --

    def create_batch(
        self,
        *,
        tenant_id: str,
        created_by: str,
        kb_id: str,
        mode: str,
        client_request_id: str,
        items: list[dict],
    ) -> BatchResult:
        from ekb_api.services.parsers.registry import BATCH_LIMIT_EXCEEDED

        if mode not in ("FILE", "MULTI_FILE", "DIRECTORY"):
            raise ApiError(400, "INVALID_MODE", "不支持的上传模式")
        if not items:
            raise ApiError(400, "EMPTY_BATCH", "批次不能没有文件")

        with self.engine.begin() as connection:
            self._require_kb(connection, tenant_id, kb_id)
            existing = connection.execute(
                text(
                    "SELECT id FROM upload_batches "
                    "WHERE tenant_id=:tenant AND created_by=:user AND client_request_id=:cr"
                ),
                {"tenant": tenant_id, "user": created_by, "cr": client_request_id},
            ).first()
            if existing is not None:
                return self.get_batch(tenant_id=tenant_id, batch_id=str(existing.id))

            preflights = [
                self.preflight_item(
                    tenant_id=tenant_id,
                    client_item_id=str(it.get("client_item_id", "")),
                    relative_path=str(it.get("relative_path", "")),
                    byte_size=int(it.get("byte_size", 0)),
                    browser_mime=it.get("browser_mime"),
                    sha256=it.get("sha256"),
                )
                for it in items
            ]
            accepted = [p for p in preflights if p.accepted]
            if len(items) > MAX_BATCH_FILES:
                raise ApiError(
                    400,
                    BATCH_LIMIT_EXCEEDED,
                    f"批次文件数超过上限 {MAX_BATCH_FILES}",
                    {"limit": MAX_BATCH_FILES, "actual": len(items)},
                )
            total_bytes = sum(p.byte_size for p in accepted)
            if total_bytes > MAX_BATCH_BYTES:
                raise ApiError(
                    400,
                    BATCH_LIMIT_EXCEEDED,
                    f"批次总大小超过上限 {MAX_BATCH_BYTES}",
                    {"limit": MAX_BATCH_BYTES, "actual": total_bytes},
                )

            from uuid import uuid4

            from ekb_api.domain import utc_now

            now = utc_now()
            batch_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO upload_batches "
                    "(id, tenant_id, knowledge_base_id, created_by, mode, status, "
                    "client_request_id, item_count, total_bytes, created_at, updated_at) "
                    "VALUES (:id, :tenant, :kb, :user, :mode, 'ACCEPTED', :cr, "
                    ":count, :total, :created, :updated)"
                ),
                {
                    "id": batch_id,
                    "tenant": tenant_id,
                    "kb": kb_id,
                    "user": created_by,
                    "mode": mode,
                    "cr": client_request_id,
                    "count": len(accepted),
                    "total": total_bytes,
                    "created": now,
                    "updated": now,
                },
            )
            result_items: list[PreflightResult] = []
            for preflight, original in zip(preflights, items):
                item_id = str(uuid4())
                preflight.upload_item_id = item_id
                sha256 = original.get("sha256")
                connection.execute(
                    text(
                        "INSERT INTO upload_items "
                        "(id, batch_id, client_item_id, display_path, normalized_relative_path, "
                        "byte_size, expected_sha256, status, uploaded_bytes) "
                        "VALUES (:id, :batch, :cid, :display, :norm, :size, :sha, "
                        ":status, 0)"
                    ),
                    {
                        "id": item_id,
                        "batch": batch_id,
                        "cid": preflight.client_item_id,
                        "display": preflight.display_path,
                        "norm": preflight.normalized_relative_path or preflight.display_path,
                        "size": preflight.byte_size,
                        "sha": sha256,
                        "status": "WAITING" if preflight.accepted else "REJECTED",
                    },
                )
                if preflight.accepted:
                    preflight.upload_session = self._open_session(
                        connection,
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        item_id=item_id,
                        byte_size=preflight.byte_size,
                        detected_mime=preflight.detected_mime,
                    ).__dict__
                result_items.append(preflight)

        return BatchResult(batch_id=batch_id, status="ACCEPTED", created=True, items=result_items)

    # -- upload session --

    def _open_session(
        self,
        connection,
        *,
        tenant_id: str,
        kb_id: str,
        item_id: str,
        byte_size: int,
        detected_mime: Optional[str],
        method: str = "SINGLE",
        part_size: Optional[int] = None,
    ):
        from uuid import uuid4

        from ekb_api.domain import utc_now

        existing = connection.execute(
            text("SELECT * FROM upload_sessions WHERE upload_item_id=:item"),
            {"item": item_id},
        ).first()
        if existing is not None:
            object_key = _object_key(tenant_id, kb_id, item_id)
            return _SessionRecord(
                session_id=str(existing.id),
                method=str(existing.method),
                provider_upload_id=existing.provider_upload_id,
                upload_urls=[
                    self.storage.get_object_url(
                        tenant_id=tenant_id,
                        object_key=object_key,
                    )
                ],
                part_size=existing.part_size,
                expires_at=str(existing.expires_at),
            )
        object_key = _object_key(tenant_id, kb_id, item_id)
        presigned = self.storage.put_presigned(
            tenant_id=tenant_id,
            object_key=object_key,
            byte_size=byte_size,
            method=method,
            part_size=part_size,
            expires_in_seconds=DEFAULT_SESSION_TTL_SECONDS,
        )
        now = utc_now()
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_SESSION_TTL_SECONDS))
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        session_id = str(uuid4())
        connection.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, tenant_id, upload_item_id, method, provider_upload_id, state, "
                "part_size, expires_at, created_at, updated_at) "
                "VALUES (:id, :tenant, :item, :method, :puid, 'ACTIVE', :part, "
                ":expires, :created, :updated)"
            ),
            {
                "id": session_id,
                "tenant": tenant_id,
                "item": item_id,
                "method": method,
                "puid": presigned.provider_upload_id,
                "part": part_size,
                "expires": expires_at,
                "created": now,
                "updated": now,
            },
        )
        connection.execute(
            text("UPDATE upload_items SET status='UPLOADING' WHERE id=:item"),
            {"item": item_id},
        )
        return _SessionRecord(
            session_id=session_id,
            method=method,
            provider_upload_id=presigned.provider_upload_id,
            upload_urls=list(presigned.upload_urls),
            part_size=part_size,
            expires_at=expires_at,
        )

    def open_session(
        self,
        *,
        tenant_id: str,
        item_id: str,
        method: str = "SINGLE",
        part_size: Optional[int] = None,
    ) -> dict:
        with self.engine.begin() as connection:
            item = self._require_item(connection, tenant_id, item_id)
            return self._open_session(
                connection,
                tenant_id=tenant_id,
                kb_id=str(item.knowledge_base_id),
                item_id=item_id,
                byte_size=int(item.byte_size),
                detected_mime=item.detected_mime if hasattr(item, "detected_mime") else None,
                method=method,
                part_size=part_size,
            ).__dict__

    # -- complete upload --

    def complete_upload(
        self,
        *,
        tenant_id: str,
        item_id: str,
        sha256: str,
        detected_mime: Optional[str] = None,
    ) -> CompleteResult:
        from ekb_api.services.ingestion import create_version_for_item
        from ekb_api.services.parsers.registry import OBJECT_CHECKSUM_MISMATCH

        with self.engine.begin() as connection:
            item = self._require_item(connection, tenant_id, item_id)
            kb_id = str(item.knowledge_base_id)
            object_key = _object_key(tenant_id, kb_id, item_id)
            session = connection.execute(
                text(
                    "SELECT state, expires_at FROM upload_sessions "
                    "WHERE upload_item_id=:item AND tenant_id=:tenant"
                ),
                {"item": item_id, "tenant": tenant_id},
            ).first()
            if session is None or (
                str(session.state) not in ("ACTIVE", "COMPLETED")
                and str(item.status) not in ("COMPLETING", "COMPLETED")
            ):
                raise ApiError(409, "UPLOAD_SESSION_INVALID", "上传会话无效，请重新上传")
            if str(session.state) == "ACTIVE" and str(session.expires_at) <= utc_now():
                raise ApiError(409, "UPLOAD_SESSION_EXPIRED", "上传会话已过期，请重新上传")

            # Idempotency: already completed -> re-establish (no-op) version.
            if str(item.status) in ("COMPLETED", "COMPLETING"):
                version_row = connection.execute(
                    text(
                        "SELECT id, doc_id FROM document_versions "
                        "WHERE source_object_id=:source AND tenant_id=:tenant "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"source": item.source_object_id, "tenant": tenant_id},
                ).first()
                if version_row is not None:
                    job_row = connection.execute(
                        text(
                            "SELECT id FROM ingest_jobs WHERE document_version_id=:dv "
                            "AND tenant_id=:tenant LIMIT 1"
                        ),
                        {"dv": str(version_row.id), "tenant": tenant_id},
                    ).first()
                    return CompleteResult(
                        version_id=str(version_row.id),
                        document_id="",
                        ingest_job_id=str(job_row.id) if job_row else "",
                        is_new=False,
                        status="COMPLETED",
                    )

            # Verify the stored object exists and matches the client checksum.
            try:
                head = self.storage.head_object(tenant_id=tenant_id, object_key=object_key)
            except Exception as exc:  # noqa: BLE001
                raise ApiError(
                    422,
                    OBJECT_CHECKSUM_MISMATCH,
                    "无法读取已上传对象，请重新上传",
                    {"reason": "object-unavailable"},
                ) from exc
            if sha256 and head.sha256 is not None and head.sha256 != sha256:
                connection.execute(
                    text(
                        "UPDATE upload_items SET status='FAILED', error_code=:code WHERE id=:item"
                    ),
                    {"code": OBJECT_CHECKSUM_MISMATCH, "item": item_id},
                )
                raise ApiError(
                    422,
                    OBJECT_CHECKSUM_MISMATCH,
                    "对象校验和不匹配，请重新上传",
                    {
                        "expected_sha256": sha256[:64],
                        "actual_sha256": str(head.sha256)[:64],
                    },
                )
            if sha256 and int(item.byte_size) != int(head.byte_size):
                raise ApiError(
                    422,
                    OBJECT_CHECKSUM_MISMATCH,
                    "对象大小不匹配",
                    {"expected": int(item.byte_size), "actual": int(head.byte_size)},
                )

            # Record / reuse the source object (content dedup within tenant).
            from uuid import uuid4

            source_id = str(uuid4())
            mime = detected_mime or "application/octet-stream"
            connection.execute(
                text(_source_object_insert(self.engine.dialect.name)),
                {
                    "id": source_id,
                    "tenant": tenant_id,
                    "key": object_key,
                    "sha": sha256,
                    "size": int(head.byte_size),
                    "mime": mime,
                    "now": utc_now(),
                },
            )
            source_row = connection.execute(
                text("SELECT id FROM source_objects WHERE tenant_id=:tenant AND object_key=:key"),
                {"tenant": tenant_id, "key": object_key},
            ).first()
            source_id = str(source_row.id)

            connection.execute(
                text(
                    "UPDATE upload_items SET status='UPLOADED', source_object_id=:src, "
                    "uploaded_bytes=:size WHERE id=:item"
                ),
                {"src": source_id, "size": int(head.byte_size), "item": item_id},
            )
            connection.execute(
                text("UPDATE upload_sessions SET state='COMPLETED' WHERE upload_item_id=:item"),
                {"item": item_id},
            )

            outcome = create_version_for_item(
                connection,
                tenant_id=tenant_id,
                kb_id=kb_id,
                normalized_relative_path=str(item.normalized_relative_path),
                display_path=str(item.display_path),
                sha256=sha256,
                byte_size=int(head.byte_size),
                detected_mime=mime,
                source_object_id=source_id,
                job_service=self.jobs,
                owner_user_id=str(item.created_by) if getattr(item, "created_by", None) else None,
            )
            connection.execute(
                text("UPDATE upload_items SET status='COMPLETING' WHERE id=:item"),
                {"item": item_id},
            )
            return CompleteResult(
                version_id=outcome["version_id"],
                document_id=outcome["document_id"],
                ingest_job_id=outcome["ingest_job_id"],
                is_new=outcome["is_new"],
                status="COMPLETING",
            )

    # -- abort --

    def abort_session(self, *, tenant_id: str, item_id: str) -> None:
        with self.engine.begin() as connection:
            item = self._require_item(connection, tenant_id, item_id)
            session = connection.execute(
                text("SELECT * FROM upload_sessions WHERE upload_item_id=:item"),
                {"item": item_id},
            ).first()
            kb_id = str(item.knowledge_base_id)
            object_key = _object_key(tenant_id, kb_id, item_id)
            if session is not None:
                puid = session.provider_upload_id
                if puid:
                    try:
                        self.storage.abort(
                            tenant_id=tenant_id,
                            object_key=object_key,
                            provider_upload_id=str(puid),
                        )
                    except Exception:  # noqa: BLE001 - abort best-effort
                        pass
                connection.execute(
                    text("UPDATE upload_sessions SET state='ABORTED' WHERE id=:sid"),
                    {"sid": str(session.id)},
                )
            connection.execute(
                text("UPDATE upload_items SET status='ABORTED' WHERE id=:item"),
                {"item": item_id},
            )

    # -- projections --

    def get_batch(self, *, tenant_id: str, batch_id: str) -> BatchProjection:
        with self.engine.connect() as connection:
            batch = connection.execute(
                text("SELECT * FROM upload_batches WHERE id=:id AND tenant_id=:tenant"),
                {"id": batch_id, "tenant": tenant_id},
            ).first()
            if batch is None:
                raise ApiError(404, "NOT_FOUND", "批次不存在或不在当前租户")
            item_rows = connection.execute(
                text("SELECT * FROM upload_items WHERE batch_id=:batch ORDER BY client_item_id"),
                {"batch": batch_id},
            ).all()
            items = [
                self._item_projection(connection, row, tenant_id=tenant_id) for row in item_rows
            ]
            item_statuses = [item.status for item in items]
            counts = {status: item_statuses.count(status) for status in (
                "queued", "uploading", "verifying", "processing", "indexing",
                "ready", "failed", "cancelled",
            )}
            if counts["failed"]:
                batch_status = "failed"
            elif item_statuses and all(status == "ready" for status in item_statuses):
                batch_status = "ready"
            elif counts["cancelled"] and sum(counts.values()) == counts["cancelled"]:
                batch_status = "cancelled"
            elif counts["indexing"]:
                batch_status = "indexing"
            elif counts["processing"]:
                batch_status = "processing"
            elif counts["verifying"]:
                batch_status = "verifying"
            elif counts["uploading"]:
                batch_status = "uploading"
            else:
                batch_status = "queued"
        return BatchProjection(
            id=str(batch.id),
            kb_id=str(batch.knowledge_base_id),
            mode=str(batch.mode),
            status=batch_status,
            item_count=int(batch.item_count),
            total_bytes=int(batch.total_bytes),
            created_at=str(batch.created_at),
            updated_at=str(batch.updated_at),
            items=items,
            source_status=str(batch.status),
            counts=counts,
        )

    def list_batches(self, *, tenant_id: str, limit: int = 30) -> list[BatchProjection]:
        """Return the most recently updated upload batches for one tenant."""

        bounded_limit = max(1, min(100, int(limit)))
        with self.engine.connect() as connection:
            batch_ids = connection.execute(
                text(
                    "SELECT id FROM upload_batches "
                    "WHERE tenant_id=:tenant "
                    "ORDER BY updated_at DESC, created_at DESC, id DESC "
                    "LIMIT :limit"
                ),
                {"tenant": tenant_id, "limit": bounded_limit},
            ).scalars().all()
        return [
            self.get_batch(tenant_id=tenant_id, batch_id=str(batch_id))
            for batch_id in batch_ids
        ]

    def _item_projection(self, connection, item_row, *, tenant_id: str) -> ItemProjection:
        version_id = None
        job_id = None
        progress = None
        error = None
        stage = None
        attempt_id = None
        attempt_no = None
        attempts = 0
        job_status = None
        if item_row.source_object_id is not None:
            dv = connection.execute(
                text(
                    "SELECT id FROM document_versions WHERE source_object_id=:src "
                    "AND tenant_id=:tenant ORDER BY created_at DESC LIMIT 1"
                ),
                {"src": str(item_row.source_object_id), "tenant": tenant_id},
            ).first()
            if dv is not None:
                version_id = str(dv.id)
                job = connection.execute(
                    text(
                        "SELECT id, status, attempts FROM ingest_jobs "
                        "WHERE document_version_id=:dv AND tenant_id=:tenant LIMIT 1"
                    ),
                    {"dv": str(dv.id), "tenant": tenant_id},
                ).first()
                if job is not None:
                    job_id = str(job.id)
                    job_status = str(job.status).upper()
                    attempts = int(job.attempts or 0)
        if item_row.error_code is not None or job_status == "FAILED":
            error_code = str(item_row.error_code or "INGEST_FAILED")
            error_detail = _json_value(getattr(item_row, "error_detail", None))
            error = {
                "code": error_code,
                "message": "上传或摄取失败",
                "retryable": _is_retryable_error(error_code),
            }
            if isinstance(error_detail, dict):
                from ekb_api.services.parsers.registry import sanitize_detail

                error["detail"] = sanitize_detail(error_detail)
        # progress from the active/last attempt of the ingest job
        if job_id is not None:
            attempt = connection.execute(
                text(
                    "SELECT id, attempt_no, state, current_stage, progress_current, progress_total, "
                    "error_code, sanitized_error "
                    "FROM ingest_job_attempts WHERE ingest_job_id=:job AND tenant_id=:tenant "
                    "ORDER BY attempt_no DESC LIMIT 1"
                ),
                {"job": job_id, "tenant": tenant_id},
            ).first()
            if attempt is not None:
                attempt_id = str(attempt.id)
                attempt_no = int(attempt.attempt_no)
                stage = str(attempt.current_stage) if attempt.current_stage else None
                unit = _progress_unit(str(attempt.current_stage))
                progress = {
                    "unit": unit,
                    "current": int(attempt.progress_current or 0),
                    "total": int(attempt.progress_total or 0),
                    "stage": stage,
                }
                if attempt.error_code and error is None:
                    error = {
                        "code": str(attempt.error_code),
                        "message": "摄取失败",
                        "retryable": _is_retryable_error(str(attempt.error_code)),
                    }
                if attempt.sanitized_error and error is not None:
                    detail = _json_value(attempt.sanitized_error)
                    if isinstance(detail, dict):
                        error["detail"] = detail
        raw_status = str(item_row.status).upper()
        status = _canonical_upload_status(
            raw_status,
            job_status=job_status,
            stage=stage,
            has_job=job_id is not None,
        )
        if status in {"uploading", "verifying"}:
            progress = {
                "unit": "bytes",
                "current": int(item_row.uploaded_bytes or 0),
                "total": int(item_row.byte_size or 0),
                "stage": status.upper(),
            }
        return ItemProjection(
            id=str(item_row.id),
            client_item_id=str(item_row.client_item_id),
            relative_path=str(item_row.normalized_relative_path),
            status=status,
            progress=progress,
            version_id=version_id,
            job_id=job_id,
            error=error,
            source_status=raw_status,
            byte_size=int(item_row.byte_size or 0),
            uploaded_bytes=int(item_row.uploaded_bytes or 0),
            stage=stage,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            attempts=attempts,
        )


@dataclass
class _SessionRecord:
    session_id: str
    method: str
    provider_upload_id: Optional[str]
    upload_urls: list[str]
    part_size: Optional[int]
    expires_at: str


def _progress_unit(stage: str) -> str:
    return {
        "VALIDATING": "bytes",
        "CONVERTING": "pages",
        "PARSING": "pages",
        "CHUNKING": "chunks",
        "EMBEDDING": "chunks",
        "INDEXING": "vectors",
    }.get(stage, "units")


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _is_retryable_error(code: str) -> bool:
    from ekb_api.services.parsers.registry import is_retryable

    return bool(is_retryable(code))


def _canonical_upload_status(
    raw_status: str,
    *,
    job_status: Optional[str],
    stage: Optional[str],
    has_job: bool,
) -> str:
    if raw_status in {"ABORTED", "CANCELLED"} or job_status == "CANCELLED":
        return "cancelled"
    if raw_status in {"REJECTED", "FAILED"}:
        return "failed"
    if job_status == "SUCCEEDED" or raw_status == "COMPLETED":
        return "ready"
    if job_status == "FAILED":
        return "failed"
    if stage == "INDEXING":
        return "indexing"
    if has_job or job_status in {"QUEUED", "RUNNING"} or raw_status in {"UPLOADED", "COMPLETING"}:
        return "processing"
    if raw_status == "UPLOADING":
        return "uploading"
    if raw_status in {"WAITING", "ACCEPTED"}:
        return "queued"
    return "verifying"
