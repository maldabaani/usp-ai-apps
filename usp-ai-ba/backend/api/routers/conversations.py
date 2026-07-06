"""CRUD over Ask Technical/Business conversation memory (Phase L-E),
scoped to the caller's own username -- api/conversation_store.py's
get_conversation() already returns None for another user's conversation id,
so a 404 (not 403) is returned either way, never confirming to a non-owner
whether an id exists at all.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import conversation_store
from api.deps import require_auth

router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    kind: Literal["technical", "business"]
    title: str | None = None


@router.get("")
async def list_conversations_endpoint(user: dict = Depends(require_auth)):
    return conversation_store.list_conversations(user["username"])


@router.post("")
async def create_conversation_endpoint(request: CreateConversationRequest, user: dict = Depends(require_auth)):
    return conversation_store.create_conversation(user["username"], request.kind, request.title)


@router.get("/{conversation_id}")
async def get_conversation_endpoint(conversation_id: str, user: dict = Depends(require_auth)):
    conversation = conversation_store.get_conversation(user["username"], conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, user: dict = Depends(require_auth)):
    removed = conversation_store.delete_conversation(user["username"], conversation_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
