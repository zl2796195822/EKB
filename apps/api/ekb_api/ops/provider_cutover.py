"""Safely move the active LLM configuration from legacy SQLite to a v4 database.

The production cutover has two independent data planes: the legacy SQLite
runtime contains model settings, while the PostgreSQL knowledge-base snapshot
contains documents.  This module deliberately migrates only the LLM
configuration.  It never copies the legacy ``llm_providers.api_key`` column
as plaintext: every such value is encrypted into ``provider_credentials``
before the target transaction is committed.

The default operation is a dry run.  The target configuration tables must be
empty, identities must already exist in the target, and all referenced source
credentials must decrypt successfully.  These checks make the operation safe
to rehearse and prevent an accidental merge into an active configuration.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine

from ekb_api.core.db import build_engine, get_engine
from ekb_api.domain import utc_now
from ekb_api.services import secrets as provider_secrets

_CONFIG_TABLES: Final[tuple[str, ...]] = (
    "llm_providers",
    "llm_models",
    "provider_credentials",
    "model_fallback_policies",
)
_JSON_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "allowed_error_classes",
        "api_features",
        "capabilities",
        "egress_policy",
        "endpoint_configs",
        "ordered_model_ids",
        "settings",
        "websites",
    }
)


class ProviderCutoverError(RuntimeError):
    """Raised when the configuration bridge cannot prove a safe import."""


@dataclass(frozen=True)
class ProviderOwnerMapping:
    """An explicit one-owner mapping between independently seeded runtimes.

    This is intentionally not inferred from email addresses or display names.
    A mapping is accepted only when every imported configuration record belongs
    to the declared source owner and the declared target identity already
    exists.  The default remains an exact source/target identity match.
    """

    source_tenant_id: str
    source_user_id: str
    target_tenant_id: str
    target_user_id: str


@dataclass(frozen=True)
class ProviderCutoverReport:
    mode: str
    providers: int
    models: int
    credentials_imported: int
    credentials_encrypted_from_legacy: int
    fallback_policies: int
    orphan_credentials_skipped: int
    duplicate_providers_skipped: int


def _table(engine: Engine, name: str) -> Table:
    metadata = MetaData()
    try:
        return Table(name, metadata, autoload_with=engine)
    except Exception as exc:  # noqa: BLE001 - normalize database-specific failures
        raise ProviderCutoverError(f"required table is unavailable: {name}") from exc


def _count(connection, table: Table) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def _normalize_value(column_name: str, value: Any) -> Any:
    if column_name not in _JSON_COLUMNS or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProviderCutoverError(
            f"source JSON configuration is malformed in {column_name}"
        ) from exc


def _copy_row(
    source: dict[str, Any], target: Table, *, excluded: Iterable[str] = ()
) -> dict[str, Any]:
    excluded_columns = set(excluded)
    return {
        column.name: _normalize_value(column.name, source[column.name])
        for column in target.columns
        if column.name in source and column.name not in excluded_columns
    }


def _required_source_rows(source_engine: Engine) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    int,
]:
    providers_table = _table(source_engine, "llm_providers")
    models_table = _table(source_engine, "llm_models")
    credentials_table = _table(source_engine, "provider_credentials")
    policies_table = _table(source_engine, "model_fallback_policies")

    with source_engine.connect() as connection:
        providers = [dict(row) for row in connection.execute(select(providers_table)).mappings()]
        models = [dict(row) for row in connection.execute(select(models_table)).mappings()]
        credentials = [
            dict(row) for row in connection.execute(select(credentials_table)).mappings()
        ]
        policies = [dict(row) for row in connection.execute(select(policies_table)).mappings()]

    credential_by_id = {str(row["id"]): row for row in credentials}
    referenced_ids = {
        str(provider["credential_id"])
        for provider in providers
        if provider.get("credential_id")
    }
    missing_credentials = referenced_ids - set(credential_by_id)
    if missing_credentials:
        raise ProviderCutoverError("provider references a missing encrypted credential")

    model_provider_ids = {str(model["provider_id"]) for model in models}
    provider_ids = {str(provider["id"]) for provider in providers}
    if not model_provider_ids.issubset(provider_ids):
        raise ProviderCutoverError("model references a missing provider")

    return providers, models, credential_by_id, policies, len(credentials) - len(referenced_ids)


def _deduplicate_providers(
    providers: list[dict[str, Any]], models: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Keep one provider per target uniqueness key without guessing active config.

    Historical SQLite data may contain repeated UI submissions for a provider
    key. The target schema correctly makes ``tenant/user/provider_key`` unique.
    Prefer an enabled record, then the latest updated/created timestamp, then
    the stable id. Models owned by a discarded provider are deliberately not
    copied, because they would otherwise reference a configuration that is no
    longer executable.
    """

    selected_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for provider in providers:
        key = (
            str(provider.get("tenant_id") or ""),
            str(provider.get("user_id") or ""),
            str(provider.get("provider_key") or ""),
        )
        if not all(key):
            raise ProviderCutoverError("provider identity or provider key is missing")
        priority = (
            bool(provider.get("is_enabled")),
            str(provider.get("updated_at") or ""),
            str(provider.get("created_at") or ""),
            str(provider.get("id") or ""),
        )
        existing = selected_by_key.get(key)
        if existing is None or priority > (
            bool(existing.get("is_enabled")),
            str(existing.get("updated_at") or ""),
            str(existing.get("created_at") or ""),
            str(existing.get("id") or ""),
        ):
            selected_by_key[key] = provider

    selected = list(selected_by_key.values())
    selected_ids = {str(provider["id"]) for provider in selected}
    selected_models = [
        model for model in models if str(model.get("provider_id") or "") in selected_ids
    ]
    return selected, selected_models, len(providers) - len(selected)


