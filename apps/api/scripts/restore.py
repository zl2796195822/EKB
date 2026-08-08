#!/usr/bin/env python3
"""M4-6 备份恢复演练：SQLite 恢复脚本。

设计：
  - 恢复前自动备份当前库（防误恢复），命名为 pre_restore_*.db。
  - 校验备份文件 sha256（与 .sha256 文件比对）。
  - 校验备份文件可打开 + 表行数，确认完整性后覆盖目标库。
  - 恢复后校验目标库表行数与备份一致。
  - --force 跳过交互确认（脚本/演练用）。

用法：
  python scripts/restore.py --backup PATH [--db PATH] [--force]

⚠️ 恢复会覆盖目标库，必须先停止服务再执行。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 复用 backup.py 的工具函数。
sys.path.insert(0, str(Path(__file__).parent))
from backup import (  # noqa: E402
    _list_tables,
    _resolve_db_path,
    _row_counts,
    _sha256,
    backup,
)

_DEFAULT_DB = os.getenv("EKB_DATABASE_URL", "sqlite:///./ekb_dev.db")
_DEFAULT_OUT_DIR = Path("./backups")


def _verify_sha256(backup_path: Path) -> str:
    """校验备份文件 sha256 与 .sha256 文件一致；返回实际 sha256。"""
    actual = _sha256(backup_path)
    sha_file = Path(f"{backup_path}.sha256")
    if sha_file.exists():
        expected = sha_file.read_text(encoding="utf-8").split()[0]
        if expected != actual:
            raise RuntimeError(f"sha256 校验失败: expected={expected} actual={actual}")
    return actual


def restore(
    backup_path: Path,
    db_url: str,
    out_dir: Path,
    force: bool = False,
) -> dict:
    """执行恢复，返回元数据 dict。"""
    if not backup_path.exists():
        raise FileNotFoundError(f"备份文件不存在: {backup_path}")

    db_path = _resolve_db_path(db_url)

    # 交互确认（非 force 时）。
    if not force:
        print(f"即将用 {backup_path} 覆盖 {db_path}，目标库现有数据将丢失。")
        answer = input("确认恢复？输入 yes 继续：").strip().lower()
        if answer != "yes":
            return {"cancelled": True}

    # 校验备份完整性。
    backup_sha = _verify_sha256(backup_path)
    with sqlite3.connect(str(backup_path)) as conn:
        backup_tables = _list_tables(conn)
        backup_counts = _row_counts(conn, backup_tables)

    # 恢复前自动备份当前库（防误恢复）。
    pre_restore_meta = None
    if db_path.exists():
        pre_restore_meta = backup(db_url, out_dir, keep=14)
        pre_restore_meta["note"] = "恢复前自动备份"

    # 执行恢复：停服务后直接文件覆盖（SQLite 文件级恢复）。
    t0 = time.perf_counter()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(backup_path), str(db_path))
    elapsed = time.perf_counter() - t0

    # 恢复后校验：目标库表行数应与备份一致。
    with sqlite3.connect(str(db_path)) as conn:
        restored_tables = _list_tables(conn)
        restored_counts = _row_counts(conn, restored_tables)

    if restored_counts != backup_counts:
        raise RuntimeError(f"恢复后行数不一致: backup={backup_counts} restored={restored_counts}")

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha,
        "db_path": str(db_path),
        "elapsed_seconds": round(elapsed, 3),
        "tables": restored_tables,
        "row_counts": restored_counts,
        "pre_restore_backup": pre_restore_meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="EKB SQLite 恢复脚本")
    parser.add_argument("--backup", type=Path, required=True, help="备份文件路径")
    parser.add_argument("--db", default=_DEFAULT_DB, help="目标数据库 URL")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR, help="恢复前备份输出目录")
    parser.add_argument("--force", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    try:
        result = restore(args.backup, args.db, args.out_dir, args.force)
    except Exception as exc:
        print(f"RESTORE_FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
