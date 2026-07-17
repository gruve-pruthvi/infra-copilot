from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(default="New session")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    status: Mapped[str] = mapped_column(default="idle")  # idle|running|awaiting_approval|error
    provider: Mapped[str | None] = mapped_column(default=None)
    model: Mapped[str | None] = mapped_column(default=None)
    # pydantic-ai message history (all_messages_json) for continuing the conversation
    agent_history: Mapped[str | None] = mapped_column(Text, default=None)
    # JSON list of tool_call_ids the paused run is waiting on (null unless awaiting_approval)
    pending_call_ids: Mapped[str | None] = mapped_column(Text, default=None)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    role: Mapped[str] = mapped_column()  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[str | None] = mapped_column(Text, default=None)  # JSON list
    usage: Mapped[str | None] = mapped_column(Text, default=None)  # JSON token usage


class Action(Base):
    """Append-only audit log of every tool execution attempt."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"), default=None)
    server: Mapped[str] = mapped_column()
    tool: Mapped[str] = mapped_column(index=True)
    tier: Mapped[str] = mapped_column()  # read|mutating|destructive
    args: Mapped[str] = mapped_column(Text, default="{}")  # JSON, secrets redacted
    status: Mapped[str] = mapped_column()  # executed|denied|failed|dry_run
    result_summary: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"), default=None)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    tool_call_id: Mapped[str] = mapped_column(index=True)  # provider tool-call id of the paused run
    server: Mapped[str] = mapped_column()
    tool: Mapped[str] = mapped_column()
    tier: Mapped[str] = mapped_column()
    args: Mapped[str] = mapped_column(Text, default="{}")  # JSON, redacted for display
    args_hash: Mapped[str] = mapped_column()  # sha256 of canonical args — gateway match key
    plan_summary: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(default="pending")  # pending|approved|rejected|expired
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    confirm_text: Mapped[str | None] = mapped_column(default=None)
    reject_reason: Mapped[str | None] = mapped_column(default=None)
    consumed_at: Mapped[datetime | None] = mapped_column(default=None)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Recommendation(Base):
    """Deterministic cost/waste finding surfaced on the dashboard.

    Populated by the scheduled scanner (`services/recommendations.py`) and by
    the restart-loop detector (Task 10) via `RecommendationService.record`.
    """

    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("kind", "resource", name="uq_reco_kind_resource"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    kind: Mapped[str] = mapped_column()  # idle_container|dangling_image|orphan_volume|exited_container|restart_loop
    resource: Mapped[str] = mapped_column()  # container/image/volume name
    detail: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    status: Mapped[str] = mapped_column(default="active")  # active|dismissed|resolved
    dismissed_at: Mapped[datetime | None] = mapped_column(default=None)


Index("ix_approvals_session_status", Approval.session_id, Approval.status)
