from __future__ import annotations

from pydantic import BaseModel, Field


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class SessionCreate(BaseModel):
    title: str | None = None


class ApprovalDecision(BaseModel):
    confirm_text: str | None = None
    reason: str | None = None


class SettingsUpdate(BaseModel):
    active_provider: str | None = None
    active_model: str | None = None
    dry_run_mode: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None


class ProviderTest(BaseModel):
    provider: str
    model: str
    api_key: str | None = None  # falls back to the stored key
