"""Chat graph migration for PH4 (Branching Conversations & Turn Registry).

Per spec `10-database-changes.md` section 3/4 (``v4_007_chat_graph``) this
migration owns the assistant conversation graph:

* ``conversation_branches`` — the branch DAG.  Every conversation gets exactly
  one *root* branch (``parent_branch_id IS NULL``); Edit/Regenerate forks a new
  branch at a fork message instead of mutating history.
* ``message_parts`` — ordered, typed message payload parts
  (``TEXT`` / ``IMAGE_REF`` / ``ATTACHMENT_REF`` / ``CITATION_MARKER``).
* ``context_summaries`` — deterministic, reusable context compaction keyed by
  ``(branch_id, source_hash, prompt_version)``.
* ``turn_resource_snapshots`` — the immutable resource set a turn actually ran
  against (KB / attachment / model parameters), keyed by ``qa_turns.turn_id``.
* ``message_citations`` — per-message citation rows with an exclusive-or between
  a KB document version and an attachment.
* ALTERs on ``conversations`` / ``messages`` / ``qa_turns`` adding the branch
  pointer, the message graph edges and the turn route/terminal columns.

Backfill order is fixed by spec section 3: root branch per conversation → assign
existing messages to that branch ordered by ``(created_at, id)`` and compute
``content_hash`` → set ``conversations.active_branch_id`` → pair
``qa_turns.user_message_id`` from the adjacent user/assistant pair → map legacy
turn status/time.  A turn whose user message cannot be paired *unambiguously* is
left ``NULL`` and counted as a legacy disposition — the migration never guesses.

``qa_turns.status`` is backfilled to the canonical uppercase vocabulary fixed by
spec section 3 (``running→RUNNING``, ``completed→COMPLETED``,
``cancelled→STOPPED``, ``timeout|error→FAILED``); ``finish_reason`` is retained
verbatim.  The lowercase wire vocabulary stays stable because ``store.py`` maps
between the canonical DB value and the API value at its single projection point.

``message_citations.attachment_id`` intentionally carries no column FK here:
``attachments`` is owned by ``v4_008`` (PH5), which adds the constraint.  The
cross-table tenant invariant is enforced by the service verifier in the writing
transaction, per spec section 3.

The module structure (``VERSION`` / ``V4_007_CHECKSUM`` evaluated at import,
``apply_v4_007`` / ``verify_v4_007`` / ``rollback_v4_007_dry_run``) and the
``__TIME__`` / ``JSON`` dialect placeholders are copied verbatim from
``v4_006_storage_ingestion``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_006_storage_ingestion import V4_006_CHECKSUM

VERSION = "v4_007_chat_graph"
V4_007_OWNED_TABLES = (
    "conversation_branches",
    "message_parts",
    "context_summaries",
    "turn_resource_snapshots",
    "message_citations",
)
V4_007_ROLLBACK_POLICY = (
    "dry-run only; branches, message parts, context summaries, turn snapshots and "
    "citations are conversation history and must not be dropped; use a forward-fix "
    "to evolve the chat graph contract"
)

# Canonical uppercase turn state vocabulary (spec section 3 fixed mapping).
TURN_STATUS_BACKFILL = {
    "running": "RUNNING",
    "completed": "COMPLETED",
    "cancelled": "STOPPED",
    "timeout": "FAILED",
    "error": "FAILED",
}
# Canonical turn vocabulary = the spec section 5 state machine plus ``RUNNING``,
# which is legacy-only (produced by the fixed backfill map, never written by the
# PH4 engine).  Terminal states are COMPLETED / STOPPED / FAILED.
TURN_STATES = (
    "QUEUED",
    "RETRIEVING",
    "BUILDING_CONTEXT",
    "STREAMING",
    "CANCEL_REQUESTED",
    "COMPLETED",
    "STOPPED",
    "FAILED",
    "RUNNING",
)
_TURN_STATE_SQL_LIST = ",".join(f"'{state}'" for state in TURN_STATES)
TERMINAL_EVENT_BY_STATE = {
    "COMPLETED": "turn.completed",
    "STOPPED": "turn.stopped",
    "FAILED": "turn.failed",
}

# ---- Canonical DDL (PostgreSQL contract with __TIME__ / JSON placeholders) ----

_CONVERSATION_BRANCHES_DDL = (
    "CREATE TABLE IF NOT EXISTS conversation_branches ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
    " parent_branch_id text REFERENCES conversation_branches(id),"
    " fork_message_id text,"
    " label text,"
    " created_by text NOT NULL,"
    " created_at __TIME__ NOT NULL"
    ")"
)

_MESSAGE_PARTS_DDL = (
    "CREATE TABLE IF NOT EXISTS message_parts ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,"
    " part_type text NOT NULL CHECK (part_type IN "
    "('TEXT','IMAGE_REF','ATTACHMENT_REF','CITATION_MARKER')),"
    " ordinal integer NOT NULL CHECK (ordinal >= 0),"
    " text_content text,"
    " json_content json,"
    " resource_id text,"
    " UNIQUE (message_id, ordinal)"
    ")"
)

_CONTEXT_SUMMARIES_DDL = (
    "CREATE TABLE IF NOT EXISTS context_summaries ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
    " branch_id text NOT NULL REFERENCES conversation_branches(id) ON DELETE CASCADE,"
    " first_message_id text NOT NULL REFERENCES messages(id),"
    " last_message_id text NOT NULL REFERENCES messages(id),"
    " source_hash char(64) NOT NULL,"
    " model_id text NOT NULL,"
    " prompt_version text NOT NULL,"
    " before_tokens integer NOT NULL CHECK (before_tokens >= 0),"
    " after_tokens integer NOT NULL CHECK (after_tokens >= 0),"
    " summary_text text NOT NULL,"
    " covered_message_ids JSON,"
    " quality_state text NOT NULL DEFAULT 'ok'"
    "   CHECK (quality_state IN ('ok','degraded','failed')),"
    " failure_reason text,"
    " created_at __TIME__ NOT NULL,"
    " UNIQUE (branch_id, source_hash, prompt_version)"
    ")"
)

_TURN_RESOURCE_SNAPSHOTS_DDL = (
    "CREATE TABLE IF NOT EXISTS turn_resource_snapshots ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,"
    " resource_type text NOT NULL,"
    " resource_id text NOT NULL,"
    " resource_version_id text,"
    " parameters json NOT NULL DEFAULT '{}',"
    " ordinal integer NOT NULL CHECK (ordinal >= 0),"
    " UNIQUE (turn_id, resource_type, resource_id, ordinal)"
    ")"
)

_MESSAGE_CITATIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS message_citations ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,"
    " document_id text,"
    " document_version_id text,"
    " chunk_id text,"
    " attachment_id text,"
    " locator json NOT NULL,"
    " display_snapshot json NOT NULL,"
    " rank integer NOT NULL CHECK (rank >= 0),"
    " CHECK ((document_version_id IS NOT NULL) <> (attachment_id IS NOT NULL)),"
    " UNIQUE (message_id, rank)"
    ")"
)

# ALTER column additions (dialect-neutral; normalized at apply time).
_CONVERSATION_COLUMNS = {
    "active_branch_id": "text",
    "title_locked": "integer NOT NULL DEFAULT 0",
}
_MESSAGE_COLUMNS = {
    "branch_id": "text",
    "parent_message_id": "text",
    "status": "text NOT NULL DEFAULT 'completed'",
    "content_hash": "char(64)",
}
_QA_TURN_COLUMNS = {
    "user_message_id": "text",
    "requested_provider_id": "text",
    "requested_model_id": "text",
    "actual_provider_id": "text",
    "actual_model_id": "text",
    "state_version": "integer NOT NULL DEFAULT 0",
    "terminal_seq": "integer",
    "terminal_event": "text",
    "updated_at": "__TIME__",
}

_INDEX_DDL = [
    # Exactly one root branch per conversation; forks always carry a parent.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_root_branch "
    "ON conversation_branches (conversation_id) WHERE parent_branch_id IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_conversation_branches_conversation "
    "ON conversation_branches (tenant_id, conversation_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_branch_order "
    "ON messages (branch_id, created_at) WHERE branch_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_message_parts_message "
    "ON message_parts (message_id, ordinal)",
    "CREATE INDEX IF NOT EXISTS ix_context_summaries_branch "
    "ON context_summaries (branch_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_turn_resource_snapshots_turn "
    "ON turn_resource_snapshots (turn_id, ordinal)",
    "CREATE INDEX IF NOT EXISTS ix_message_citations_message "
    "ON message_citations (message_id, rank)",
    "CREATE INDEX IF NOT EXISTS ix_qa_turns_state "
    "ON qa_turns (tenant_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_qa_turns_user_message "
    "ON qa_turns (user_message_id) WHERE user_message_id IS NOT NULL",
]

# Table-level FK constraints are PostgreSQL-only (SQLite cannot ADD CONSTRAINT).
_FK_DDL = [
    "ALTER TABLE conversations ADD CONSTRAINT fk_conversations_active_branch "
    "FOREIGN KEY (active_branch_id) REFERENCES conversation_branches(id)",
    "ALTER TABLE messages ADD CONSTRAINT fk_messages_branch "
    "FOREIGN KEY (branch_id) REFERENCES conversation_branches(id)",
    "ALTER TABLE messages ADD CONSTRAINT fk_messages_parent "
    "FOREIGN KEY (parent_message_id) REFERENCES messages(id)",
    "ALTER TABLE qa_turns ADD CONSTRAINT fk_qa_turns_user_message "
    "FOREIGN KEY (user_message_id) REFERENCES messages(id)",
]

# Canonical DDL used to derive the migration checksum (module-load invariant).
_CANONICAL_DDL = "\n".join(
    [
        _CONVERSATION_BRANCHES_DDL,
        _MESSAGE_PARTS_DDL,
        _CONTEXT_SUMMARIES_DDL,
        _TURN_RESOURCE_SNAPSHOTS_DDL,
        _MESSAGE_CITATIONS_DDL,
    ]
    + [
        f"ALTER TABLE {t} ADD COLUMN {name} {definition}"
        for t, cols in (
            ("conversations", _CONVERSATION_COLUMNS),
            ("messages", _MESSAGE_COLUMNS),
            ("qa_turns", _QA_TURN_COLUMNS),
        )
        for name, definition in cols.items()
    ]
    + _INDEX_DDL
    + _FK_DDL
    + [f"BACKFILL status {key}->{value}" for key, value in sorted(TURN_STATUS_BACKFILL.items())]
)

V4_007_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_CANONICAL_DDL.split())
        + "\nrollback="
        + V4_007_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def branch_id_for_conversation(conversation_id: str) -> str:
    """Deterministic root-branch id so the backfill is replay-safe."""
    digest = hashlib.sha256(f"{VERSION}:root:{conversation_id}".encode()).hexdigest()
    return f"cbr_{digest[:32]}"


def message_content_hash(role: str, content: str) -> str:
    """Canonical message content hash used for dedup and context source hashing."""
    payload = f"{(role or '').strip().lower()}\n{content or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_ddl(statement: str, dialect: str) -> str:
    if dialect == "postgresql":
        return statement.replace(" json", " JSONB").replace("__TIME__", "TIMESTAMPTZ")
    if dialect == "sqlite":
        return statement.replace("__TIME__", "VARCHAR(64)")
    raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")


def _create_tables_ddl() -> list[str]:
    return [
        _CONVERSATION_BRANCHES_DDL,
        _MESSAGE_PARTS_DDL,
        _CONTEXT_SUMMARIES_DDL,
        _TURN_RESOURCE_SNAPSHOTS_DDL,
        _MESSAGE_CITATIONS_DDL,
    ]


def _alter_columns() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for table, cols in (
        ("conversations", _CONVERSATION_COLUMNS),
        ("messages", _MESSAGE_COLUMNS),
        ("qa_turns", _QA_TURN_COLUMNS),
    ):
        for name, definition in cols.items():
            out.append((table, name, definition))
    return out


def _ensure_column(connection: Connection, table: str, name: str, definition: str) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns(table)}
    if name in existing:
        return
    connection.execute(
        text(
            f"ALTER TABLE {table} ADD COLUMN {name} "
            f"{_normalize_ddl(definition, connection.dialect.name)}"
        )
    )


def _ledger(connection: Connection, version: str) -> Any:
    return connection.execute(
        text("SELECT checksum, manifest_position FROM migration_provenance WHERE version=:version"),
        {"version": version},
    ).first()


def _audit(connection: Connection, action: str, outcome: str, detail: dict[str, Any]) -> None:
    from uuid import uuid4

    expression = "CAST(:detail AS JSONB)" if connection.dialect.name == "postgresql" else ":detail"
    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id,:version,:action,:outcome,{expression},:recorded_at)"
        ),
        {
            "id": str(uuid4()),
            "version": VERSION,
            "action": action,
            "outcome": outcome,
            "detail": json.dumps(detail, sort_keys=True, separators=(",", ":")),
            "recorded_at": utc_now(),
        },
    )


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    owned = set(V4_007_OWNED_TABLES)
    for table in V4_007_OWNED_TABLES + ("conversations", "messages", "qa_turns"):
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
        owned.add(table)

    expected_columns = {
        "conversations": set(_CONVERSATION_COLUMNS),
        "messages": set(_MESSAGE_COLUMNS),
        "qa_turns": set(_QA_TURN_COLUMNS),
        # Spec section 8 requires the summary to record its covered range and
        # its quality/failure state, not just the text.
        "context_summaries": {
            "covered_message_ids",
            "quality_state",
            "failure_reason",
            "source_hash",
            "before_tokens",
            "after_tokens",
        },
    }
    for table, expected in expected_columns.items():
        present = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(expected - present)
        if missing:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={','.join(missing)}"
            )

    indexes: set[str] = set()
    for table in owned:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                indexes.add(str(name))
    expected_indexes = {
        "uq_conversation_root_branch",
        "ix_conversation_branches_conversation",
        "ix_messages_branch_order",
        "ix_message_parts_message",
        "ix_context_summaries_branch",
        "ix_turn_resource_snapshots_turn",
        "ix_message_citations_message",
        "ix_qa_turns_state",
        "ix_qa_turns_user_message",
    }
    missing_indexes = sorted(expected_indexes - indexes)
    if missing_indexes:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={','.join(missing_indexes)}"
        )
    return tuple(sorted(indexes))


def _backfill_root_branches(connection: Connection, now: str) -> int:
    """Create the root branch for every conversation that lacks one."""
    rows = connection.execute(
        text(
            "SELECT c.id, c.tenant_id, c.user_id FROM conversations c "
            "WHERE NOT EXISTS (SELECT 1 FROM conversation_branches b "
            "WHERE b.conversation_id = c.id AND b.parent_branch_id IS NULL)"
        )
    ).fetchall()
    for conversation_id, tenant_id, user_id in rows:
        connection.execute(
            text(
                "INSERT INTO conversation_branches "
                "(id, tenant_id, conversation_id, parent_branch_id, fork_message_id,"
                " label, created_by, created_at) "
                "VALUES (:id,:tenant_id,:conversation_id,NULL,NULL,'root',:created_by,:created_at)"
            ),
            {
                "id": branch_id_for_conversation(str(conversation_id)),
                "tenant_id": str(tenant_id),
                "conversation_id": str(conversation_id),
                "created_by": str(user_id),
                "created_at": now,
            },
        )
    return len(rows)


def _backfill_messages(connection: Connection) -> dict[str, int]:
    """Assign legacy messages to the root branch and compute content hashes.

    Ordering is ``(created_at, id)`` per spec; ``parent_message_id`` reconstructs
    the legacy linear history inside the root branch.
    """
    rows = connection.execute(
        text(
            "SELECT m.id, m.conversation_id, m.role, m.content, m.branch_id, m.content_hash "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "ORDER BY m.conversation_id, m.created_at, m.id"
        )
    ).fetchall()

    branched = 0
    hashed = 0
    linked = 0
    previous_conversation: Optional[str] = None
    previous_message_id: Optional[str] = None
    for message_id, conversation_id, role, content, branch_id, content_hash in rows:
        if conversation_id != previous_conversation:
            previous_conversation = conversation_id
            previous_message_id = None
        if not branch_id:
            connection.execute(
                text("UPDATE messages SET branch_id=:branch_id WHERE id=:id"),
                {
                    "branch_id": branch_id_for_conversation(str(conversation_id)),
                    "id": str(message_id),
                },
            )
            branched += 1
        if not content_hash:
            connection.execute(
                text("UPDATE messages SET content_hash=:content_hash WHERE id=:id"),
                {
                    "content_hash": message_content_hash(str(role or ""), str(content or "")),
                    "id": str(message_id),
                },
            )
            hashed += 1
        if previous_message_id is not None:
            connection.execute(
                text(
                    "UPDATE messages SET parent_message_id=:parent WHERE id=:id "
                    "AND parent_message_id IS NULL"
                ),
                {"parent": previous_message_id, "id": str(message_id)},
            )
            linked += 1
        previous_message_id = str(message_id)

    connection.execute(
        text(
            "UPDATE conversations SET active_branch_id = ("
            "SELECT b.id FROM conversation_branches b "
            "WHERE b.conversation_id = conversations.id AND b.parent_branch_id IS NULL"
            ") WHERE active_branch_id IS NULL"
        )
    )
    return {"messages_branched": branched, "messages_hashed": hashed, "messages_linked": linked}


def _backfill_turns(connection: Connection, now: str) -> dict[str, int]:
    """Map legacy turn status/time and pair the user message when unambiguous."""
    status_updates = 0
    for legacy, canonical in TURN_STATUS_BACKFILL.items():
        result = connection.execute(
            text("UPDATE qa_turns SET status=:canonical WHERE status=:legacy"),
            {"canonical": canonical, "legacy": legacy},
        )
        status_updates += int(result.rowcount or 0)

    unknown = connection.execute(
        text(f"SELECT COUNT(*) FROM qa_turns WHERE status NOT IN ({_TURN_STATE_SQL_LIST})")
    ).scalar_one()
    if int(unknown or 0) > 0:
        raise RuntimeError(
            f"{VERSION} backfill failed: {unknown} qa_turns rows carry an unmapped status; "
            "the spec status mapping is fixed and must not be guessed"
        )

    connection.execute(text(_turn_updated_at_backfill_sql(connection.dialect.name)))
    for state, event in TERMINAL_EVENT_BY_STATE.items():
        connection.execute(
            text(
                "UPDATE qa_turns SET terminal_seq=last_seq, terminal_event=:event "
                "WHERE status=:state AND terminal_event IS NULL"
            ),
            {"event": event, "state": state},
        )
    connection.execute(
        text(
            "UPDATE qa_turns SET actor_id=("
            "SELECT c.user_id FROM conversations c WHERE c.id = qa_turns.conversation_id"
            ") WHERE (actor_id IS NULL OR actor_id = '') AND conversation_id IS NOT NULL"
        )
    )

    # Pair the user message only when the message immediately preceding the
    # assistant message in (created_at, id) order is a user message.
    candidates = connection.execute(
        text(
            "SELECT turn_id, conversation_id, assistant_message_id FROM qa_turns "
            "WHERE user_message_id IS NULL AND assistant_message_id IS NOT NULL "
            "AND conversation_id IS NOT NULL"
        )
    ).fetchall()
    paired = 0
    unpaired = 0
    for turn_id, conversation_id, assistant_message_id in candidates:
        anchor = connection.execute(
            text("SELECT created_at FROM messages WHERE id=:id"),
            {"id": str(assistant_message_id)},
        ).first()
        if anchor is None:
            unpaired += 1
            continue
        previous = connection.execute(
            text(
                "SELECT id, role FROM messages WHERE conversation_id=:conversation_id "
                "AND (created_at < :anchor OR (created_at = :anchor AND id < :assistant_id)) "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {
                "conversation_id": str(conversation_id),
                "anchor": anchor[0],
                "assistant_id": str(assistant_message_id),
            },
        ).first()
        if previous is None or str(previous[1] or "").lower() != "user":
            unpaired += 1
            continue
        connection.execute(
            text("UPDATE qa_turns SET user_message_id=:user_message_id WHERE turn_id=:turn_id"),
            {"user_message_id": str(previous[0]), "turn_id": str(turn_id)},
        )
        paired += 1

    # The legacy disposition is *not* audited here: ``migration_audit.version``
    # has an FK to ``migration_provenance``, whose row is only written after the
    # backfill.  It is folded into the single APPLY audit at the end instead.
    return {
        "turn_status_mapped": status_updates,
        "turn_user_message_paired": paired,
        "turn_user_message_unpaired": unpaired,
    }


def _turn_updated_at_backfill_sql(dialect: str) -> str:
    """Return the legacy timestamp backfill with PostgreSQL's explicit cast."""

    if dialect == "postgresql":
        return (
            "UPDATE qa_turns SET updated_at=COALESCE("
            "NULLIF(completed_at, '')::TIMESTAMPTZ, "
            "NULLIF(created_at, '')::TIMESTAMPTZ) WHERE updated_at IS NULL"
        )
    return (
        "UPDATE qa_turns SET updated_at=COALESCE(completed_at, created_at) "
        "WHERE updated_at IS NULL"
    )


