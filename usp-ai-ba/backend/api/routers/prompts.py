"""Prompt customization for Ask Technical/Business (Phase L-D) -- read/write
access to prompt_store.py's persisted per-kind overrides, admin-gated for
writes like every other mutating settings-style endpoint in this app.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import prompt_store
from api.deps import require_admin, require_auth
from prompts.ask_prompts import BUSINESS_ASK_SYSTEM_PROMPT, TECHNICAL_ASK_SYSTEM_PROMPT

router = APIRouter(prefix="/prompts", tags=["prompts"])

_DEFAULT_PROMPTS = {"technical": TECHNICAL_ASK_SYSTEM_PROMPT, "business": BUSINESS_ASK_SYSTEM_PROMPT}


class UpdateAskPromptRequest(BaseModel):
    template: str | None = None


def _prompt_info(kind: str) -> dict:
    custom = prompt_store.get_custom_prompt(kind)
    default = _DEFAULT_PROMPTS[kind]
    return {"custom": custom, "default": default, "effective": custom or default}


@router.get("/ask")
async def get_ask_prompts(user: dict = Depends(require_auth)):
    return {"technical": _prompt_info("technical"), "business": _prompt_info("business")}


@router.put("/ask/{kind}")
async def update_ask_prompt(
    kind: Literal["technical", "business"], request: UpdateAskPromptRequest, user: dict = Depends(require_admin)
):
    try:
        prompt_store.save_custom_prompt(kind, request.template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _prompt_info(kind)
