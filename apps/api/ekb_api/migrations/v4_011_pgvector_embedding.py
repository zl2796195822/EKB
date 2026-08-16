"""pgvector 原生 embedding 列迁移 (v4_011).

把 ``chunks.embedding`` 从 JSON/JSONB 降级列切换为 PostgreSQL 原生
``VECTOR`` 列，并建立 HNSW 余弦索引 ``ix_chunks_embedding_hnsw``，
使检索可从 Python 层余弦切换到数据库内 ANN。

前置条件（v4_010 已验证）：

* ``chunks`` 表已存在，``embedding`` 是 JSON/JSONB 列。
* 生产库中存在历史向量（1024 维）与少量 JSON null（``'null'`` 文本值）。

方言行为：

* **PostgreSQL**：先把 JSON null / 非数组文本归一到 SQL NULL
  （``VECTOR`` 不接受 ``null`` 文本），再
  ``ALTER COLUMN embedding TYPE vector USING embedding::vector``
  （显式 cast 走 text I/O 路径，json/jsonb 均可用），随后建 HNSW 索引。
* **SQLite**：NO-OP，保留 JSON 列（测试/本地开发仍走 Python 层余弦），
  但同样记录 provenance，保证 checksum/链一致。

回滚：dry-run only。HNSW 索引可 drop，但 ``vector`` 列类型不可安全降级
（ORM 的 ``EmbeddingVector`` 在 SQLite 下按 JSON 读，PostgreSQL 下由
pgvector 驱动编解码，因此降级仍可被 ORM 兼容读取；禁止自动 DDL 降级）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_010_conversation_engine_unification import V4_010_CHECKSUM

VERSION = "v4_011_pgvector_embedding"
V4_011_OWNED_TABLES = ()
V4_011_ROLLBACK_POLICY = (
    "dry-run only; the HNSW index may be dropped, but the vector column type "
    "is not downgradable (JSON reads still work via the EmbeddingVector ORM "
    "type); use a forward-fix to evolve the vector contract"
)

# ---- Canonical DDL (PostgreSQL target; SQLite apply is a NO-OP) ----

# 归一 JSON null（文本 'null'）与非数组残值 → SQL NULL。VECTOR 的输入
# 函数不接受 null 文本，必须先清理再 ALTER TYPE。
_NULL_PURGE_DDL = (
    "UPDATE chunks SET embedding = NULL "
    "WHERE embedding IS NOT NULL AND embedding::text NOT LIKE '[%'"
)

_ALTER_VECTOR_DDL = (
    "ALTER TABLE chunks ALTER COLUMN embedding TYPE vector USING embedding::vector"
)

_HNSW_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
    "ON chunks USING hnsw (embedding vector_cosine_ops)"
)

# Canonical DDL 用于推导迁移 checksum（模块加载不变式）。
_CANONICAL_DDL = "\n".join([_NULL_PURGE_DDL, _ALTER_VECTOR_DDL, _HNSW_INDEX_DDL])

_REQUIRED_ALTERS = {
    "chunks": {"embedding"},
}
_REQUIRED_INDEXES = {"ix_chunks_embedding_hnsw"}

V4_011_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_CANONICAL_DDL.split())
        + "\nrollback="
        + V4_011_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:VECTOR"
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def _ddl_for(statement: str, dialect: str) -> str:
    """方言分派：PG 返回原生 DDL；SQLite apply 为 NO-OP（不执行）。"""
    if dialect == "postgresql":
        return statement
    raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")


def _ledger(connection: Connection, version: str) -> Any:
    return connection.execute(
        text(
            "SELECT checksum, manifest_position FROM migration_provenance "
            "WHERE version=:version"
        ),
        {"version": version},
    ).first()


def _audit(connection: Connection, action: str, outcome: str, detail: dict[str, Any]) -> None:
    from uuid import uuid4

    expression = "CAST(:detail AS JSONB)" if connection.dialect.name == "postgresql" else ":detail"
    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id,:version,:action,:outcome,{expression},:recorded_at)"
        ),
        {
            "id": str(uuid4()),
            "version": VERSION,
            "action": action,
            "outcome": outcome,
            "detail": json.dumps(detail, sort_keys=True, separators=(",", ":")),
            "recorded_at": utc_now(),
        },
    )


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "chunks" not in tables:
        raise RuntimeError(f"{VERSION} verification failed: missing table=chunks")

    columns = {column["name"]: column["type"] for column in inspector.get_columns("chunks")}
    missing_columns = sorted(_REQUIRED_ALTERS["chunks"] - set(columns))
    if missing_columns:
        raise RuntimeError(
            f"{VERSION} verification failed: chunks missing columns={','.join(missing_columns)}"
        )

    indexes = {str(index["name"]) for index in inspector.get_indexes("chunks") if index.get("name")}
    if connection.dialect.name == "postgresql":
        missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            raise RuntimeError(
                f"{VERSION} verification failed: missing indexes={','.join(missing_indexes)}"
            )
        # 直接查 information_schema，避免依赖 SQLAlchemy 对 pgvector 类型的
        # 反射注册（migration runner 不导入 pgvector.sqlalchemy）。
        udt = connection.execute(
            text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name='chunks' AND column_name='embedding'"
            )
        ).scalar_one_or_none()
        if not udt or "vector" not in str(udt).lower():
            raise RuntimeError(
                f"{VERSION} verification failed: chunks.embedding is not vector (udt={udt})"
            )
    else:
        # SQLite：JSON 列保留，HNSW 索引不应存在。
        if "ix_chunks_embedding_hnsw" in indexes:
            raise RuntimeError(f"{VERSION} verification failed: unexpected HNSW index on SQLite")
    return tuple(sorted(indexes))


def apply_v4_011(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_010_conversation_engine_unification")
        if previous is None or str(previous[0]) != V4_010_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} requires verified v4_010 conversation_engine_unification"
            )
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) == V4_011_CHECKSUM:
            _audit(connection, "APPLY", "NO_OP", {"engine": "already_applied"})
            return MigrationResult(VERSION, V4_011_CHECKSUM, False, {"engine": "already_applied"})

        dialect = connection.dialect.name
        counts: dict[str, int | str] = {}
        if dialect == "postgresql":
            # 1. JSON null → SQL NULL（vector 不接受 'null' 文本）。
            connection.execute(text(_ddl_for(_NULL_PURGE_DDL, dialect)))
            nulled = connection.execute(
                text("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL")
            ).scalar_one()
            counts["nulled_embedding_rows"] = int(nulled or 0)
            # 2. JSON/JSONB 列 → 原生 vector（显式 cast 走 text I/O 路径）。
            connection.execute(text(_ddl_for(_ALTER_VECTOR_DDL, dialect)))
            # 3. HNSW 余弦索引。
            connection.execute(text(_ddl_for(_HNSW_INDEX_DDL, dialect)))
        elif dialect == "sqlite":
            # SQLite：NO-OP，保留 JSON 列；provenance 照常记录保证链一致。
            counts["engine"] = "noop_json_retained"
        else:
            raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")

        _catalog(connection)

        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"engine": "schema_refreshed"})
            return MigrationResult(VERSION, V4_011_CHECKSUM, False, {"engine": "schema_refreshed"})

        position = int(previous[1]) + 1
        now = utc_now()
        expression = (
            "CAST(:provenance AS JSONB)" if dialect == "postgresql" else ":provenance"
        )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version, owner, checksum, applied_at, verify_status, provenance,"
                "manifest_position, created_at) "
                "VALUES (:version, :owner, :checksum, :applied_at, 'VERIFIED',"
                f"{expression}, :position, :created_at)"
            ),
            {
                "version": VERSION,
                "owner": "pgvector-embedding",
                "checksum": V4_011_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {
                        "tables": list(V4_011_OWNED_TABLES),
                        "indexes": list(_REQUIRED_INDEXES),
                        "audit": {key: value for key, value in counts.items()},
                    },
                    sort_keys=True,
                ),
                "position": position,
                "created_at": now,
            },
        )
        detail: dict[str, object] = {key: value for key, value in counts.items()}
        _audit(connection, "APPLY", "PASS", detail)
    return MigrationResult(VERSION, V4_011_CHECKSUM, True, counts)


def verify_v4_011(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_011_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"engine": "pgvector-embedding"})
    return VerificationResult(
        "PASS", VERSION, V4_011_CHECKSUM, V4_011_OWNED_TABLES, indexes, {}
    )


def rollback_v4_011_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), V4_011_OWNED_TABLES)
        if str(row[0]) != V4_011_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        # HNSW 索引可 drop；列类型不可降级。
        objects = ("ix_chunks_embedding_hnsw",)
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_011_ROLLBACK_POLICY})
    return RollbackPlan(True, objects, V4_011_OWNED_TABLES, V4_011_ROLLBACK_POLICY)
