"""pgvector 原生向量列（方言感知）。

- PostgreSQL：使用 pgvector 的 VECTOR 类型（原生 ANN 检索 + HNSW 索引）。
- SQLite：降级为 JSON（测试/本地开发，仍走 Python 层余弦）。

用法：embedding = Column(EmbeddingVector(), nullable=True)
写入时传 list[float]，读取时得到 list[float]（PG 由 pgvector 驱动编解码）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class EmbeddingVector(TypeDecorator):
    """PostgreSQL → pgvector VECTOR；SQLite → JSON。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector  # noqa: PLC0415

            return dialect.type_descriptor(Vector())  # 无固定维度，兼容任意维
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return [float(v) for v in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        # pgvector 的 result processor 返回 numpy.ndarray（float32），而契约要求
        # list[float]；归一为纯 Python list，保证 Python 层余弦检索与 JSON 语义一致
        # （cosine_similarity 的 truthiness / len 检查不接受 ndarray）。
        if value is None:
            return None
        return [float(v) for v in value]