def _assert_target_is_empty(target_engine: Engine, tables: dict[str, Table]) -> None:
    with target_engine.connect() as connection:
        populated = [name for name, table in tables.items() if _count(connection, table)]
    if populated:
        raise ProviderCutoverError(
            "target provider configuration is not empty: " + ",".join(sorted(populated))
        )


def _assert_target_identities(
    target_engine: Engine,
    providers: list[dict[str, Any]],
    owner_mapping: ProviderOwnerMapping | None,
) -> None:
    users_table = _table(target_engine, "users")
    expected = {
        _mapped_identity(provider, owner_mapping) for provider in providers
    }
    with target_engine.connect() as connection:
        actual = {
            (str(row.tenant_id), str(row.id))
            for row in connection.execute(select(users_table.c.tenant_id, users_table.c.id))
        }
    if not expected.issubset(actual):
        raise ProviderCutoverError("target is missing an LLM configuration owner identity")


def _mapped_identity(
    row: dict[str, Any], owner_mapping: ProviderOwnerMapping | None
) -> tuple[str, str]:
    source_identity = (str(row.get("tenant_id") or ""), str(row.get("user_id") or ""))
    if owner_mapping is None:
        return source_identity
    if source_identity != (owner_mapping.source_tenant_id, owner_mapping.source_user_id):
        raise ProviderCutoverError("source configuration owner does not match the explicit mapping")
    return owner_mapping.target_tenant_id, owner_mapping.target_user_id


def _validate_owner_mapping(
    providers: list[dict[str, Any]],
    models: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    owner_mapping: ProviderOwnerMapping | None,
) -> None:
    if owner_mapping is None:
        return
    values = (
        owner_mapping.source_tenant_id,
        owner_mapping.source_user_id,
        owner_mapping.target_tenant_id,
        owner_mapping.target_user_id,
    )
    if any(not value.strip() for value in values):
        raise ProviderCutoverError("explicit owner mapping is incomplete")
    for row in [*providers, *models]:
        _mapped_identity(row, owner_mapping)
    for policy in policies:
        if str(policy.get("tenant_id") or "") != owner_mapping.source_tenant_id:
            raise ProviderCutoverError("source fallback policy does not match the explicit mapping")
        for key in ("user_id", "owner_user_id"):
            value = policy.get(key)
            if value is not None and str(value) != owner_mapping.source_user_id:
                raise ProviderCutoverError(
                    "source fallback policy does not match the explicit mapping"
                )


