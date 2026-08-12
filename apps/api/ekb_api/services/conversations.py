"""PH4 conversation graph service (spec 05-ai-chat.md sections 2, 3 and 7).

A conversation is a *branch DAG*, not a linear log.  Every conversation owns
exactly one root branch; Edit/Regenerate forks a new branch at a fork message so
an existing assistant answer is never overwritten (spec section 7).

Reading a branch therefore means walking the ancestor chain: a branch inherits
its parent's messages up to and including the fork message, then appends its own.
That keeps forks O(1) to create — no history is copied.

Every statement binds ``tenant_id``; there is no code path that reads or writes a
conversation without it, so tenant isolation is a property of construction rather
than of a runtime check that could be forgotten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, text

from ekb_api.domain import new_id, utc_now
from ekb_api.migrations.v4_007_chat_graph import (
    branch_id_for_conversation,
    message_content_hash,
)

MESSAGE_ROLES = ("system", "user", "assistant")
MESSAGE_STATUSES = ("pending", "streaming", "completed", "stopped", "failed")
PART_TYPES = ("TEXT", "IMAGE_REF", "ATTACHMENT_REF", "CITATION_MARKER")
# Spec section 2: the schema keeps room for future tool results, but this round
# of the API rejects tool / MCP / code-execution parts.
REJECTED_PART_TYPES = ("TOOL_CALL", "TOOL_RESULT", "MCP_CALL", "CODE_EXECUTION")

MAX_BRANCH_DEPTH = 64


class ConversationNotFound(Exception):
    """Conversation missing, deleted or owned by another tenant/actor."""


class BranchNotFound(Exception):
    """Branch missing or not part of the requested conversation."""


class MessageNotFound(Exception):
    """Message missing or outside the caller's tenant."""


class ChatGraphError(Exception):
    """Invalid chat-graph operation (bad role, bad part type, cycle, ...)."""


@dataclass(frozen=True)
class BranchView:
    id: str
    conversation_id: str
    parent_branch_id: Optional[str]
    fork_message_id: Optional[str]
    label: Optional[str]
    created_by: str
    created_at: str


@dataclass(frozen=True)
class MessageView:
    id: str
    conversation_id: str
    branch_id: Optional[str]
    parent_message_id: Optional[str]
    role: str
    status: str
    content: str
    content_hash: Optional[str]
    turn_id: Optional[str]
    created_at: str


def _json_expr(dialect: str, param: str) -> str:
    return f"CAST(:{param} AS JSONB)" if dialect == "postgresql" else f":{param}"


