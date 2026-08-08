"""M4-6 备份恢复演练：备份/恢复完整性测试。"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DEV_USER_EMAIL", "admin@example.com")
os.environ.setdefault("EKB_DEV_PASSWORD", "test-password")
os.environ.setdefault("EKB_TOKEN_SECRET", "test-only-token-secret")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

# scripts/ 目录加入 path 以导入 backup/restore 模块。
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import backup as backup_mod  # noqa: E402
import restore as restore_mod  # noqa: E402


def _make_test_db(path: Path, rows: int = 3) -> str:
    """创建测试用 SQLite 库并写入数据，返回 db_url。"""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO items VALUES (?, ?)", [(i, f"item-{i}") for i in range(rows)])
    return f"sqlite:///{path}"


def test_backup_creates_valid_snapshot(tmp_path: Path) -> None:
    """备份生成一致性快照，行数与源库一致，含 sha256 校验文件。"""
    db_path = tmp_path / "src.db"
    db_url = _make_test_db(db_path, rows=5)
    out_dir = tmp_path / "backups"

    result = backup_mod.backup(db_url, out_dir, keep=7)

    assert result["backup_path"]
    backup_file = Path(result["backup_path"])
    assert backup_file.exists()
    assert result["backup_sha256"]
    assert result["backup_size_bytes"] > 0
    assert result["row_counts"]["items"] == 5
    # sha256 校验文件存在
    assert (out_dir / f"{backup_file.name}.sha256").exists()
    # manifest 存在
    assert (out_dir / "backup_manifest.jsonl").exists()


def test_backup_dry_run(tmp_path: Path) -> None:
    """dry-run 不写备份文件，只返回表清单和行数。"""
    db_path = tmp_path / "src.db"
    db_url = _make_test_db(db_path, rows=2)
    out_dir = tmp_path / "backups"

    result = backup_mod.backup(db_url, out_dir, keep=7, dry_run=True)

    assert result["dry_run"] is True
    assert "items" in result["tables"]
    assert result["row_counts"]["items"] == 2
    # 不应生成备份文件
    assert not list(out_dir.glob("ekb_backup_*.db"))


def test_backup_keeps_only_recent(tmp_path: Path) -> None:
    """keep=N 时只保留最近 N 份备份。"""
    db_path = tmp_path / "src.db"
    db_url = _make_test_db(db_path, rows=1)
    out_dir = tmp_path / "backups"

    # 连续备份 3 次，keep=2。
    for _ in range(3):
        backup_mod.backup(db_url, out_dir, keep=2)

    backups = list(out_dir.glob("ekb_backup_*.db"))
    assert len(backups) == 2


def test_restore_recovers_data(tmp_path: Path) -> None:
    """恢复后目标库数据与备份一致。"""
    # 源库写入 5 行。
    src_path = tmp_path / "src.db"
    db_url = _make_test_db(src_path, rows=5)
    out_dir = tmp_path / "backups"

    # 备份。
    backup_result = backup_mod.backup(db_url, out_dir, keep=7)
    backup_file = Path(backup_result["backup_path"])

    # 模拟数据丢失：删源库，重建一个只有 1 行的库。
    src_path.unlink()
    _make_test_db(src_path, rows=1)

    # 恢复。
    restore_result = restore_mod.restore(backup_file, db_url, out_dir, force=True)

    assert restore_result["row_counts"]["items"] == 5
    assert restore_result["backup_sha256"] == backup_result["backup_sha256"]
    # 恢复后源库应有 5 行。
    with sqlite3.connect(str(src_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 5


def test_restore_creates_pre_restore_backup(tmp_path: Path) -> None:
    """恢复前自动备份当前库（防误恢复）。"""
    src_path = tmp_path / "src.db"
    db_url = _make_test_db(src_path, rows=3)
    out_dir = tmp_path / "backups"

    # 第一次备份（作为恢复源）。
    backup_result = backup_mod.backup(db_url, out_dir, keep=7)
    backup_file = Path(backup_result["backup_path"])

    # 往源库再写一行（模拟恢复前有新数据需保护）。
    with sqlite3.connect(str(src_path)) as conn:
        conn.execute("INSERT INTO items VALUES (999, 'pre-restore')")

    # 恢复（force 跳过交互）。
    restore_result = restore_mod.restore(backup_file, db_url, out_dir, force=True)

    # 应生成恢复前自动备份。
    assert restore_result["pre_restore_backup"] is not None
    assert restore_result["pre_restore_backup"]["note"] == "恢复前自动备份"


def test_restore_sha256_mismatch_fails(tmp_path: Path) -> None:
    """备份文件 sha256 与 .sha256 文件不一致时恢复失败。"""
    src_path = tmp_path / "src.db"
    db_url = _make_test_db(src_path, rows=2)
    out_dir = tmp_path / "backups"

    backup_result = backup_mod.backup(db_url, out_dir, keep=7)
    backup_file = Path(backup_result["backup_path"])

    # 篡改 sha256 文件。
    sha_file = out_dir / f"{backup_file.name}.sha256"
    sha_file.write_text("0000000000000000000000000000000000000000000000000000000000000000  fake\n")

    # 恢复应因校验失败而抛异常。
    try:
        restore_mod.restore(backup_file, db_url, out_dir, force=True)
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as exc:
        assert "sha256" in str(exc).lower()