def _remap_owner(
    row: dict[str, Any], owner_mapping: ProviderOwnerMapping | None
) -> dict[str, Any]:
    if owner_mapping is None:
        return row
    mapped = dict(row)
    if str(mapped.get("tenant_id") or "") == owner_mapping.source_tenant_id:
        mapped["tenant_id"] = owner_mapping.target_tenant_id
    for key in ("user_id", "owner_user_id"):
        if str(mapped.get(key) or "") == owner_mapping.source_user_id:
            mapped[key] = owner_mapping.target_user_id
    if mapped.get("ownership_key") == f"USER:{owner_mapping.source_user_id}":
        mapped["ownership_key"] = f"USER:{owner_mapping.target_user_id}"
    return mapped


def _source_credential_for_provider(
    provider: dict[str, Any], credential_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    credential_id = provider.get("credential_id")
    if not credential_id:
        return None
    credential = credential_by_id[str(credential_id)]
    if str(credential["tenant_id"]) != str(provider["tenant_id"]):
        raise ProviderCutoverError("provider credential tenant mismatch")
    if str(credential.get("ownership_scope")) == "PERSONAL" and str(
        credential.get("owner_user_id") or ""
    ) != str(provider["user_id"]):
        raise ProviderCutoverError("provider credential owner mismatch")
    try:
        provider_secrets.decrypt(str(credential["ciphertext"]))
    except Exception as exc:  # noqa: BLE001 - no secret/error detail reaches output
        raise ProviderCutoverError("referenced encrypted credential cannot be decrypted") from exc
    return credential


def _prepare_records(
    providers: list[dict[str, Any]],
    models: list[dict[str, Any]],
    credential_by_id: dict[str, dict[str, Any]],
    policies: list[dict[str, Any]],
    target_tables: dict[str, Table],
    owner_mapping: ProviderOwnerMapping | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
]:
    provider_records: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    credential_records: list[dict[str, Any]] = []
    policy_records: list[dict[str, Any]] = []
    encrypted_from_legacy = 0
    emitted_credentials: set[str] = set()

    for provider in providers:
        mapped_provider = _remap_owner(provider, owner_mapping)
        provider_record = _copy_row(
            mapped_provider, target_tables["llm_providers"], excluded=("api_key",)
        )
        legacy_secret = str(provider.get("api_key") or "").strip()
        source_credential = _source_credential_for_provider(provider, credential_by_id)
        if legacy_secret and source_credential is not None:
            raise ProviderCutoverError("provider has both legacy and encrypted credentials")

        if source_credential is not None:
            credential_id = str(source_credential["id"])
            if credential_id not in emitted_credentials:
                credential_records.append(
                    _copy_row(
                        _remap_owner(source_credential, owner_mapping),
                        target_tables["provider_credentials"],
                    )
                )
                emitted_credentials.add(credential_id)
        elif legacy_secret:
            try:
                envelope = provider_secrets.encrypt(legacy_secret)
            except Exception as exc:  # noqa: BLE001 - normalize without logging plaintext
                raise ProviderCutoverError("legacy provider secret cannot be encrypted") from exc
            credential_id = str(uuid4())
            credential_records.append(
                {
                    "id": credential_id,
                    "tenant_id": str(mapped_provider["tenant_id"]),
                    "owner_user_id": str(mapped_provider["user_id"]),
                    "ownership_scope": "PERSONAL",
                    "ownership_key": f"USER:{mapped_provider['user_id']}",
                    "ciphertext": envelope.ciphertext,
                    "key_version": envelope.key_version,
                    "secret_last4": envelope.secret_last4,
                    "status": "ACTIVE",
                    "created_at": utc_now(),
                }
            )
            encrypted_from_legacy += 1
        else:
            credential_id = None

        provider_record["api_key"] = None
        provider_record["credential_id"] = credential_id
        if credential_id and source_credential is None:
            provider_record["ownership_scope"] = "PERSONAL"
            provider_record["ownership_key"] = f"USER:{mapped_provider['user_id']}"
        provider_records.append(provider_record)

    for model in models:
        model_records.append(
            _copy_row(_remap_owner(model, owner_mapping), target_tables["llm_models"])
        )
    for policy in policies:
        policy_records.append(
            _copy_row(
                _remap_owner(policy, owner_mapping),
                target_tables["model_fallback_policies"],
            )
        )

    return (
        provider_records,
        model_records,
        credential_records,
        policy_records,
        encrypted_from_legacy,
    )


def import_provider_configuration(
    source_engine: Engine,
    target_engine: Engine,
    *,
    apply: bool = False,
    owner_mapping: ProviderOwnerMapping | None = None,
) -> ProviderCutoverReport:
    """Validate and optionally migrate provider configuration into an empty target.

    Both engines are supplied by the caller so deployment code can keep the
    target DSN in the process environment instead of placing it on a command
    line.  The source engine is never mutated.
    """

    if str(source_engine.url) == str(target_engine.url):
        raise ProviderCutoverError("source and target databases must differ")

    providers, models, credentials, policies, _ = _required_source_rows(
        source_engine
    )
    providers, models, duplicate_providers_skipped = _deduplicate_providers(providers, models)
    referenced_credentials = {
        str(provider["credential_id"])
        for provider in providers
        if provider.get("credential_id")
    }
    orphan_credentials = len(credentials) - len(referenced_credentials)
    _validate_owner_mapping(providers, models, policies, owner_mapping)
    target_tables = {name: _table(target_engine, name) for name in _CONFIG_TABLES}
    _assert_target_is_empty(target_engine, target_tables)
    _assert_target_identities(target_engine, providers, owner_mapping)
    (
        provider_records,
        model_records,
        credential_records,
        policy_records,
        encrypted_from_legacy,
    ) = _prepare_records(
        providers,
        models,
        credentials,
        policies,
        target_tables,
        owner_mapping,
    )

    if apply:
        with target_engine.begin() as connection:
            if credential_records:
                connection.execute(
                    target_tables["provider_credentials"].insert(), credential_records
                )
            if provider_records:
                connection.execute(target_tables["llm_providers"].insert(), provider_records)
            if model_records:
                connection.execute(target_tables["llm_models"].insert(), model_records)
            if policy_records:
                connection.execute(
                    target_tables["model_fallback_policies"].insert(), policy_records
                )

    return ProviderCutoverReport(
        mode="apply" if apply else "dry-run",
        providers=len(provider_records),
        models=len(model_records),
        credentials_imported=len(credential_records) - encrypted_from_legacy,
        credentials_encrypted_from_legacy=encrypted_from_legacy,
        fallback_policies=len(policy_records),
        orphan_credentials_skipped=orphan_credentials,
        duplicate_providers_skipped=duplicate_providers_skipped,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-sqlite-path",
        required=True,
        help="legacy SQLite database path; it is opened read-only by this command",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the validated configuration to the target; default is dry-run",
    )
    parser.add_argument("--source-tenant-id")
    parser.add_argument("--source-user-id")
    parser.add_argument("--target-tenant-id")
    parser.add_argument("--target-user-id")
    args = parser.parse_args(argv)
    source_path = Path(args.source_sqlite_path).expanduser().resolve()
    if not source_path.is_file():
        print("provider cutover refused: source SQLite database is unavailable")
        return 78

    try:
        mapping_values = (
            args.source_tenant_id,
            args.source_user_id,
            args.target_tenant_id,
            args.target_user_id,
        )
        if any(mapping_values) and not all(mapping_values):
            raise ProviderCutoverError("explicit owner mapping is incomplete")
        owner_mapping = (
            ProviderOwnerMapping(*mapping_values) if all(mapping_values) else None
        )
        source_engine = build_engine(f"sqlite:///{source_path}")
        report = import_provider_configuration(
            source_engine,
            get_engine(),
            apply=args.apply,
            owner_mapping=owner_mapping,
        )
    except ProviderCutoverError:
        print("provider cutover refused")
        return 78

    print(
        f"status={report.mode} providers={report.providers} models={report.models} "
        f"credentials_imported={report.credentials_imported} "
        f"credentials_encrypted={report.credentials_encrypted_from_legacy} "
        f"fallback_policies={report.fallback_policies} "
        f"orphan_credentials_skipped={report.orphan_credentials_skipped} "
        f"duplicate_providers_skipped={report.duplicate_providers_skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