class ConversationGraphService:
    """Branch-aware conversation reads/writes.

    Methods accept an optional ``connection`` so a caller that already opened a
    transaction (turn creation is a single transaction per spec section 4) can
    compose without nesting ``engine.begin()``, which deadlocks on SQLite.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ---- conversation lifecycle (spec section 3) ----

    def create_conversation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        title: str = "",
        connection: Optional[Connection] = None,
    ) -> tuple[str, str]:
        """Create a conversation together with its root branch. Returns ids."""

        def _run(conn: Connection) -> tuple[str, str]:
            now = utc_now()
            conversation_id = new_id()
            conn.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, tenant_id, user_id, title, title_locked, created_at, updated_at) "
                    "VALUES (:id,:tenant_id,:user_id,:title,0,:created_at,:updated_at)"
                ),
                {
                    "id": conversation_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "title": title,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            branch = self._insert_branch(
                conn,
                branch_id=branch_id_for_conversation(conversation_id),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                parent_branch_id=None,
                fork_message_id=None,
                label="root",
                created_by=user_id,
                now=now,
            )
            conn.execute(
                text(
                    "UPDATE conversations SET active_branch_id=:branch_id "
                    "WHERE id=:id AND tenant_id=:tenant_id"
                ),
                {"branch_id": branch.id, "id": conversation_id, "tenant_id": tenant_id},
            )
            return conversation_id, branch.id

        return self._with_connection(connection, _run)

    def load_conversation(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_id: Optional[str] = None,
        connection: Optional[Connection] = None,
    ) -> dict[str, object]:
        def _run(conn: Connection) -> dict[str, object]:
            row = conn.execute(
                text(
                    "SELECT id, tenant_id, user_id, title, title_locked, active_branch_id, "
                    "deleted_at FROM conversations WHERE id=:id AND tenant_id=:tenant_id"
                ),
                {"id": conversation_id, "tenant_id": tenant_id},
            ).first()
            if row is None or row[6] is not None:
                raise ConversationNotFound(conversation_id)
            if actor_id is not None and str(row[2]) != actor_id:
                raise ConversationNotFound(conversation_id)
            return {
                "id": str(row[0]),
                "tenant_id": str(row[1]),
                "user_id": str(row[2]),
                "title": str(row[3] or ""),
                "title_locked": bool(row[4]),
                "active_branch_id": str(row[5]) if row[5] else None,
            }

        return self._with_connection(connection, _run)

    def rename(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        title: str,
        lock: bool = True,
        connection: Optional[Connection] = None,
    ) -> None:
        """User rename locks the title so the async title job never overwrites it."""

        def _run(conn: Connection) -> None:
            result = conn.execute(
                text(
                    "UPDATE conversations SET title=:title, title_locked=:locked, "
                    "updated_at=:now WHERE id=:id AND tenant_id=:tenant_id "
                    "AND deleted_at IS NULL"
                ),
                {
                    "title": title,
                    "locked": 1 if lock else 0,
                    "now": utc_now(),
                    "id": conversation_id,
                    "tenant_id": tenant_id,
                },
            )
            if not result.rowcount:
                raise ConversationNotFound(conversation_id)

        self._with_connection(connection, _run)

    # ---- branches (spec section 7 Regenerate) ----

    def ensure_root_branch(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        user_id: str,
        connection: Optional[Connection] = None,
    ) -> BranchView:
        def _run(conn: Connection) -> BranchView:
            existing = conn.execute(
                text(
                    "SELECT id FROM conversation_branches WHERE conversation_id=:conversation_id "
                    "AND tenant_id=:tenant_id AND parent_branch_id IS NULL"
                ),
                {"conversation_id": conversation_id, "tenant_id": tenant_id},
            ).first()
            if existing is not None:
                return self._branch(conn, tenant_id=tenant_id, branch_id=str(existing[0]))
            return self._insert_branch(
                conn,
                branch_id=branch_id_for_conversation(conversation_id),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                parent_branch_id=None,
                fork_message_id=None,
                label="root",
                created_by=user_id,
                now=utc_now(),
            )

        return self._with_connection(connection, _run)

    def fork_branch(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        fork_message_id: str,
        created_by: str,
        label: Optional[str] = None,
        connection: Optional[Connection] = None,
    ) -> BranchView:
        """Fork at ``fork_message_id`` (the user message being regenerated).

        The old answer stays reachable on the parent branch — Regenerate never
        mutates history (spec section 7).
        """

        def _run(conn: Connection) -> BranchView:
            message = self._message(conn, tenant_id=tenant_id, message_id=fork_message_id)
            if message.conversation_id != conversation_id:
                raise ChatGraphError("fork message belongs to another conversation")
            if message.branch_id is None:
                raise ChatGraphError("fork message has no branch; run the v4_007 backfill")
            return self._insert_branch(
                conn,
                branch_id=new_id(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                parent_branch_id=message.branch_id,
                fork_message_id=fork_message_id,
                label=label,
                created_by=created_by,
                now=utc_now(),
            )

        return self._with_connection(connection, _run)

    def set_active_branch(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        branch_id: str,
        connection: Optional[Connection] = None,
    ) -> None:
        def _run(conn: Connection) -> None:
            branch = self._branch(conn, tenant_id=tenant_id, branch_id=branch_id)
            if branch.conversation_id != conversation_id:
                raise BranchNotFound(branch_id)
            conn.execute(
                text(
                    "UPDATE conversations SET active_branch_id=:branch_id, updated_at=:now "
                    "WHERE id=:id AND tenant_id=:tenant_id"
                ),
                {
                    "branch_id": branch_id,
                    "now": utc_now(),
                    "id": conversation_id,
                    "tenant_id": tenant_id,
                },
            )

        self._with_connection(connection, _run)

    def list_branches(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        connection: Optional[Connection] = None,
    ) -> list[BranchView]:
        def _run(conn: Connection) -> list[BranchView]:
            rows = conn.execute(
                text(
                    "SELECT id, conversation_id, parent_branch_id, fork_message_id, label,"
                    " created_by, created_at FROM conversation_branches "
                    "WHERE tenant_id=:tenant_id AND conversation_id=:conversation_id "
                    "ORDER BY created_at, id"
                ),
                {"tenant_id": tenant_id, "conversation_id": conversation_id},
            ).fetchall()
            return [BranchView(*(None if v is None else str(v) for v in row)) for row in rows]

        return self._with_connection(connection, _run)

    # ---- messages ----

    def append_message(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        branch_id: str,
        role: str,
        content: str,
        status: str = "completed",
        turn_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        connection: Optional[Connection] = None,
    ) -> MessageView:
        if role not in MESSAGE_ROLES:
            raise ChatGraphError(f"unsupported message role: {role}")
        if status not in MESSAGE_STATUSES:
            raise ChatGraphError(f"unsupported message status: {status}")

        def _run(conn: Connection) -> MessageView:
            branch = self._branch(conn, tenant_id=tenant_id, branch_id=branch_id)
            if branch.conversation_id != conversation_id:
                raise BranchNotFound(branch_id)
            if parent_message_id is None:
                previous = self.branch_messages(
                    tenant_id=tenant_id, branch_id=branch_id, connection=conn
                )
                parent = previous[-1].id if previous else None
            else:
                parent = self._message(
                    conn, tenant_id=tenant_id, message_id=parent_message_id
                ).id
            now = utc_now()
            message_id = new_id()
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, tenant_id, conversation_id, role, content, turn_id,"
                    " visibility_state, created_at, branch_id, parent_message_id,"
                    " status, content_hash) "
                    "VALUES (:id,:tenant_id,:conversation_id,:role,:content,:turn_id,"
                    "'visible',:created_at,:branch_id,:parent_message_id,:status,:content_hash)"
                ),
                {
                    "id": message_id,
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "turn_id": turn_id,
                    "created_at": now,
                    "branch_id": branch_id,
                    "parent_message_id": parent,
                    "status": status,
                    "content_hash": message_content_hash(role, content),
                },
            )
            conn.execute(
                text("UPDATE conversations SET updated_at=:now WHERE id=:id AND tenant_id=:tid"),
                {"now": now, "id": conversation_id, "tid": tenant_id},
            )
            return MessageView(
                id=message_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                parent_message_id=parent,
                role=role,
                status=status,
                content=content,
                content_hash=message_content_hash(role, content),
                turn_id=turn_id,
                created_at=now,
            )

        return self._with_connection(connection, _run)

    def update_message(
        self,
        *,
        tenant_id: str,
        message_id: str,
        content: Optional[str] = None,
        status: Optional[str] = None,
        connection: Optional[Connection] = None,
    ) -> MessageView:
        if status is not None and status not in MESSAGE_STATUSES:
            raise ChatGraphError(f"unsupported message status: {status}")

        def _run(conn: Connection) -> MessageView:
            current = self._message(conn, tenant_id=tenant_id, message_id=message_id)
            new_content = current.content if content is None else content
            new_status = current.status if status is None else status
            conn.execute(
                text(
                    "UPDATE messages SET content=:content, status=:status, content_hash=:hash "
                    "WHERE id=:id AND tenant_id=:tenant_id"
                ),
                {
                    "content": new_content,
                    "status": new_status,
                    "hash": message_content_hash(current.role, new_content),
                    "id": message_id,
                    "tenant_id": tenant_id,
                },
            )
            return self._message(conn, tenant_id=tenant_id, message_id=message_id)

        return self._with_connection(connection, _run)

    def branch_messages(
        self,
        *,
        tenant_id: str,
        branch_id: str,
        connection: Optional[Connection] = None,
    ) -> list[MessageView]:
        """Materialize a branch: inherited ancestor prefix + own messages."""

        def _run(conn: Connection) -> list[MessageView]:
            lineage: list[tuple[str, Optional[str]]] = []
            cursor: Optional[str] = branch_id
            seen: set[str] = set()
            while cursor is not None:
                if cursor in seen or len(lineage) > MAX_BRANCH_DEPTH:
                    raise ChatGraphError(f"branch lineage cycle or too deep at {cursor}")
                seen.add(cursor)
                branch = self._branch(conn, tenant_id=tenant_id, branch_id=cursor)
                lineage.append((branch.id, branch.fork_message_id))
                cursor = branch.parent_branch_id

            messages: list[MessageView] = []
            # Walk root → leaf; each ancestor contributes only up to its child's
            # fork message, which is what makes a fork O(1) instead of a copy.
            ordered = list(reversed(lineage))
            for index, (current_branch, _fork) in enumerate(ordered):
                child_fork = ordered[index + 1][1] if index + 1 < len(ordered) else None
                rows = conn.execute(
                    text(
                        "SELECT id, conversation_id, branch_id, parent_message_id, role,"
                        " status, content, content_hash, turn_id, created_at FROM messages "
                        "WHERE tenant_id=:tenant_id AND branch_id=:branch_id "
                        "ORDER BY created_at, id"
                    ),
                    {"tenant_id": tenant_id, "branch_id": current_branch},
                ).fetchall()
                for view in _order_by_parent_chain(rows):
                    messages.append(view)
                    if child_fork is not None and view.id == child_fork:
                        break
            return messages

        return self._with_connection(connection, _run)

    # ---- parts & citations ----

    def add_parts(
        self,
        *,
        tenant_id: str,
        message_id: str,
        parts: list[dict[str, object]],
        connection: Optional[Connection] = None,
    ) -> int:
        def _run(conn: Connection) -> int:
            self._message(conn, tenant_id=tenant_id, message_id=message_id)
            expression = _json_expr(conn.dialect.name, "json_content")
            written = 0
            for ordinal, part in enumerate(parts):
                part_type = str(part.get("part_type") or "TEXT").upper()
                if part_type in REJECTED_PART_TYPES:
                    raise ChatGraphError(f"part type not accepted in this round: {part_type}")
                if part_type not in PART_TYPES:
                    raise ChatGraphError(f"unsupported part type: {part_type}")
                conn.execute(
                    text(
                        "INSERT INTO message_parts "
                        "(id, tenant_id, message_id, part_type, ordinal, text_content,"
                        f" json_content, resource_id) VALUES (:id,:tenant_id,:message_id,"
                        f":part_type,:ordinal,:text_content,{expression},:resource_id)"
                    ),
                    {
                        "id": new_id(),
                        "tenant_id": tenant_id,
                        "message_id": message_id,
                        "part_type": part_type,
                        "ordinal": int(part.get("ordinal", ordinal)),
                        "text_content": part.get("text_content"),
                        "json_content": json.dumps(part.get("json_content") or {}, sort_keys=True),
                        "resource_id": part.get("resource_id"),
                    },
                )
                written += 1
            return written

        return self._with_connection(connection, _run)

    def add_citations(
        self,
        *,
        tenant_id: str,
        message_id: str,
        citations: list[dict[str, object]],
        connection: Optional[Connection] = None,
    ) -> int:
        """Persist citations. Exactly one of document_version_id / attachment_id."""

        def _run(conn: Connection) -> int:
            self._message(conn, tenant_id=tenant_id, message_id=message_id)
            locator_expr = _json_expr(conn.dialect.name, "locator")
            display_expr = _json_expr(conn.dialect.name, "display_snapshot")
            written = 0
            for rank, citation in enumerate(citations):
                version_id = citation.get("document_version_id")
                attachment_id = citation.get("attachment_id")
                if bool(version_id) == bool(attachment_id):
                    raise ChatGraphError(
                        "citation must reference exactly one of "
                        "document_version_id / attachment_id"
                    )
                conn.execute(
                    text(
                        "INSERT INTO message_citations "
                        "(id, tenant_id, message_id, document_id, document_version_id,"
                        " chunk_id, attachment_id, locator, display_snapshot, rank) "
                        "VALUES (:id,:tenant_id,:message_id,:document_id,:document_version_id,"
                        f":chunk_id,:attachment_id,{locator_expr},{display_expr},:rank)"
                    ),
                    {
                        "id": new_id(),
                        "tenant_id": tenant_id,
                        "message_id": message_id,
                        "document_id": citation.get("document_id"),
                        "document_version_id": version_id,
                        "chunk_id": citation.get("chunk_id"),
                        "attachment_id": attachment_id,
                        "locator": json.dumps(citation.get("locator") or {}, sort_keys=True),
                        "display_snapshot": json.dumps(
                            citation.get("display_snapshot") or {}, sort_keys=True
                        ),
                        "rank": int(citation.get("rank", rank)),
                    },
                )
                written += 1
            return written

        return self._with_connection(connection, _run)

    # ---- internals ----

    def _with_connection(self, connection: Optional[Connection], run):
        if connection is not None:
            return run(connection)
        with self.engine.begin() as owned:
            return run(owned)

    def _insert_branch(
        self,
        connection: Connection,
        *,
        branch_id: str,
        tenant_id: str,
        conversation_id: str,
        parent_branch_id: Optional[str],
        fork_message_id: Optional[str],
        label: Optional[str],
        created_by: str,
        now: str,
    ) -> BranchView:
        connection.execute(
            text(
                "INSERT INTO conversation_branches "
                "(id, tenant_id, conversation_id, parent_branch_id, fork_message_id,"
                " label, created_by, created_at) "
                "VALUES (:id,:tenant_id,:conversation_id,:parent_branch_id,:fork_message_id,"
                ":label,:created_by,:created_at)"
            ),
            {
                "id": branch_id,
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "parent_branch_id": parent_branch_id,
                "fork_message_id": fork_message_id,
                "label": label,
                "created_by": created_by,
                "created_at": now,
            },
        )
        return BranchView(
            id=branch_id,
            conversation_id=conversation_id,
            parent_branch_id=parent_branch_id,
            fork_message_id=fork_message_id,
            label=label,
            created_by=created_by,
            created_at=now,
        )

    def _branch(self, connection: Connection, *, tenant_id: str, branch_id: str) -> BranchView:
        row = connection.execute(
            text(
                "SELECT id, conversation_id, parent_branch_id, fork_message_id, label,"
                " created_by, created_at FROM conversation_branches "
                "WHERE id=:id AND tenant_id=:tenant_id"
            ),
            {"id": branch_id, "tenant_id": tenant_id},
        ).first()
        if row is None:
            raise BranchNotFound(branch_id)
        return BranchView(
            id=str(row[0]),
            conversation_id=str(row[1]),
            parent_branch_id=str(row[2]) if row[2] else None,
            fork_message_id=str(row[3]) if row[3] else None,
            label=str(row[4]) if row[4] else None,
            created_by=str(row[5]),
            created_at=str(row[6]),
        )

    def _message(self, connection: Connection, *, tenant_id: str, message_id: str) -> MessageView:
        row = connection.execute(
            text(
                "SELECT id, conversation_id, branch_id, parent_message_id, role, status,"
                " content, content_hash, turn_id, created_at FROM messages "
                "WHERE id=:id AND tenant_id=:tenant_id"
            ),
            {"id": message_id, "tenant_id": tenant_id},
        ).first()
        if row is None:
            raise MessageNotFound(message_id)
        return _message_view(row)


def _order_by_parent_chain(rows) -> list[MessageView]:
    """Order one branch's messages by the ``parent_message_id`` chain.

    ``created_at`` only has second resolution, so a user message and its answer
    written in the same second would otherwise be ordered by random UUID.  The
    parent chain is the authoritative order and is clock-independent.

    Rows whose chain is broken (legacy imports, a repaired conversation) are
    appended afterwards in ``(created_at, id)`` order — the caller's SQL already
    supplies that order — so the result stays deterministic instead of dropping
    messages.
    """

    views = [_message_view(row) for row in rows]
    if len(views) < 2:
        return views

    by_id = {view.id: view for view in views}
    children: dict[Optional[str], list[MessageView]] = {}
    for view in views:
        parent = view.parent_message_id
        # A fork's first message points at a message on the parent branch; treat
        # it as a head for this branch's ordering.
        key = parent if parent in by_id else None
        children.setdefault(key, []).append(view)

    ordered: list[MessageView] = []
    emitted: set[str] = set()
    queue = list(children.get(None, ()))
    while queue:
        view = queue.pop(0)
        if view.id in emitted:
            continue
        emitted.add(view.id)
        ordered.append(view)
        queue = list(children.get(view.id, ())) + queue

    if len(ordered) != len(views):
        ordered.extend(view for view in views if view.id not in emitted)
    return ordered


def _message_view(row) -> MessageView:
    return MessageView(
        id=str(row[0]),
        conversation_id=str(row[1]),
        branch_id=str(row[2]) if row[2] else None,
        parent_message_id=str(row[3]) if row[3] else None,
        role=str(row[4]),
        status=str(row[5] or "completed"),
        content=str(row[6] or ""),
        content_hash=str(row[7]) if row[7] else None,
        turn_id=str(row[8]) if row[8] else None,
        created_at=str(row[9]),
    )
