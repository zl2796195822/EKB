#!/usr/bin/env python3
"""Explicit, narrow wiring of one KB's embedding profile and active index generation."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url
    for key in ("EKB_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            return value
    from ekb_api.core.config import get_settings

    settings = get_settings()
    if settings.database_url:
        return str(settings.database_url)
    raise SystemExit("no database url: pass --database-url or set EKB_DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="defaults to EKB_DATABASE_URL/DATABASE_URL")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--provider-id", required=True, help="llm_providers.id")
    parser.add_argument("--model-id", required=True, help="llm_models.id (model_type=embedding)")
    parser.add_argument("--dimensions", type=int, required=True)
    parser.add_argument("--tokenizer", default="unicode")
    parser.add_argument("--chunker-id", default="fixed")
    parser.add_argument("--chunker-version", default="chunk-v1.0")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write profile/generation wiring; without it the command is a dry-run",
    )
    args = parser.parse_args(argv)

    from ekb_api.core.db import build_engine
    from ekb_api.ops.embedding_profiles import ProfilePlanRefused, apply_kb_profile, plan_kb_profile

    engine = build_engine(_database_url(args))
    try:
        if args.apply:
            result = apply_kb_profile(
                engine,
                tenant_id=args.tenant_id,
                kb_id=args.kb_id,
                llm_provider_id=args.provider_id,
                model_id=args.model_id,
                dimensions=args.dimensions,
                tokenizer=args.tokenizer,
                chunker_id=args.chunker_id,
                chunker_version=args.chunker_version,
            )
        else:
            result = plan_kb_profile(
                engine,
                tenant_id=args.tenant_id,
                kb_id=args.kb_id,
                llm_provider_id=args.provider_id,
                model_id=args.model_id,
                dimensions=args.dimensions,
                tokenizer=args.tokenizer,
                chunker_id=args.chunker_id,
                chunker_version=args.chunker_version,
            )
    except ProfilePlanRefused as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": result.mode,
                "profile_action": result.profile_action,
                "generation_action": result.generation_action,
                "profile_id": result.profile_id,
                "active_generation_id": result.active_generation_id,
                "kb_id": result.kb_id,
                "tenant_id": result.tenant_id,
                "llm_provider_id": result.llm_provider_id,
                "model_id": result.model_id,
                "dimensions": result.dimensions,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
