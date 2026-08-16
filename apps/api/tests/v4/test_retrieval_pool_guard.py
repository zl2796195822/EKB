"""M4-9 检索候选池防护：防全表加载 OOM + embedding 降级。

背景（线上故障）：检索大数据量知识库（数万 chunk × embedding 数组）时，
fetch_search_context 用 .all() 全表加载会 OOM 崩溃 worker，SSE 流中断 → 前端
永久卡「正在生成回答」。修复：SQL 层候选池限上限 + embedding 不可用降级纯关键词。

本测试覆盖：
  1. _fetch_kw_pool 受 pool_size 限制（LIMIT），不把知识库全部 chunk 载入内存。
  2. search 在 embedding 不可用（embed_one 抛 EmbeddingError）时降级纯关键词门禁，
     仍返回关键词命中的 chunk，不抛错导致 SSE 断流。
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.store import SqlStore


def _fresh(tmp_path: Path, name: str, chunk_count: int = 60) -> tuple[object, str, str, str]:
    """建临时库 + seed + 迁移链；额外向首个知识库注入 chunk_count 个 chunk。

    返回 (engine, tenant_id, user_id, kb_id)。
    """
    engine = build_engine(f"sqlite:///{tmp_path / name}")
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)
    with engine.connect() as connection:
        tenant = str(
            connection.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one()
        )
        user = str(
            connection.execute(
                text("SELECT id FROM users WHERE tenant_id=:t LIMIT 1"), {"t": tenant}
            ).scalar_one()
        )
        kb = str(
            connection.execute(text("SELECT id FROM knowledge_bases LIMIT 1")).scalar_one()
        )
    with engine.begin() as connection:
        for i in range(chunk_count):
            doc_id = f"doc-pool-{i}"
            connection.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, kb_id, title, status, version,"
                    " mime_type, checksum, chunk_count, source_type, created_at, updated_at)"
                    " VALUES (:id,:t,:kb,'doc','READY',1,'text/plain','c',1,'UPLOAD',"
                    "'2020-01-01T00:00:00Z','2020-01-01T00:00:00Z')"
                ),
                {"id": doc_id, "t": tenant, "kb": kb},
            )
            connection.execute(
                text(
                    "INSERT INTO chunks (id, tenant_id, kb_id, doc_id, doc_version,"
                    " chunk_index, title, section_path, content, token_count, embedding,"
                    " created_at, updated_at)"
                    " VALUES (:id,:t,:kb,:doc,1,:i,'chunk','[]',:content,5,"
                    " '[0.1,0.2,0.3]','2020-01-01T00:00:00Z','2020-01-01T00:00:00Z')"
                ),
                {
                    "id": f"chunk-pool-{i}",
                    "t": tenant,
                    "kb": kb,
                    "doc": doc_id,
                    "i": i,
                    "content": f"填充内容 数据库连接池 {i}",
                },
            )
    return engine, tenant, user, kb


def _store(engine, tenant: str, user: str, monkeypatch) -> tuple[SqlStore, AuthContext]:
    import ekb_api.core.db as db_mod

    monkeypatch.setattr(db_mod, "_engine", engine)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    store = SqlStore()
    auth = AuthContext(
        actor_id=user,
        tenant_id=tenant,
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[],
        policy_version=1,
        trace_id="retrieval-pool-guard-test",
    )
    return store, auth


def test_fetch_kw_pool_capped_by_pool_size(tmp_path: Path, monkeypatch) -> None:
    """关键词候选池受 pool_size 限制：60 个 chunk 只加载 ≤10，不载入全表。"""
    engine, tenant, user, kb = _fresh(tmp_path, "pool-cap.db", chunk_count=60)
    store, auth = _store(engine, tenant, user, monkeypatch)

    rows = store._fetch_kw_pool(auth, "数据库连接池", {kb}, 10)
    assert isinstance(rows, list)
    assert 0 < len(rows) <= 10
    # 无 embedding provider 时池内 row 仍可正常访问（ORM 对象）
    assert all(r.content for r in rows)


def test_search_degrades_when_embedding_fails(tmp_path: Path, monkeypatch) -> None:
    """embedding provider 不可用（embed_one 抛 EmbeddingError）时检索降级纯关键词。

    不抛错（否则 SSE 检索阶段断流，前端永久卡「正在生成回答」），且仍返回命中 chunk。
    """
    engine, tenant, user, kb = _fresh(tmp_path, "embed-fail.db", chunk_count=20)
    store, auth = _store(engine, tenant, user, monkeypatch)

    from ekb_api.embedding import EmbeddingError

    def _boom(*args, **kwargs):
        raise EmbeddingError("未配置远程 embedding provider")

    monkeypatch.setattr("ekb_api.embedding.embed_one", _boom)
    results = store.search(auth, "数据库连接池", [kb], top_k=3)
    assert isinstance(results, list)
    assert len(results) > 0
    # 降级后返回的是关键词命中的 chunk
    assert all("数据库" in c.content or "连接" in c.content for c in results)


def test_search_survives_large_kb(tmp_path: Path, monkeypatch) -> None:
    """大数据量知识库（60 chunk）检索不崩溃、返回 top_k 结果。"""
    engine, tenant, user, kb = _fresh(tmp_path, "large-kb.db", chunk_count=60)
    store, auth = _store(engine, tenant, user, monkeypatch)

    from ekb_api.embedding import EmbeddingError

    def _boom(*args, **kwargs):
        raise EmbeddingError("未配置远程 embedding provider")

    monkeypatch.setattr("ekb_api.embedding.embed_one", _boom)
    results = store.search(auth, "填充内容", [kb], top_k=5)
    assert isinstance(results, list)
    assert len(results) > 0
    assert len(results) <= 5
