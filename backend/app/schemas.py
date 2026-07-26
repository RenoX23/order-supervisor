"""Pydantic request models for the API.

Note the supervisor config's ``model_config`` column: that name is reserved by
Pydantic v2, so the field is called ``configuration`` here and exposed under the
alias ``model_config`` (the JSON/DB name). ``populate_by_name`` lets callers send
either key.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.temporal.shared import ACTION_TYPES


class SupervisorCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    base_instruction: str
    tools_enabled: list[str] = Field(default_factory=lambda: list(ACTION_TYPES))
    wake_policy: dict = Field(default_factory=dict)
    configuration: dict = Field(default_factory=dict, alias="model_config")


class RunCreate(BaseModel):
    supervisor_id: str
    order_id: str
    first_event: str = "order_created"
    order_context: dict = Field(default_factory=dict)


class EventIn(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


class InstructionIn(BaseModel):
    text: str


class TerminateIn(BaseModel):
    reason: str = ""
