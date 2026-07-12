"""Per-session agent run lifecycle: start → stream → pause on approval → resume.

Each session has a broadcaster; both the POST /messages SSE response and the
GET /stream SSE endpoint subscribe to it, so events from a resumed run reach
the UI even though the original request stream has ended.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterable

from pydantic_ai import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from sqlalchemy import select

from ..config import settings
from ..db.database import session_factory
from ..db.models import Approval, ChatSession, Message
from ..llm.models import ProviderNotConfigured
from ..services.event_bus import bus
from ..tools.classification import SAFETY, Tier
from ..tools.gateway import args_hash, redact
from .copilot import build_agent
from .deps import RunDeps

log = logging.getLogger(__name__)

TERMINAL_EVENTS = {"done", "turn_paused", "error"}


def _tier_for_tool(tool: str) -> str:
    for key, tier in SAFETY.items():
        if key.split(".", 1)[1] == tool:
            return tier.value
    return Tier.DESTRUCTIVE.value


def _server_for_tool(tool: str) -> str:
    for key in SAFETY:
        server, name = key.split(".", 1)
        if name == tool:
            return server
    return "unknown"


class Broadcaster:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self.last_status: str = "idle"

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def emit(self, event: dict[str, Any]) -> None:
        if event.get("type") == "status":
            self.last_status = event.get("status", self.last_status)
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


class RunManager:
    def __init__(self) -> None:
        self._broadcasters: dict[str, Broadcaster] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def broadcaster(self, session_id: str) -> Broadcaster:
        if session_id not in self._broadcasters:
            self._broadcasters[session_id] = Broadcaster()
        return self._broadcasters[session_id]

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    # -- public entry points -------------------------------------------------

    async def start_turn(self, session_id: str, user_text: str) -> None:
        """Persist the user message and launch the agent turn."""
        if self.is_running(session_id):
            raise RuntimeError("a turn is already running for this session")
        async with session_factory() as db:
            sess = await db.get(ChatSession, session_id)
            if sess is None:
                raise KeyError("session not found")
            if sess.status == "awaiting_approval":
                raise RuntimeError("session is awaiting approval — approve or reject the pending action first")
            db.add(Message(session_id=session_id, role="user", content=user_text))
            if sess.title == "New session":
                sess.title = user_text[:60]
            sess.status = "running"
            await db.commit()
        self._launch(session_id, user_prompt=user_text)

    async def cancel(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        confirm_text: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Mark one approval resolved; resume the paused run when all are resolved."""
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            row = await db.get(Approval, approval_id)
            if row is None:
                raise KeyError("approval not found")
            if row.status != "pending":
                raise ValueError(f"approval already {row.status}")
            expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
            if approved and expires < now:
                row.status = "expired"
                await db.commit()
                raise ValueError("approval has expired — reject it and ask again")
            if approved and row.tier == "destructive" and not (confirm_text or "").strip():
                raise ValueError("destructive actions require typing the resource name to confirm")
            row.status = "approved" if approved else "rejected"
            row.resolved_at = now
            row.confirm_text = confirm_text
            row.reject_reason = reason
            await db.commit()

            sess = await db.get(ChatSession, row.session_id)
            pending_ids: list[str] = json.loads(sess.pending_call_ids or "[]")
            rows = (
                (await db.execute(select(Approval).where(Approval.session_id == row.session_id, Approval.tool_call_id.in_(pending_ids))))
                .scalars()
                .all()
            )
        bus.publish("approval_resolved", {"id": approval_id, "status": row.status, "session_id": row.session_id})

        unresolved = [r for r in rows if r.status == "pending"]
        if unresolved:
            return {"resumed": False, "waiting_on": len(unresolved)}

        results = DeferredToolResults()
        for r in rows:
            if r.status == "approved":
                results.approvals[r.tool_call_id] = ToolApproved()
            else:
                msg = r.reject_reason or ("approval expired" if r.status == "expired" else "The user rejected this action.")
                results.approvals[r.tool_call_id] = ToolDenied(message=f"Denied: {msg}")

        async with session_factory() as db:
            sess = await db.get(ChatSession, row.session_id)
            sess.status = "running"
            sess.pending_call_ids = None
            await db.commit()
        self._launch(row.session_id, deferred_results=results)
        return {"resumed": True}

    # -- internals -----------------------------------------------------------

    def _launch(self, session_id: str, user_prompt: str | None = None, deferred_results: DeferredToolResults | None = None) -> None:
        bcast = self.broadcaster(session_id)
        bcast.emit({"type": "status", "status": "running"})
        bus.publish("session_status", {"session_id": session_id, "status": "running"})
        self._tasks[session_id] = asyncio.create_task(
            self._run(session_id, user_prompt, deferred_results), name=f"agent-run-{session_id}"
        )

    async def _run(
        self,
        session_id: str,
        user_prompt: str | None,
        deferred_results: DeferredToolResults | None,
    ) -> None:
        bcast = self.broadcaster(session_id)
        turn: dict[str, Any] = {"text": "", "tool_calls": []}

        async def event_stream_handler(_ctx: Any, events: AsyncIterable[Any]) -> None:
            async for ev in events:
                self._convert_event(ev, turn, bcast)

        try:
            agent, model_settings = build_agent()
            history = await self._load_history(session_id)
            usage_limits = None
            try:
                from pydantic_ai.usage import UsageLimits

                usage_limits = UsageLimits(request_limit=settings.max_tool_calls_per_turn)
            except Exception:  # noqa: BLE001
                pass

            result = await agent.run(
                user_prompt,
                message_history=history,
                deferred_tool_results=deferred_results,
                deps=RunDeps(session_id=session_id),
                event_stream_handler=event_stream_handler,
                model_settings=model_settings,
                usage_limits=usage_limits,
            )

            await self._save_history(session_id, result.all_messages_json())
            usage = self._usage_dict(result)

            if isinstance(result.output, DeferredToolRequests):
                await self._pause_for_approvals(session_id, result.output, turn, bcast)
            else:
                await self._finish_turn(session_id, str(result.output), turn, usage, bcast)
        except asyncio.CancelledError:
            await self._set_status(session_id, "idle")
            bcast.emit({"type": "error", "message": "cancelled"})
        except ProviderNotConfigured as e:
            await self._set_status(session_id, "idle")
            bcast.emit({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("agent run failed for session %s", session_id)
            await self._set_status(session_id, "error")
            bcast.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})

    def _convert_event(self, ev: Any, turn: dict[str, Any], bcast: Broadcaster) -> None:
        if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
            if ev.part.content:
                turn["text"] += ev.part.content
                bcast.emit({"type": "text_delta", "text": ev.part.content})
        elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
            if ev.delta.content_delta:
                turn["text"] += ev.delta.content_delta
                bcast.emit({"type": "text_delta", "text": ev.delta.content_delta})
        elif isinstance(ev, FunctionToolCallEvent):
            part = ev.part
            args = _safe_args(part)
            call = {
                "tool": part.tool_name,
                "server": _server_for_tool(part.tool_name),
                "tier": _tier_for_tool(part.tool_name),
                "args": redact(args),
                "tool_call_id": part.tool_call_id,
            }
            turn["tool_calls"].append(call)
            bcast.emit({"type": "tool_call_started", **call})
        elif isinstance(ev, FunctionToolResultEvent):
            res = ev.part
            content = getattr(res, "content", None)
            summary = _summarize(content)
            for call in turn["tool_calls"]:
                if call.get("tool_call_id") == getattr(res, "tool_call_id", None):
                    call["result_summary"] = summary
            bcast.emit(
                {
                    "type": "tool_result",
                    "tool": getattr(res, "tool_name", None),
                    "tool_call_id": getattr(res, "tool_call_id", None),
                    "result_summary": summary,
                }
            )

    async def _pause_for_approvals(
        self, session_id: str, requests: DeferredToolRequests, turn: dict[str, Any], bcast: Broadcaster
    ) -> None:
        expires = datetime.now(timezone.utc) + timedelta(seconds=settings.approval_ttl_seconds)
        approvals_payload = []
        call_ids = []
        async with session_factory() as db:
            for part in requests.approvals:
                args = _safe_args(part)
                server = _server_for_tool(part.tool_name)
                row = Approval(
                    session_id=session_id,
                    tool_call_id=part.tool_call_id,
                    server=server,
                    tool=part.tool_name,
                    tier=_tier_for_tool(part.tool_name),
                    args=json.dumps(redact(args), default=str),
                    args_hash=args_hash(server, part.tool_name, args),
                    plan_summary=turn["text"][-2000:] or None,
                    expires_at=expires,
                )
                db.add(row)
                await db.flush()
                call_ids.append(part.tool_call_id)
                approvals_payload.append(
                    {
                        "id": row.id,
                        "session_id": session_id,
                        "tool": row.tool,
                        "server": server,
                        "tier": row.tier,
                        "args": json.loads(row.args),
                        "plan_summary": row.plan_summary,
                        "expires_at": expires.isoformat(),
                    }
                )
            # save the plan text as an assistant message
            if turn["text"] or turn["tool_calls"]:
                db.add(
                    Message(
                        session_id=session_id,
                        role="assistant",
                        content=turn["text"],
                        tool_calls=json.dumps(turn["tool_calls"], default=str),
                    )
                )
            sess = await db.get(ChatSession, session_id)
            sess.status = "awaiting_approval"
            sess.pending_call_ids = json.dumps(call_ids)
            await db.commit()

        for payload in approvals_payload:
            bcast.emit({"type": "approval_required", "approval": payload})
            bus.publish("approval_pending", payload)
        bus.publish("session_status", {"session_id": session_id, "status": "awaiting_approval"})
        bcast.emit({"type": "status", "status": "awaiting_approval"})
        bcast.emit({"type": "turn_paused"})

    async def _finish_turn(
        self, session_id: str, output_text: str, turn: dict[str, Any], usage: dict[str, Any], bcast: Broadcaster
    ) -> None:
        # streamed text should equal output_text; prefer the authoritative output
        content = output_text or turn["text"]
        async with session_factory() as db:
            db.add(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content=content,
                    tool_calls=json.dumps(turn["tool_calls"], default=str) if turn["tool_calls"] else None,
                    usage=json.dumps(usage) if usage else None,
                )
            )
            sess = await db.get(ChatSession, session_id)
            sess.status = "idle"
            await db.commit()
        bus.publish("session_status", {"session_id": session_id, "status": "idle"})
        bcast.emit({"type": "status", "status": "idle"})
        bcast.emit({"type": "done", "usage": usage})

    async def _set_status(self, session_id: str, status: str) -> None:
        async with session_factory() as db:
            sess = await db.get(ChatSession, session_id)
            if sess:
                sess.status = status
                await db.commit()
        bus.publish("session_status", {"session_id": session_id, "status": status})
        self.broadcaster(session_id).emit({"type": "status", "status": status})

    async def _load_history(self, session_id: str):
        async with session_factory() as db:
            sess = await db.get(ChatSession, session_id)
        if sess and sess.agent_history:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            return ModelMessagesTypeAdapter.validate_json(sess.agent_history)
        return None

    async def _save_history(self, session_id: str, history_json: bytes) -> None:
        async with session_factory() as db:
            sess = await db.get(ChatSession, session_id)
            if sess:
                sess.agent_history = history_json.decode()
                await db.commit()

    @staticmethod
    def _usage_dict(result: Any) -> dict[str, Any]:
        try:
            u = result.usage()
            return {
                "input_tokens": getattr(u, "input_tokens", None) or getattr(u, "request_tokens", None),
                "output_tokens": getattr(u, "output_tokens", None) or getattr(u, "response_tokens", None),
                "requests": getattr(u, "requests", None),
            }
        except Exception:  # noqa: BLE001
            return {}


def _safe_args(part: Any) -> dict[str, Any]:
    try:
        return part.args_as_dict() or {}
    except Exception:  # noqa: BLE001
        args = getattr(part, "args", None)
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                return json.loads(args)
            except (ValueError, TypeError):
                return {"_raw": args}
        return {}


def _summarize(content: Any, limit: int = 1500) -> str:
    try:
        text = content if isinstance(content, str) else json.dumps(content, default=str)
    except (TypeError, ValueError):
        text = str(content)
    return text[:limit]


async def recover_on_startup() -> None:
    """Called once at boot: orphaned running sessions → error; stale approvals → expired."""
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        sessions = (await db.execute(select(ChatSession).where(ChatSession.status == "running"))).scalars().all()
        for s in sessions:
            s.status = "error"
        stale = (
            (await db.execute(select(Approval).where(Approval.status == "pending")))
            .scalars()
            .all()
        )
        for a in stale:
            expires = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=timezone.utc)
            if expires < now:
                a.status = "expired"
        await db.commit()


run_manager = RunManager()
