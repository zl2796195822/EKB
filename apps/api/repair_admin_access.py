#!/usr/bin/env python3
"""Explicit, narrow repair for one administrator's knowledge-base access."""
from __future__ import annotations

import argparse
import sys

from ekb_api.core.config import get_settings
from ekb_api.core.db import build_engine
from ekb_api.ops.admin_access import repair_admin_access


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument(
        "--tenant-role",
        required=True,
        choices=("OWNER", "ADMIN", "MEMBER", "CUSTOMER"),
    )
    parser.add_argument(
        "--kb-role",
        required=True,
        choices=("OWNER", "ADMIN", "EDITOR", "VIEWER"),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the repair; default is dry-run",
    )
    args = parser.parse_args(argv)

    try:
        result = repair_admin_access(
            build_engine(get_settings().database_url),
            email=args.admin_email,
            tenant_id=args.tenant_id,
            kb_id=args.kb_id,
            tenant_role=args.tenant_role,
            kb_role=args.kb_role,
            apply=args.apply,
        )
    except (RuntimeError, ValueError):
        print("admin access repair refused", file=sys.stderr)
        return 78
    print(f"status={result.mode} legacy_role={result.legacy_role_action}")
    print(
        f"tenant_membership={result.tenant_membership_action} "
        f"kb_membership={result.kb_membership_action}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
