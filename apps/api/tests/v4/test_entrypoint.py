from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "entrypoint.sh"


def _run_entrypoint(tmp_path: Path, env_overrides: dict[str, str], *command: str):
    environment = os.environ.copy()
    environment.update(
        {
            "EKB_APP_DIR": str(tmp_path),
            "EKB_ENV": "development",
            "EKB_DATABASE_URL": f"sqlite:///{tmp_path / 'runtime.db'}",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
    )
    environment.update(env_overrides)
    return subprocess.run(
        ["/bin/bash", str(ENTRYPOINT), *command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_missing_token_fails_before_uvicorn(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    result = _run_entrypoint(
        tmp_path,
        {
            "EKB_ENV": "production",
            "EKB_TOKEN_SECRET": "",
            "EKB_DEV_USER_EMAIL": "operator@example.invalid",
            "EKB_DEV_USER_NAME": "Operator",
            "EKB_DEV_PASSWORD": "runtime-password",
            "EKB_APPS_MASTER_KEY": "runtime-master-key",
        },
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "missing_token_secret" in result.stdout
    assert "runtime-password" not in result.stdout
    assert "runtime-master-key" not in result.stdout


def test_production_missing_runtime_master_fails_before_uvicorn(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    result = _run_entrypoint(
        tmp_path,
        {
            "EKB_ENV": "production",
            "EKB_TOKEN_SECRET": "runtime-token-secret",
            "EKB_DATABASE_URL": "postgresql://db.example.invalid/ekb",
            "EKB_DEV_USER_EMAIL": "operator@example.invalid",
            "EKB_DEV_USER_NAME": "Operator",
            "EKB_DEV_PASSWORD": "runtime-password",
            "EKB_APPS_MASTER_KEY": "",
        },
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "missing_runtime_master_key" in result.stdout
    assert "runtime-token-secret" not in result.stdout


def test_production_missing_runtime_admin_fails_before_uvicorn(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    result = _run_entrypoint(
        tmp_path,
        {
            "EKB_ENV": "production",
            "EKB_TOKEN_SECRET": "runtime-token-secret",
            "EKB_DATABASE_URL": "postgresql://db.example.invalid/ekb",
            "EKB_DEV_USER_EMAIL": "operator@example.invalid",
            "EKB_DEV_USER_NAME": "Operator",
            "EKB_DEV_PASSWORD": "",
            "EKB_APPS_MASTER_KEY": "runtime-master-key",
        },
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "missing_runtime_admin_password" in result.stdout
    assert "runtime-token-secret" not in result.stdout


def test_production_default_sqlite_is_rejected_before_migration(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    result = _run_entrypoint(
        tmp_path,
        {
            "EKB_ENV": "production",
            "EKB_TOKEN_SECRET": "runtime-token-secret",
            "EKB_DEV_USER_EMAIL": "operator@example.invalid",
            "EKB_DEV_USER_NAME": "Operator",
            "EKB_DEV_PASSWORD": "runtime-password",
            "EKB_APPS_MASTER_KEY": "runtime-master-key",
        },
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "production_sqlite_database_forbidden" in result.stdout
    assert "runtime-token-secret" not in result.stdout


def test_production_default_runtime_password_is_rejected_before_migration(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    result = _run_entrypoint(
        tmp_path,
        {
            "EKB_ENV": "production",
            "EKB_TOKEN_SECRET": "runtime-token-secret",
            "EKB_DATABASE_URL": "postgresql://db.example.invalid/ekb",
            "EKB_DEV_USER_EMAIL": "operator@example.invalid",
            "EKB_DEV_USER_NAME": "Operator",
            "EKB_DEV_PASSWORD": "admin",
            "EKB_APPS_MASTER_KEY": "runtime-master-key",
        },
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "default_admin_password" in result.stdout
    assert "runtime-token-secret" not in result.stdout


def test_migration_failure_does_not_start_command(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    marker = tmp_path / "started"

    result = _run_entrypoint(
        tmp_path,
        {"PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"},
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "migration_or_verify" in result.stdout


def test_ensure_admin_failure_does_not_start_command(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *'-m ekb_api.migrations.v4_fullstack'*) exit 0;;\n"
        "  *) exit 19;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    marker = tmp_path / "started"

    result = _run_entrypoint(
        tmp_path,
        {"PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"},
        "sh",
        "-c",
        f"touch {marker}",
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "ensure_admin" in result.stdout
