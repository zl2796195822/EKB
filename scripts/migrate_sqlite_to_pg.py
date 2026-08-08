#!/usr/bin/env python
"""EKB · SQLite → PostgreSQL 数据迁移脚本

用途：将现有 SQLite 数据库（ekb_dev.db 或指定路径）迁移到 PostgreSQL。

使用方法：
    # 1. 确保 PostgreSQL 已启动并配置好 EKB_DATABASE_URL
    export EKB_DATABASE_URL="postgresql+psycopg2://ekb:password@localhost:5432/ekb_prod"

    # 2. 运行迁移
    cd /Users/alin/EKB
    .venv/bin/python scripts/migrate_sqlite_to_pg.py

    # 3. 干运行（不写入，仅统计迁移数量）
    .venv/bin/python scripts/migrate_sqlite_to_pg.py --dry-run

    # 4. 指定 SQLite 路径
    .venv/bin/python scripts/migrate_sqlite_to_pg.py --sqlite-path /path/to/ekb.db

注意：
    - 目标 PostgreSQL 必须是空库或全新初始化的库（脚本不处理冲突合并）
    - 迁移前会自动在 PostgreSQL 创建表结构（create_all）
    - embedding 列为 JSON，SQLite TEXT 和 PostgreSQL JSONB 均兼容
    - 迁移完成后需删除 SQLite 文件或修改 EKB_DATABASE_URL 指向 PostgreSQL
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate EKB data from SQLite to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        default=str(Path(__file__).resolve().parents[1] / "ekb_dev.db"),
        help="Path to source SQLite database (default: project root ekb_dev.db)",
    )
    parser.add_argument(
        "--pg-url",
        default=os.getenv("EKB_DATABASE_URL"),
        help="PostgreSQL connection URL (default: EKB_DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows without writing to PostgreSQL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows per batch insert (default: 200)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        print(f"[ERROR] SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1

    if not args.pg_url:
        print(
            "[ERROR] PostgreSQL URL not specified. Set EKB_DATABASE_URL or use --pg-url.",
            file=sys.stderr,
        )
        return 1

    if not args.pg_url.startswith("postgresql"):
        print(
            f"[ERROR] Target must be PostgreSQL, got: {args.pg_url[:30]}...",
            file=sys.stderr,
        )
        return 1

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
    except ImportError:
        print("[ERROR] sqlalchemy not installed. Run: pip install sqlalchemy psycopg2-binary")
        return 1

    print(f"Source SQLite: {sqlite_path}")
    print(f"Target PostgreSQL: {args.pg_url.split('@')[-1]}")
    print(f"Dry run: {args.dry_run}")
    print()

    # --- 源：SQLite ---
    src_engine = create_engine(
        f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False}
    )
    SrcSession = sessionmaker(bind=src_engine)

    # --- 目标：PostgreSQL ---
    dst_engine = create_engine(
        args.pg_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
    )

    if not args.dry_run:
        # 初始化目标库表结构（幂等）
        print("Initializing PostgreSQL schema...")
        os.environ["EKB_DATABASE_URL"] = args.pg_url
        os.environ["EKB_ENV"] = "test"  # 跳过 .env 加载，避免覆盖已设置的 URL
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
        try:
            from ekb_api.core.db import Base  # noqa: PLC0415
            from ekb_api import models as _models  # noqa: F401,PLC0415

            # pgvector 扩展（静默跳过若无权限）
            try:
                with dst_engine.begin() as conn:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                print("  pgvector extension: OK")
            except Exception:
                print("  pgvector extension: skipped (no permission or not installed)")

            Base.metadata.create_all(dst_engine)
            print("  Schema created: OK")
        except Exception as e:
            print(f"[ERROR] Schema init failed: {e}", file=sys.stderr)
            return 1
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

    DstSession = sessionmaker(bind=dst_engine)

    # --- 按表逐批迁移 ---
    TABLE_ORDER = [
        "tenants",
        "tenant_daily_usage",
        "users",
        "knowledge_bases",
        "kb_memberships",
        "documents",
        "chunks",
        "ingest_jobs",
        "conversations",
        "messages",
        "feedback",
        "audit_logs",
        "review_items",
        "document_versions",
        "sync_sources",
    ]

    total_rows = 0
    errors = []

    with src_engine.connect() as src_conn:
        for table in TABLE_ORDER:
            try:
                result = src_conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar() or 0
            except Exception:
                print(f"  [{table}] SKIP (table not found in source)")
                continue

            if count == 0:
                print(f"  [{table}] 0 rows, skip")
                continue

            if args.dry_run:
                print(f"  [{table}] {count} rows (dry-run)")
                total_rows += count
                continue

            # 批量迁移
            migrated = 0
            offset = 0
            with DstSession() as dst_session:
                while offset < count:
                    rows_result = src_conn.execute(
                        text(f"SELECT * FROM {table} LIMIT {args.batch_size} OFFSET {offset}")
                    )
                    batch = rows_result.mappings().all()
                    if not batch:
                        break
                    try:
                        dst_session.execute(
                            text(
                                f"INSERT INTO {table} ({', '.join(batch[0].keys())}) "
                                f"VALUES ({', '.join(':' + k for k in batch[0].keys())}) "
                                f"ON CONFLICT DO NOTHING"
                            ),
                            [dict(row) for row in batch],
                        )
                        dst_session.commit()
                        migrated += len(batch)
                    except Exception as e:
                        dst_session.rollback()
                        errors.append(f"{table} offset={offset}: {e}")
                        print(f"  [{table}] ERROR at offset={offset}: {e}", file=sys.stderr)
                    offset += args.batch_size

            total_rows += migrated
            print(f"  [{table}] {migrated}/{count} rows migrated")

    print()
    if args.dry_run:
        print(f"Dry run complete. Would migrate {total_rows} rows across {len(TABLE_ORDER)} tables.")
    else:
        print(f"Migration complete. {total_rows} rows migrated.")
        if errors:
            print(f"  {len(errors)} errors encountered (see above).")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
