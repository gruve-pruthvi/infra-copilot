"""ToolGateway — the server-side security boundary for every agent tool call.

Enforcement rules (independent of anything the model or the LLM framework
does):

* READ tools execute immediately, but are still audited.
* MUTATING / DESTRUCTIVE tools require an ``approvals`` DB row in status
  ``approved`` whose args hash matches the exact call, unconsumed and
  unexpired. The pydantic-ai ``ApprovalRequired`` pause is UX; the DB check
  here is the guarantee.
* Global dry-run mode forces ``dry_run=True`` on mutating tools and skips
  the approval requirement (nothing can change).
* Every attempt — executed, denied, failed, dry-run — writes an append-only
  ``actions`` audit row (args redacted) and publishes an event.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic_ai import ApprovalRequired, RunContext

from ..config import settings
from ..db.database import session_factory
from ..db.models import Action, Approval
from ..services.event_bus import bus
from ..services.settings_store import store
from .classification import Tier, classify, timeout_for

SECRET_KEY_RE = re.compile(r"(?i)(password|secret|token|api_key|apikey|access_key|private_key)")

# Receives the effective args (dry-run mode may have injected dry_run=True).
Executor = Callable[[dict[str, Any]], Awaitable[Any]]


def redact(obj: Any) -> Any:
    """Recursively mask values whose keys look like secrets."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if isinstance(v, (str, int, float)) and SECRET_KEY_RE.search(str(k)) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def args_hash(server: str, tool: str, args: dict[str, Any]) -> str:
    canon = json.dumps({"server": server, "tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def summarize_result(result: Any, limit: int = 1500) -> str:
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = str(result)
    return text[:limit]


class GatewayDenied(Exception):
    pass


class ToolGateway:
    async def execute(
        self,
        ctx: RunContext[Any],
        server: str,
        tool: str,
        args: dict[str, Any],
        executor: Executor,
    ) -> Any:
        """Run one tool call through classification + approval + audit.

        ``executor(effective_args)`` performs the actual call (MCP call_tool
        or a native function).
        """
        tier = classify(server, tool)
        session_id = getattr(ctx.deps, "session_id", None)
        approval_id: str | None = None
        effective_args = dict(args)

        if tier is not Tier.READ:
            if store.dry_run_mode:
                effective_args["dry_run"] = True
            elif not ctx.tool_call_approved:
                # Pause the run; run_manager turns the resulting
                # DeferredToolRequests into approvals rows + UI cards.
                raise ApprovalRequired
            else:
                try:
                    approval_id = await self._consume_approval(session_id, ctx.tool_call_id, server, tool, args)
                except GatewayDenied as e:
                    await self._audit(
                        session_id=session_id,
                        server=server,
                        tool=tool,
                        tier=tier,
                        args=args,
                        status="denied",
                        result=None,
                        error=str(e),
                        duration_ms=0,
                        approval_id=None,
                    )
                    return {"error": True, "denied": True, "message": str(e)}

        start = time.monotonic()
        status = "dry_run" if effective_args.get("dry_run") else "executed"
        result: Any = None
        error: str | None = None
        actual_timeout = timeout_for(server, tool)
        try:
            result = await asyncio.wait_for(executor(effective_args), timeout=actual_timeout)
            result = redact(result) if isinstance(result, (dict, list)) else result
            return result
        except ApprovalRequired:
            raise
        except asyncio.TimeoutError:
            status, error = "failed", f"tool call timed out after {actual_timeout}s"
            result = {"error": True, "message": error}
            return result
        except Exception as e:  # noqa: BLE001
            status, error = "failed", f"{type(e).__name__}: {e}"
            result = {"error": True, "message": error}
            return result
        finally:
            await self._audit(
                session_id=session_id,
                server=server,
                tool=tool,
                tier=tier,
                args=args,
                status=status,
                result=result,
                error=error,
                duration_ms=int((time.monotonic() - start) * 1000),
                approval_id=approval_id,
            )

    async def _consume_approval(
        self, session_id: str | None, tool_call_id: str | None, server: str, tool: str, args: dict[str, Any]
    ) -> str:
        """Verify + consume the DB approval record. Raises GatewayDenied if anything is off."""
        from sqlalchemy import select

        if not session_id or not tool_call_id:
            raise GatewayDenied("no session/tool-call context for approval check")
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(Approval).where(
                        Approval.session_id == session_id,
                        Approval.tool_call_id == tool_call_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise GatewayDenied("no approval record exists for this tool call")
            if row.status != "approved":
                raise GatewayDenied(f"approval status is '{row.status}', not 'approved'")
            if row.consumed_at is not None:
                raise GatewayDenied("approval was already consumed")
            now = datetime.now(timezone.utc)
            expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
            if expires < now:
                row.status = "expired"
                await db.commit()
                raise GatewayDenied("approval has expired — ask the user again")
            if row.args_hash != args_hash(server, tool, args):
                raise GatewayDenied("tool arguments do not match what the user approved")
            row.consumed_at = now
            await db.commit()
            return row.id

    async def _audit(
        self,
        *,
        session_id: str | None,
        server: str,
        tool: str,
        tier: Tier,
        args: dict[str, Any],
        status: str,
        result: Any,
        error: str | None,
        duration_ms: int,
        approval_id: str | None,
    ) -> None:
        try:
            async with session_factory() as db:
                action = Action(
                    session_id=session_id,
                    server=server,
                    tool=tool,
                    tier=tier.value,
                    args=json.dumps(redact(args), default=str),
                    status=status,
                    result_summary=summarize_result(result) if result is not None else None,
                    error=error,
                    duration_ms=duration_ms,
                    approval_id=approval_id,
                )
                db.add(action)
                await db.commit()
                bus.publish(
                    "action_logged",
                    {
                        "id": action.id,
                        "created_at": action.created_at.isoformat(),
                        "session_id": session_id,
                        "server": server,
                        "tool": tool,
                        "tier": tier.value,
                        "status": status,
                        "duration_ms": duration_ms,
                        "error": error,
                    },
                )
        except Exception:  # noqa: BLE001 — auditing must never crash a run
            import logging

            logging.getLogger(__name__).exception("failed to write audit row")


gateway = ToolGateway()