def apply_v4_007(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_006_storage_ingestion")
        if previous is None or str(previous[0]) != V4_006_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_006 storage ingestion")
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) == V4_007_CHECKSUM:
            _audit(connection, "APPLY", "NO_OP", {"chat_graph": "already_applied"})
            return MigrationResult(
                VERSION, V4_007_CHECKSUM, False, {"chat_graph": "already_applied"}
            )

        dialect = connection.dialect.name
        for statement in _create_tables_ddl():
            connection.execute(text(_normalize_ddl(statement, dialect)))
        for table, name, definition in _alter_columns():
            _ensure_column(connection, table, name, definition)
        for statement in _INDEX_DDL:
            connection.execute(text(_normalize_ddl(statement, dialect)))
        if dialect == "postgresql":
            for statement in _FK_DDL:
                connection.execute(text(statement))

        now = utc_now()
        counts: dict[str, int | str] = {}
        counts["root_branches"] = _backfill_root_branches(connection, now)
        counts.update(_backfill_messages(connection))
        counts.update(_backfill_turns(connection, now))

        _catalog(connection)

        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"chat_graph": "schema_refreshed"})
            return MigrationResult(
                VERSION, V4_007_CHECKSUM, False, {"chat_graph": "schema_refreshed"}
            )

        position = int(previous[1]) + 1
        expression = (
            "CAST(:provenance AS JSONB)" if dialect == "postgresql" else ":provenance"
        )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version, owner, checksum, applied_at, verify_status, provenance,"
                "manifest_position, created_at) "
                "VALUES (:version, :owner, :checksum, :applied_at, 'VERIFIED',"
                f"{expression}, :position, :created_at)"
            ),
            {
                "version": VERSION,
                "owner": "assistant",
                "checksum": V4_007_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {
                        "tables": list(V4_007_OWNED_TABLES),
                        "backfill": {key: value for key, value in counts.items()},
                        "message_branch_watermark": now,
                    },
                    sort_keys=True,
                ),
                "position": position,
                "created_at": now,
            },
        )
        detail: dict[str, object] = {key: value for key, value in counts.items()}
        if int(counts.get("turn_user_message_unpaired") or 0) > 0:
            detail["disposition"] = "LEGACY_UNPAIRED_USER_MESSAGE"
            detail["policy"] = (
                "regenerate disabled until repaired; user message never guessed"
            )
        _audit(connection, "APPLY", "PASS", detail)
    return MigrationResult(VERSION, V4_007_CHECKSUM, True, counts)


