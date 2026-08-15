"""PH4 turn registry & state machine (spec 05-ai-chat.md sections 4, 5, 7, 11).

The turn is the unit of cancellation and the unit of audit.  Two properties are
non-negotiable and both are enforced here rather than by convention:

**Single-transaction creation (section 4).**  Resolving the actor, locking the
conversation, creating the user message, the turn, its resource snapshot and the
assistant placeholder all happen in *one* transaction.  A duplicate
``client_turn_id`` returns the original turn instead of producing a second pair
of messages.

**First-terminal-wins (section 5).**  Every transition is a single CAS carrying
``tenant_id + turn_id + expected_state``.  When completion and cancellation race,
the first successful CAS is authoritative; the loser reads the actual terminal
state and returns it *without* writing message state or emitting a second
terminal event.  ``TurnTerminalConflict`` carries that observed state so the
caller can report the truth instead of guessing.

``qa_turns.turn_id`` stays the canonical PK and API id — no second UUID id is
introduced (spec 10-database-changes.md section 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Connection, Engine, text

from ekb_api.domain import new_id, utc_now
from ekb_api.services.conversations import (
    ConversationGraphService,
    ConversationNotFound,
)

QUEUED = "QUEUED"
RETRIEVING = "RETRIEVING"
BUILDING_CONTEXT = "BUILDING_CONTEXT"
STREAMING = "STREAMING"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
COMPLETED = "COMPLETED"
STOPPED = "STOPPED"
FAILED = "FAILED"

TERMINAL_STATES = frozenset({COMPLETED, STOPPED, FAILED})
ACTIVE_STATES = (QUEUED, RETRIEVING, BUILDING_CONTEXT, STREAMING)

# Spec section 5 transition table. CANCEL_REQUESTED is reachable from every
# active state; FAILED is reachable from every non-terminal state.
_ALLOWED: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RETRIEVING, CANCEL_REQUESTED, FAILED}),
    RETRIEVING: frozenset({BUILDING_CONTEXT, CANCEL_REQUESTED, FAILED}),
    BUILDING_CONTEXT: frozenset({STREAMING, CANCEL_REQUESTED, FAILED}),
    STREAMING: frozenset({COMPLETED, CANCEL_REQUESTED, FAILED}),
    CANCEL_REQUESTED: frozenset({STOPPED, FAILED}),
}

TERMINAL_EVENT = {
    COMPLETED: "turn.completed",
    STOPPED: "turn.stopped",
    FAILED: "turn.failed",
}

# Spec section 11: these classes must never trigger provider fallback.
NON_FALLBACK_ERROR_CODES = frozenset(
    {
        "TENANT_FORBIDDEN",
        "ACL_REVOKED",
        "ATTACHMENT_POLICY_VIOLATION",
        "EGRESS_POLICY_BLOCKED",
        "MIGRATION_REQUIRED",
        "SCHEMA_MISMATCH",
        "PERSISTENCE_FAILED",
    }
)


class TurnNotFound(Exception):
    """Turn missing or outside the caller's tenant."""


class TurnTransitionError(Exception):
    """Transition not allowed by the spec section 5 state machine."""


class TurnTerminalConflict(Exception):
    """Lost a first-terminal-wins race; ``actual_state`` is authoritative."""

    def __init__(self, turn_id: str, actual_state: str) -> None:
        super().__init__(f"turn {turn_id} already terminal in state {actual_state}")
        self.turn_id = turn_id
        self.actual_state = actual_state


@dataclass(frozen=True)
class TurnView:
    turn_id: str
    request_id: str
    tenant_id: str
    actor_id: str
    conversation_id: Optional[str]
    branch_id: Optional[str]
    user_message_id: Optional[str]
    assistant_message_id: Optional[str]
    state: str
    state_version: int
    last_seq: int
    terminal_seq: Optional[int]
    terminal_event: Optional[str]
    requested_provider_id: Optional[str]
    requested_model_id: Optional[str]
    actual_provider_id: Optional[str]
    actual_model_id: Optional[str]
    finish_reason: Optional[str]
    created_at: str
    updated_at: Optional[str]
    completed_at: Optional[str]


@dataclass(frozen=True)
class TurnCreation:
    turn: TurnView
    branch_id: str
    user_message_id: str
    assistant_message_id: str
    reused: bool
    snapshots: tuple[dict[str, object], ...] = field(default=())


def is_fallback_allowed(error_code: str) -> bool:
    """Spec section 11 — ACL/policy/schema/persistence failures never fall back."""
    return error_code.upper() not in NON_FALLBACK_ERROR_CODES


