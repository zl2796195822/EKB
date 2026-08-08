#!/usr/bin/env python3
"""M4-6 备份恢复演练：SQLite 业务库一致性快照备份脚本。

设计：
  - 用 SQLite VACUUM INTO 生成一致性快照（在线备份，不阻塞写入）。
  - 备份文件命名：ekb_backup_YYYYmmdd_HHMMSS.db，含 sha256 校验和（.sha256）。
  - 保留最近 N 份备份，自动清理更老的（默认 7 份）。
  - 备份元数据（路径/大小/校验和/耗时）写入 backup_manifest.jsonl 便于审计。
  - 支持 --dry-run 预检（仅校验源库可打开 + 表清单）。

用法：
  python scripts/backup.py [--db PATH] [--out-dir DIR] [--keep N] [--dry-run]

Spec 依据：部署运维与灾备手册 §6 备份策略；NFR §10 备份"业务库、索引元数据、
对象存储可恢复，M4 完成恢复演练并记录 RPO/RTO"。
当前 M1/M4 阶段用 SQLite（业务库+索引元数据合一），对象存储原文未落盘，
故备份范围 = SQLite 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 默认值：对齐 db.py 的 DEFAULT_DATABASE_URL。
_DEFAULT_DB = os.getenv("EKB_DATABASE_URL", "sqlite:///./ekb_dev.db")
_DEFAULT_OUT_DIR = Path("./backups")
_DEFAULT_KEEP = 7


def _resolve_db_path(db_url: str) -> Path:
    """从 sqlite:///path 解析出文件路径。"""
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        return Path(db_url[len(prefix) :])
    if db_url.startswith("sqlite://"):
        return Path(db_url[len("sqlite://") :])
    return Path(db_url)


def _sha256(path: Path) -> str:
    """计算文件 sha256（分块读取避免大文件内存爆）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    """列出业务表（排除 sqlite 内部表），用于 dry-run 校验。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _row_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    """统计各表行数，备份前后对比用。"""
    counts = {}
    for t in tables:
        counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return counts


def backup(
    db_url: str,
    out_dir: Path,
    keep: int = _DEFAULT_KEEP,
    dry_run: bool = False,
) -> dict:
    """执行备份，返回元数据 dict。dry_run=True 时只校验不写文件。"""
    db_path = _resolve_db_path(db_url)
    if not db_path.exists():
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # dry-run：校验源库可打开 + 表清单，不写备份。
    with sqlite3.connect(str(db_path)) as conn:
        tables = _list_tables(conn)
        source_counts = _row_counts(conn, tables)

    if dry_run:
        return {
            "dry_run": True,
            "db_path": str(db_path),
            "tables": tables,
            "row_counts": source_counts,
        }

    # 在线一致性快照：VACUUM INTO 不阻塞写入，生成紧凑无碎片副本。
    # timestamp 精度到秒，同秒内多次备份加序号避免 VACUUM INTO 文件已存在报错。
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = out_dir / f"ekb_backup_{timestamp}.db"
    seq = 1
    while backup_path.exists():
        backup_path = out_dir / f"ekb_backup_{timestamp}_{seq}.db"
        seq += 1
    t0 = time.perf_counter()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"VACUUM INTO '{backup_path}'")
    elapsed = time.perf_counter() - t0

    # 校验和（备份文件 + 源文件，便于恢复时完整性校验）。
    backup_sha = _sha256(backup_path)
    backup_size = backup_path.stat().st_size

    # 校验备份文件可打开且行数一致。
    with sqlite3.connect(str(backup_path)) as conn:
        backup_tables = _list_tables(conn)
        backup_counts = _row_counts(conn, backup_tables)
    if backup_counts != source_counts:
        raise RuntimeError(f"备份行数不一致: source={source_counts} backup={backup_counts}")

    # 写 .sha256 校验文件。
    (out_dir / f"{backup_path.name}.sha256").write_text(
        f"{backup_sha}  {backup_path.name}\n", encoding="utf-8"
    )

    # 清理老备份（保留最近 keep 份）。
    backups = sorted(out_dir.glob("ekb_backup_*.db"))
    if len(backups) > keep:
        for old in backups[: len(backups) - keep]:
            old.unlink(missing_ok=True)
            (out_dir / f"{old.name}.sha256").unlink(missing_ok=True)

    manifest = {
        "timestamp": timestamp,
        "db_path": str(db_path),
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha,
        "backup_size_bytes": backup_size,
        "elapsed_seconds": round(elapsed, 3),
        "tables": tables,
        "row_counts": backup_counts,
        "keep": keep,
    }

    # 追加写入 manifest（JSONL，便于审计追溯）。
    manifest_path = out_dir / "backup_manifest.jsonl"
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(manifest, ensure_ascii=False) + "\n")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="EKB SQLite 备份脚本")
    parser.add_argument("--db", default=_DEFAULT_DB, help="数据库 URL（默认从环境变量）")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR, help="备份输出目录")
    parser.add_argument("--keep", type=int, default=_DEFAULT_KEEP, help="保留最近 N 份备份")
    parser.add_argument("--dry-run", action="store_true", help="只预检不写文件")
    args = parser.parse_args()

    try:
        result = backup(args.db, args.out_dir, args.keep, args.dry_run)
    except Exception as exc:
        print(f"BACKUP_FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
