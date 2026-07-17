"""Scheduled deterministic cost/waste scan -> recommendations table -> dashboard.

Deliberately LLM-free (runs without an API key); the agent is brought in via
the dashboard's "Diagnose" button, not by the scanner. Talks to docker-py
directly (like `services/monitor.py`) plus reads the monitor's live snapshot
for the idle-container heuristic — it does not go through MCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import docker
from sqlalchemy import select

from ..config import settings
from ..db.database import session_factory
from ..db.models import Recommendation
from .event_bus import bus
from .monitor import monitor

log = logging.getLogger(__name__)

# Findings produced by the periodic scan (as opposed to `record`, which is
# used by the restart-loop detector for one-off findings) -- a row of one of
# these kinds that's no longer seen in a fresh scan gets marked resolved.
SCAN_KINDS = {"dangling_image", "orphan_volume", "idle_container", "exited_container"}

IDLE_CPU_THRESHOLD = 1.0
EXITED_HOURS_THRESHOLD = 24.0


class RecommendationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="recommendation-scanner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(60)  # let monitor warm up first
        while True:
            try:
                await self.scan_now()
            except Exception:  # noqa: BLE001
                log.exception("cost scan failed")
            await asyncio.sleep(settings.cost_scan_interval_hours * 3600)

    async def scan_now(self) -> None:
        findings = await asyncio.to_thread(self._scan_sync)
        await self._store(findings)

    def _scan_sync(self) -> list[dict]:
        findings: list[dict] = []
        cli = docker.from_env()

        for img in cli.images.list(filters={"dangling": True}):
            findings.append(
                {
                    "kind": "dangling_image",
                    "resource": img.short_id,
                    "detail": {"size_mb": round(img.attrs.get("Size", 0) / 1e6, 1)},
                }
            )

        containers = cli.containers.list(all=True)
        used_volumes = {
            m.get("Name")
            for c in containers
            for m in c.attrs.get("Mounts", [])
            if m.get("Type") == "volume"
        }
        for v in cli.volumes.list():
            if v.name not in used_volumes:
                findings.append({"kind": "orphan_volume", "resource": v.name, "detail": {}})

        now = datetime.now(timezone.utc)
        for c in containers:
            if c.status != "exited":
                continue
            finished = c.attrs.get("State", {}).get("FinishedAt")
            if not finished:
                continue
            try:
                finished_dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            except ValueError:
                continue
            age_hours = (now - finished_dt).total_seconds() / 3600
            if age_hours >= EXITED_HOURS_THRESHOLD:
                findings.append(
                    {
                        "kind": "exited_container",
                        "resource": c.name,
                        "detail": {"exited_hours_ago": round(age_hours, 1)},
                    }
                )

        # Idle heuristic from the monitor's live snapshot (already has cpu_percent).
        for c in monitor.containers:
            if (
                c.get("status") == "running"
                and c.get("cpu_percent") is not None
                and c["cpu_percent"] < IDLE_CPU_THRESHOLD
            ):
                findings.append(
                    {
                        "kind": "idle_container",
                        "resource": c["name"],
                        "detail": {
                            "cpu_percent": c["cpu_percent"],
                            "memory_usage_bytes": c.get("memory_usage_bytes"),
                        },
                    }
                )
        return findings

    async def record(self, kind: str, resource: str, detail: dict) -> None:
        """Upsert a single finding immediately (used by the restart-loop detector).

        Unlike `_store`, this never resolves other rows -- it's a one-off
        finding outside the periodic full scan.
        """
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(Recommendation).where(Recommendation.kind == kind, Recommendation.resource == resource)
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(Recommendation(kind=kind, resource=resource, detail=json.dumps(detail)))
            elif row.status != "dismissed":
                row.status = "active"
                row.detail = json.dumps(detail)
                row.updated_at = now
            await db.commit()
        await self._publish()

    async def _store(self, findings: list[dict]) -> None:
        """Full-scan semantics: upsert findings, resolve active rows no longer found."""
        seen = {(f["kind"], f["resource"]) for f in findings}
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            existing = (await db.execute(select(Recommendation))).scalars().all()
            by_key = {(r.kind, r.resource): r for r in existing}
            for f in findings:
                row = by_key.get((f["kind"], f["resource"]))
                if row is None:
                    db.add(Recommendation(kind=f["kind"], resource=f["resource"], detail=json.dumps(f["detail"])))
                elif row.status != "dismissed":
                    row.status = "active"
                    row.detail = json.dumps(f["detail"])
                    row.updated_at = now
            for (kind, resource), row in by_key.items():
                if kind in SCAN_KINDS and (kind, resource) not in seen and row.status == "active":
                    row.status = "resolved"
                    row.updated_at = now
            await db.commit()
        await self._publish()

    async def _publish(self) -> None:
        async with session_factory() as db:
            rows = (
                await db.execute(select(Recommendation).where(Recommendation.status == "active"))
            ).scalars().all()
        payload = [
            {
                "id": r.id,
                "kind": r.kind,
                "resource": r.resource,
                "detail": json.loads(r.detail or "{}"),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]
        bus.publish("recommendations_updated", {"recommendations": payload})


recommendations = RecommendationService()