def verify_v4_007(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_007_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)

        missing_branch = connection.execute(
            text(
                "SELECT COUNT(*) FROM conversations c WHERE NOT EXISTS "
                "(SELECT 1 FROM conversation_branches b WHERE b.conversation_id = c.id "
                "AND b.parent_branch_id IS NULL)"
            )
        ).scalar_one()
        if int(missing_branch or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {missing_branch} conversations without root branch"
            )

        missing_active = connection.execute(
            text("SELECT COUNT(*) FROM conversations WHERE active_branch_id IS NULL")
        ).scalar_one()
        if int(missing_active or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {missing_active} conversations without active branch"
            )

        watermark = connection.execute(
            text("SELECT applied_at FROM migration_provenance WHERE version=:version"),
            {"version": VERSION},
        ).scalar_one()
        unbranched = connection.execute(
            text(
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE m.branch_id IS NULL AND m.created_at <= :watermark"
            ),
            {"watermark": watermark},
        ).scalar_one()
        if int(unbranched or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {unbranched} legacy messages without branch"
            )

        bad_status = connection.execute(
            text(f"SELECT COUNT(*) FROM qa_turns WHERE status NOT IN ({_TURN_STATE_SQL_LIST})")
        ).scalar_one()
        if int(bad_status or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {bad_status} qa_turns with non-canonical status"
            )

        orphan_citations = connection.execute(
            text(
                "SELECT COUNT(*) FROM message_citations mc LEFT JOIN messages m "
                "ON m.id = mc.message_id AND m.tenant_id = mc.tenant_id "
                "WHERE m.id IS NULL"
            )
        ).scalar_one()
        if int(orphan_citations or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {orphan_citations} cross-tenant/orphan citations"
            )

        orphan_snapshots = connection.execute(
            text(
                "SELECT COUNT(*) FROM turn_resource_snapshots s LEFT JOIN qa_turns t "
                "ON t.turn_id = s.turn_id AND t.tenant_id = s.tenant_id "
                "WHERE t.turn_id IS NULL"
            )
        ).scalar_one()
        if int(orphan_snapshots or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {orphan_snapshots} cross-tenant/orphan snapshots"
            )

        _audit(connection, "VERIFY", "PASS", {"chat_graph": "branches+parts+turns"})
    return VerificationResult("PASS", VERSION, V4_007_CHECKSUM, V4_007_OWNED_TABLES, indexes)


def rollback_v4_007_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_007_OWNED_TABLES))
        if str(row[0]) != V4_007_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_007_ROLLBACK_POLICY})
    return RollbackPlan(True, (), tuple(V4_007_OWNED_TABLES), V4_007_ROLLBACK_POLICY)