class TurnService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.graph = ConversationGraphService(engine)

    # ---- creation (spec section 4) ----

    def create_turn(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
        client_turn_id: str,
        request_id: str,
        prompt: str,
        branch_id: Optional[str] = None,
        requested_provider_id: Optional[str] = None,
        requested_model_id: Optional[str] = None,
        answer_mode: str = "knowledge_enhanced",
        resources: Optional[list[dict[str, object]]] = None,
    ) -> TurnCreation:
        with self.engine.begin() as connection:
            conversation = self.graph.load_conversation(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                actor_id=actor_id,
                connection=connection,
            )

            existing = connection.execute(
                text(
                    "SELECT turn_id FROM qa_turns WHERE tenant_id=:tenant_id "
                    "AND actor_id=:actor_id AND conversation_id=:conversation_id "
                    "AND client_turn_id=:client_turn_id"
                ),
                {
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "client_turn_id": client_turn_id,
                },
            ).first()
            if existing is not None:
                turn = self._load(connection, tenant_id=tenant_id, turn_id=str(existing[0]))
                return TurnCreation(
                    turn=turn,
                    branch_id=str(turn.branch_id or conversation["active_branch_id"]),
                    user_message_id=str(turn.user_message_id or ""),
                    assistant_message_id=str(turn.assistant_message_id or ""),
                    reused=True,
                )

            target_branch = branch_id or conversation["active_branch_id"]
            if not target_branch:
                target_branch = self.graph.ensure_root_branch(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    user_id=str(conversation["user_id"]),
                    connection=connection,
                ).id

            user_message = self.graph.append_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                branch_id=str(target_branch),
                role="user",
                content=prompt,
                status="completed",
                connection=connection,
            )
            turn_id = new_id()
            assistant_message = self.graph.append_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                branch_id=str(target_branch),
                role="assistant",
                content="",
                status="pending",
                turn_id=turn_id,
                parent_message_id=user_message.id,
                connection=connection,
            )

            now = utc_now()
            connection.execute(
                text(
                    "INSERT INTO qa_turns "
                    "(turn_id, request_id, client_turn_id, tenant_id, actor_id,"
                    " conversation_id, assistant_message_id, user_message_id,"
                    " stream_version, status, last_seq, state_version,"
                    " requested_provider_id, requested_model_id, created_at, updated_at) "
                    "VALUES (:turn_id,:request_id,:client_turn_id,:tenant_id,:actor_id,"
                    ":conversation_id,:assistant_message_id,:user_message_id,2,:status,0,0,"
                    ":requested_provider_id,:requested_model_id,:created_at,:updated_at)"
                ),
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "client_turn_id": client_turn_id,
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message.id,
                    "user_message_id": user_message.id,
                    "status": QUEUED,
                    "requested_provider_id": requested_provider_id,
                    "requested_model_id": requested_model_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )

            # Create the first attempt row (PH0 v4_010 turn_attempts table).
            # capability_snapshot is filled by GenerationWorker when the model
            # is resolved; here we record the QUEUED attempt for lease/recovery.
            attempt_id = new_id()
            _insert_attempt_ddl = (
                "INSERT INTO turn_attempts "
                "(id, tenant_id, turn_id, attempt_no, capability_snapshot,"
                " sampling_snapshot, state, created_at) "
                "VALUES (:id,:tenant_id,:turn_id,1,:capability_snapshot,"
                "'{}',:state,:created_at)"
            )
            if connection.dialect.name == "postgresql":
                _insert_attempt_ddl = _insert_attempt_ddl.replace(
                    "'{}'", "'{}'::jsonb"
                ).replace(":capability_snapshot", "CAST(:capability_snapshot AS jsonb)")
            connection.execute(
                text(_insert_attempt_ddl),
                {
                    "id": attempt_id,
                    "tenant_id": tenant_id,
                    "turn_id": turn_id,
                    "capability_snapshot": "{}",
                    "state": "QUEUED",
                    "created_at": now,
                },
            )
            connection.execute(
                text(
                    "UPDATE qa_turns SET active_attempt_id=:attempt_id "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
                ),
                {"attempt_id": attempt_id, "turn_id": turn_id, "tenant_id": tenant_id},
            )

            snapshot_rows = self._write_snapshots(
                connection,
                tenant_id=tenant_id,
                turn_id=turn_id,
                answer_mode=answer_mode,
                branch_id=str(target_branch),
                requested_model_id=requested_model_id,
                resources=resources or [],
            )
            turn = self._load(connection, tenant_id=tenant_id, turn_id=turn_id)

        return TurnCreation(
            turn=turn,
            branch_id=str(target_branch),
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            reused=False,
            snapshots=tuple(snapshot_rows),
        )

    # ---- state machine (spec section 5) ----

    def advance(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        expected_state: str,
        next_state: str,
        actor_id: Optional[str] = None,
    ) -> TurnView:
        if next_state not in _ALLOWED.get(expected_state, frozenset()):
            raise TurnTransitionError(
                f"turn {turn_id}: {expected_state} -> {next_state} is not a legal transition"
            )
        with self.engine.begin() as connection:
            return self._cas(
                connection,
                tenant_id=tenant_id,
                turn_id=turn_id,
                expected_state=expected_state,
                next_state=next_state,
                actor_id=actor_id,
            )

    def request_cancel(
        self, *, tenant_id: str, turn_id: str, actor_id: str
    ) -> tuple[bool, TurnView]:
        """Owner-only Stop. Repeat calls report the current state, never re-write."""
        with self.engine.begin() as connection:
            turn = self._load(connection, tenant_id=tenant_id, turn_id=turn_id)
            if turn.actor_id != actor_id:
                raise TurnNotFound(turn_id)
            if turn.state in TERMINAL_STATES or turn.state == CANCEL_REQUESTED:
                return False, turn
            updated = self._cas(
                connection,
                tenant_id=tenant_id,
                turn_id=turn_id,
                expected_state=turn.state,
                next_state=CANCEL_REQUESTED,
                actor_id=actor_id,
                extra_sql="cancel_requested_at=:cancel_requested_at",
                extra_params={"cancel_requested_at": utc_now()},
            )
            return True, updated

    def finish(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        expected_state: str,
        terminal_state: str,
        finish_reason: str,
        last_seq: int,
        actual_provider_id: Optional[str] = None,
        actual_model_id: Optional[str] = None,
        assistant_content: Optional[str] = None,
    ) -> TurnView:
        """Write the single terminal event for a turn. Loser of a race raises."""
        if terminal_state not in TERMINAL_STATES:
            raise TurnTransitionError(f"{terminal_state} is not a terminal state")
        if terminal_state not in _ALLOWED.get(expected_state, frozenset()):
            raise TurnTransitionError(
                f"turn {turn_id}: {expected_state} -> {terminal_state} is not a legal transition"
            )
        now = utc_now()
        with self.engine.begin() as connection:
            turn = self._cas(
                connection,
                tenant_id=tenant_id,
                turn_id=turn_id,
                expected_state=expected_state,
                next_state=terminal_state,
                extra_sql=(
                    "finish_reason=:finish_reason, last_seq=:last_seq, "
                    "terminal_seq=:terminal_seq, terminal_event=:terminal_event, "
                    "completed_at=:completed_at, actual_provider_id=:actual_provider_id, "
                    "actual_model_id=:actual_model_id"
                ),
                extra_params={
                    "finish_reason": finish_reason,
                    "last_seq": last_seq,
                    "terminal_seq": last_seq,
                    "terminal_event": TERMINAL_EVENT[terminal_state],
                    "completed_at": now,
                    "actual_provider_id": actual_provider_id,
                    "actual_model_id": actual_model_id,
                },
            )
            if turn.assistant_message_id:
                message_status = {
                    COMPLETED: "completed",
                    STOPPED: "stopped",
                    FAILED: "failed",
                }[terminal_state]
                self.graph.update_message(
                    tenant_id=tenant_id,
                    message_id=turn.assistant_message_id,
                    content=assistant_content,
                    status=message_status,
                    connection=connection,
                )
            return turn

    def get(self, *, tenant_id: str, turn_id: str) -> TurnView:
        with self.engine.connect() as connection:
            return self._load(connection, tenant_id=tenant_id, turn_id=turn_id)

    def snapshots(self, *, tenant_id: str, turn_id: str) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT resource_type, resource_id, resource_version_id, parameters, ordinal "
                    "FROM turn_resource_snapshots WHERE tenant_id=:tenant_id AND turn_id=:turn_id "
                    "ORDER BY ordinal"
                ),
                {"tenant_id": tenant_id, "turn_id": turn_id},
            ).fetchall()
        return [
            {
                "resource_type": str(row[0]),
                "resource_id": str(row[1]),
                "resource_version_id": str(row[2]) if row[2] else None,
                "parameters": _load_json(row[3]),
                "ordinal": int(row[4]),
            }
            for row in rows
        ]

    # ---- Retry / Regenerate (spec section 7) ----

    def regenerate(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
        assistant_message_id: str,
        client_turn_id: str,
        request_id: str,
        requested_provider_id: Optional[str] = None,
        requested_model_id: Optional[str] = None,
        activate: bool = True,
    ) -> TurnCreation:
        """Fork a new branch at the originating user message and run a new turn.

        The previous answer stays intact on the parent branch — the UI switches
        versions by switching the active branch.
        """
        with self.engine.begin() as connection:
            source = self.graph._message(  # noqa: SLF001 - same-package invariant read
                connection, tenant_id=tenant_id, message_id=assistant_message_id
            )
            if source.role != "assistant":
                raise TurnTransitionError("regenerate requires an assistant message")
            if source.status not in ("completed", "stopped"):
                raise TurnTransitionError(
                    f"regenerate requires a completed or stopped answer, got {source.status}"
                )
            if not source.parent_message_id:
                raise TurnTransitionError(
                    "regenerate blocked: the originating user message is unknown "
                    "(legacy disposition recorded by v4_007 backfill)"
                )
            user_message = self.graph._message(  # noqa: SLF001
                connection, tenant_id=tenant_id, message_id=source.parent_message_id
            )
            branch = self.graph.fork_branch(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                fork_message_id=user_message.id,
                created_by=actor_id,
                label="regenerate",
                connection=connection,
            )
            prompt = user_message.content
            if activate:
                self.graph.set_active_branch(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    branch_id=branch.id,
                    connection=connection,
                )

        creation = self.create_turn(
            tenant_id=tenant_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            request_id=request_id,
            prompt=prompt,
            branch_id=branch.id,
            requested_provider_id=requested_provider_id,
            requested_model_id=requested_model_id,
        )
        return creation

    def retry(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        turn_id: str,
        client_turn_id: str,
        request_id: str,
        requested_provider_id: Optional[str] = None,
        requested_model_id: Optional[str] = None,
    ) -> TurnCreation:
        """Retry a FAILED turn on the same branch, reusing the user prompt."""
        with self.engine.connect() as connection:
            turn = self._load(connection, tenant_id=tenant_id, turn_id=turn_id)
            if turn.actor_id != actor_id:
                raise TurnNotFound(turn_id)
            if turn.state != FAILED:
                raise TurnTransitionError(f"retry requires a FAILED turn, got {turn.state}")
            if not turn.user_message_id:
                raise TurnTransitionError("retry blocked: turn has no user message")
            user_message = self.graph._message(  # noqa: SLF001
                connection, tenant_id=tenant_id, message_id=turn.user_message_id
            )
        return self.create_turn(
            tenant_id=tenant_id,
            actor_id=actor_id,
            conversation_id=str(turn.conversation_id),
            client_turn_id=client_turn_id,
            request_id=request_id,
            prompt=user_message.content,
            branch_id=user_message.branch_id,
            requested_provider_id=requested_provider_id,
            requested_model_id=requested_model_id,
        )

    # ---- internals ----

    def _cas(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        turn_id: str,
        expected_state: str,
        next_state: str,
        actor_id: Optional[str] = None,
        extra_sql: str = "",
        extra_params: Optional[dict[str, object]] = None,
    ) -> TurnView:
        current = self._load(connection, tenant_id=tenant_id, turn_id=turn_id)
        if actor_id is not None and current.actor_id != actor_id:
            raise TurnNotFound(turn_id)
        params: dict[str, object] = {
            "tenant_id": tenant_id,
            "turn_id": turn_id,
            "expected_state": expected_state,
            "expected_version": current.state_version,
            "next_state": next_state,
            "updated_at": utc_now(),
        }
        params.update(extra_params or {})
        assignment = "status=:next_state, state_version=state_version+1, updated_at=:updated_at"
        if extra_sql:
            assignment = f"{assignment}, {extra_sql}"
        result = connection.execute(
            text(
                f"UPDATE qa_turns SET {assignment} WHERE turn_id=:turn_id "
                "AND tenant_id=:tenant_id AND status=:expected_state "
                "AND state_version=:expected_version"
            ),
            params,
        )
        if not result.rowcount:
            actual = self._load(connection, tenant_id=tenant_id, turn_id=turn_id)
            raise TurnTerminalConflict(turn_id, actual.state)
        return self._load(connection, tenant_id=tenant_id, turn_id=turn_id)

    def _write_snapshots(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        turn_id: str,
        answer_mode: str,
        branch_id: str,
        requested_model_id: Optional[str],
        resources: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = [
            {
                "resource_type": "ANSWER_MODE",
                "resource_id": answer_mode,
                "resource_version_id": None,
                "parameters": {"branch_id": branch_id},
            }
        ]
        if requested_model_id:
            rows.append(
                {
                    "resource_type": "MODEL",
                    "resource_id": requested_model_id,
                    "resource_version_id": None,
                    "parameters": {},
                }
            )
        rows.extend(
            {
                "resource_type": str(item.get("resource_type") or "KB"),
                "resource_id": str(item.get("resource_id")),
                "resource_version_id": item.get("resource_version_id"),
                "parameters": item.get("parameters") or {},
            }
            for item in resources
        )
        expression = (
            "CAST(:parameters AS JSONB)"
            if connection.dialect.name == "postgresql"
            else ":parameters"
        )
        for ordinal, row in enumerate(rows):
            connection.execute(
                text(
                    "INSERT INTO turn_resource_snapshots "
                    "(id, tenant_id, turn_id, resource_type, resource_id,"
                    f" resource_version_id, parameters, ordinal) VALUES "
                    f"(:id,:tenant_id,:turn_id,:resource_type,:resource_id,"
                    f":resource_version_id,{expression},:ordinal)"
                ),
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "turn_id": turn_id,
                    "resource_type": row["resource_type"],
                    "resource_id": row["resource_id"],
                    "resource_version_id": row["resource_version_id"],
                    "parameters": json.dumps(row["parameters"], sort_keys=True),
                    "ordinal": ordinal,
                },
            )
            row["ordinal"] = ordinal
        return rows

    def _load(self, connection: Connection, *, tenant_id: str, turn_id: str) -> TurnView:
        row = connection.execute(
            text(
                "SELECT t.turn_id, t.request_id, t.tenant_id, t.actor_id, t.conversation_id,"
                " m.branch_id, t.user_message_id, t.assistant_message_id, t.status,"
                " t.state_version, t.last_seq, t.terminal_seq, t.terminal_event,"
                " t.requested_provider_id, t.requested_model_id, t.actual_provider_id,"
                " t.actual_model_id, t.finish_reason, t.created_at, t.updated_at,"
                " t.completed_at FROM qa_turns t "
                "LEFT JOIN messages m ON m.id = t.assistant_message_id "
                "AND m.tenant_id = t.tenant_id "
                "WHERE t.turn_id=:turn_id AND t.tenant_id=:tenant_id"
            ),
            {"turn_id": turn_id, "tenant_id": tenant_id},
        ).first()
        if row is None:
            raise TurnNotFound(turn_id)
        return TurnView(
            turn_id=str(row[0]),
            request_id=str(row[1]),
            tenant_id=str(row[2]),
            actor_id=str(row[3]),
            conversation_id=str(row[4]) if row[4] else None,
            branch_id=str(row[5]) if row[5] else None,
            user_message_id=str(row[6]) if row[6] else None,
            assistant_message_id=str(row[7]) if row[7] else None,
            state=str(row[8]),
            state_version=int(row[9] or 0),
            last_seq=int(row[10] or 0),
            terminal_seq=int(row[11]) if row[11] is not None else None,
            terminal_event=str(row[12]) if row[12] else None,
            requested_provider_id=str(row[13]) if row[13] else None,
            requested_model_id=str(row[14]) if row[14] else None,
            actual_provider_id=str(row[15]) if row[15] else None,
            actual_model_id=str(row[16]) if row[16] else None,
            finish_reason=str(row[17]) if row[17] else None,
            created_at=str(row[18]),
            updated_at=str(row[19]) if row[19] else None,
            completed_at=str(row[20]) if row[20] else None,
        )


def _load_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


__all__ = [
    "ACTIVE_STATES",
    "BUILDING_CONTEXT",
    "CANCEL_REQUESTED",
    "COMPLETED",
    "ConversationNotFound",
    "FAILED",
    "QUEUED",
    "RETRIEVING",
    "STOPPED",
    "STREAMING",
    "TERMINAL_EVENT",
    "TERMINAL_STATES",
    "TurnCreation",
    "TurnNotFound",
    "TurnService",
    "TurnTerminalConflict",
    "TurnTransitionError",
    "TurnView",
    "is_fallback_allowed",
]
