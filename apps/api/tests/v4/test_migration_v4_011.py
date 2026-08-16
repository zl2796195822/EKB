"""Migration verification for v4_011 pgvector native embedding column.

Tests cover:
* apply / verify through the full migration chain on SQLite
* provenance ledger recorded with a stable checksum
* SQLite retains the JSON column (no HNSW index) and ORM list round-trip
* EmbeddingVector dialect resolution (SQLite→JSON, PostgreSQL→VECTOR)
* apply idempotency and dry-run-only rollback
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations import v4_fullstack
from ekb_api.migrations.v4_011_pgvector_embedding import (
    V4_011_CHECKSUM,
    VERSION,
    apply_v4_011,
    rollback_v4_011_dry_run,
    verify_v4_011,
)


def _apply_full_chain(database: Path) -> int:
    return v4_fullstack.main(
        ["--database-url", f"sqlite:///{database}", "--verify"]
    )


def _fresh_engine(database: Path):
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    engine.dispose()
    return build_engine(f"sqlite:///{database}")


def test_v4_011_provenance_ledger_and_json_retention(tmp_path: Path) -> None:
    database = tmp_path / "v4_011_schema.db"
    engine = _fresh_engine(database)
    assert _apply_full_chain(database) == 0

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT checksum, verify_status FROM migration_provenance "
                "WHERE version=:version"
            ),
            {"version": VERSION},
        ).first()
        assert row is not None
        assert row[0] == V4_011_CHECKSUM
        assert row[1] == "VERIFIED"

        # SQLite keeps the JSON column; no HNSW index is created.
        inspector = inspect(conn)
        assert "chunks" in inspector.get_table_names()
        col_types = {c["name"]: c["type"] for c in inspector.get_columns("chunks")}
        assert str(col_types["embedding"]).lower().startswith("json"), (
            f"SQLite embedding must stay JSON, got {col_types['embedding']}"
        )
        index_names = {
            str(i["name"]) for i in inspector.get_indexes("chunks") if i.get("name")
        }
        assert "ix_chunks_embedding_hnsw" not in index_names


def test_v4_011_orm_round_trip_list(tmp_path: Path) -> None:
    database = tmp_path / "v4_011_roundtrip.db"
    engine = _fresh_engine(database)
    assert _apply_full_chain(database) == 0

    from sqlalchemy.orm import Session

    from ekb_api import models

    with Session(engine) as session:
        session.add(
            models.Chunk(
                id="c-011",
                tenant_id="t",
                kb_id="kb",
                doc_id="d",
                doc_version=1,
                chunk_index=0,
                title="t",
                section_path=[],
                content="c",
                content_hash="h",
                token_count=3,
                embedding=[0.1, 0.2, 0.3],
                created_at="2026-08-16T00:00:00Z",
                updated_at="2026-08-16T00:00:00Z",
            )
        )
        session.commit()
        chunk = session.get(models.Chunk, "c-011")
        assert chunk.embedding == [0.1, 0.2, 0.3]
        assert isinstance(chunk.embedding, list)


def test_v4_011_apply_is_idempotent_and_rollback_dry_run(tmp_path: Path) -> None:
    database = tmp_path / "v4_011_idempotent.db"
    engine = _fresh_engine(database)
    assert _apply_full_chain(database) == 0

    result = apply_v4_011(engine)
    assert result.applied is False
    assert result.checksum == V4_011_CHECKSUM

    verification = verify_v4_011(engine)
    assert verification.status == "PASS"
    assert verification.version == VERSION

    plan = rollback_v4_011_dry_run(engine)
    assert plan.applied is True
    assert plan.blocked_reason is not None
    assert "dry-run only" in plan.blocked_reason
    # HNSW index is the only droppable object; no tables are owned.
    assert plan.objects == ("ix_chunks_embedding_hnsw",)
    assert plan.retained == ()


def test_embedding_vector_dialect_resolution() -> None:
    from sqlalchemy import JSON, create_engine

    from ekb_api.core.embedding_types import EmbeddingVector

    sqlite_engine = create_engine("sqlite:///:memory:")
    sqlite_impl = EmbeddingVector().load_dialect_impl(sqlite_engine.dialect)
    assert isinstance(sqlite_impl, JSON)

    from sqlalchemy.dialects import postgresql

    pg_impl = EmbeddingVector().load_dialect_impl(postgresql.dialect())
    assert type(pg_impl).__module__ == "pgvector.sqlalchemy.vector"
    assert type(pg_impl).__name__ == "VECTOR"

    vector = EmbeddingVector()
    # bind: None and list normalize to list[float]
    assert vector.process_bind_param(None, sqlite_engine.dialect) is None
    assert vector.process_bind_param([1, 2.5], sqlite_engine.dialect) == [1.0, 2.5]
    # result: ndarray (pgvector) and list (JSON) both normalize to list[float]
    assert vector.process_result_value(None, sqlite_engine.dialect) is None
    assert vector.process_result_value([0.1, 0.2], sqlite_engine.dialect) == [0.1, 0.2]
