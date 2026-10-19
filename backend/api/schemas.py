"""The API's request/response models. frontend/lib/types.ts mirrors them.

A run is streamed as server-sent events (api/events.py builds them):

    token      {"id", "text"}   a delta of the agent's reply; a new id starts a new message
    status     {"label"}        the agent called a tool, e.g. "Researching NVDA"
    interrupt  {...}            buy_stock's confirmation payload, as the tool built it
    done       {}               the run finished and its final checkpoint is saved
    error      {"message"}      the run failed
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class NewThread(BaseModel):
    thread_id: UUID


class MessageView(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ThreadView(BaseModel):
    thread_id: UUID
    # Only what the chat shows: the user's messages and the agent's replies with
    # text. Tool calls and tool results stay in the checkpoint.
    messages: list[MessageView]
    interrupt: dict[str, Any] | None
    active_run: bool


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
