from __future__ import annotations

from typing_extensions import Annotated
from typing import Optional

from fastapi import APIRouter, Depends
from starlette import status

from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.schemas import ConversationUpdate
from ekb_api.store import SqlStore

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_conversations(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[dict[str, Optional[str]]]:
    return [
        {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "archived_at": conversation.archived_at,
        }
        for conversation in store.list_conversations(auth)
    ]


@router.get("/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[dict[str, object]]:
    conversation = store.get_conversation(auth, conversation_id)
    if not conversation:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at,
            # Keep the existing response fields and expose the PH6 citation
            # metadata under the redacted metadata envelope expected by the
            # conversation client. No provider secret or raw object key is
            # included in this projection.
            "metadata_redacted": (
                {"citations": message.citations}
                if message.citations
                else {}
            ),
        }
        for message in store.list_messages(auth, conversation_id)
    ]


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, Optional[str]]:
    conversation = store.update_conversation(
        auth,
        conversation_id,
        title=payload.title,
        archived=payload.archived,
    )
    if not conversation:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    return {
        "id": conversation.id,
        "title": conversation.title,
        "updated_at": conversation.updated_at,
        "archived_at": conversation.archived_at,
    }


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> None:
    if not store.delete_conversation(auth, conversation_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
