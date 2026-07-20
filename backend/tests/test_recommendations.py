"""Contract tests for the deterministic cost/waste scanner.

Two halves: `_scan_sync` (pure, docker-py + monitor snapshot mocked out) and
`_store` (async, hits the real test DB via `db_ready`).
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.db.database import session_factory
from app.db.models import Recommendation
from app.services.recommendations import RecommendationService


def test_scan_sync_finds_waste(monkeypatch):
    svc = RecommendationService()

    class FakeImage:
        short_id = "sha256:abc123"
        attrs = {"Size": 120_000_000}

    class FakeVol:
        name = "seed-orphan-volume"

    class FakeCli:
        class images:
            @staticmethod
            def list(filters=None):
                return [FakeImage()] if filters == {"dangling": True} else []

        class volumes:
            @staticmethod
            def list():
                return [FakeVol()]

        class containers:
            @staticmethod
            def list(all=True):
                return []

    monkeypatch.setattr("app.services.recommendations.docker.from_env", lambda: FakeCli())
    monkeypatch.setattr("app.services.recommendations.monitor", type("M", (), {"containers": []})())
    findings = svc._scan_sync()
    kinds = {f["kind"] for f in findings}
    assert "dangling_image" in kinds and "orphan_volume" in kinds


async def test_store_upserts_and_resolves(db_ready):
    svc = RecommendationService()
    await svc._store([{"kind": "orphan_volume", "resource": "v1", "detail": {}}])
    await svc._store([{"kind": "orphan_volume", "resource": "v1", "detail": {}}])  # same again -> still 1 row
    async with session_factory() as db:
        rows = (await db.execute(select(Recommendation))).scalars().all()
        assert len(rows) == 1 and rows[0].status == "active"
    await svc._store([])  # gone from scan -> resolved
    async with session_factory() as db:
        rows = (await db.execute(select(Recommendation))).scalars().all()
        assert rows[0].status == "resolved"


async def test_record_upserts_without_resolving_others(db_ready):
    """RecommendationService.record (used by the restart-loop detector, Task 10)
    upserts a single finding without touching unrelated active rows.
    """
    svc = RecommendationService()
    await svc._store([{"kind": "orphan_volume", "resource": "v1", "detail": {}}])
    await svc.record("restart_loop", "flaky-postgres", {"restart_count": 7})
    async with session_factory() as db:
        rows = (await db.execute(select(Recommendation))).scalars().all()
        by_kind = {r.kind: r for r in rows}
        assert by_kind["orphan_volume"].status == "active"
        assert by_kind["restart_loop"].status == "active"
        assert by_kind["restart_loop"].resource == "flaky-postgres"


async def test_dismissed_row_is_not_reactivated_by_rescan(db_ready):
    svc = RecommendationService()
    reco_query = select(Recommendation).where(
        Recommendation.kind == "orphan_volume", Recommendation.resource == "v2-dismiss-test"
    )
    await svc._store([{"kind": "orphan_volume", "resource": "v2-dismiss-test", "detail": {}}])
    async with session_factory() as db:
        row = (await db.execute(reco_query)).scalar_one()
        row.status = "dismissed"
        await db.commit()
    await svc._store([{"kind": "orphan_volume", "resource": "v2-dismiss-test", "detail": {}}])
    async with session_factory() as db:
        row = (await db.execute(reco_query)).scalar_one()
        assert row.status == "dismissed"


async def test_record_collision_updates_existing_row_instead_of_raising(db_ready):
    """Regression test for the SELECT-then-insert race on uq_reco_kind_resource:
    if a row for (kind, resource) already exists by the time `record` runs (e.g.
    a manual POST /api/recommendations/scan racing the background loop), it must
    update in place rather than raising IntegrityError and 500ing.
    """
    svc = RecommendationService()
    key_query = select(Recommendation).where(
        Recommendation.kind == "restart_loop", Recommendation.resource == "race-preexisting"
    )
    async with session_factory() as db:
        db.add(Recommendation(kind="restart_loop", resource="race-preexisting", detail="{}"))
        await db.commit()

    await svc.record("restart_loop", "race-preexisting", {"restart_count": 7})  # must not raise

    async with session_factory() as db:
        rows = (await db.execute(key_query)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "active"
        assert json.loads(rows[0].detail) == {"restart_count": 7}


async def test_store_collision_updates_existing_row_instead_of_raising(db_ready):
    """Same regression, exercised through the full-scan `_store` path: a row
    pre-inserted out-of-band for a (kind, resource) the scan is about to report
    must be updated, not duplicated or raise IntegrityError.
    """
    svc = RecommendationService()
    key_query = select(Recommendation).where(
        Recommendation.kind == "orphan_volume", Recommendation.resource == "v3-race"
    )
    async with session_factory() as db:
        db.add(Recommendation(kind="orphan_volume", resource="v3-race", detail="{}"))
        await db.commit()

    await svc._store([{"kind": "orphan_volume", "resource": "v3-race", "detail": {"size_mb": 1}}])

    async with session_factory() as db:
        rows = (await db.execute(key_query)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "active"
        assert json.loads(rows[0].detail) == {"size_mb": 1}


async def test_concurrent_record_calls_on_new_key_do_not_raise(db_ready):
    """Genuine two-writer race: two callers upsert the same brand-new (kind,
    resource) concurrently. Before the fix this was a classic SELECT-then-insert
    TOCTOU -- both see no row, both try to INSERT, the loser gets IntegrityError.
    The atomic ON CONFLICT DO UPDATE upsert makes this race-free: exactly one
    row survives and neither call raises.
    """
    svc_a = RecommendationService()
    svc_b = RecommendationService()
    await asyncio.gather(
        svc_a.record("dangling_image", "sha256:race", {"attempt": "a"}),
        svc_b.record("dangling_image", "sha256:race", {"attempt": "b"}),
    )
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Recommendation).where(
                    Recommendation.kind == "dangling_image", Recommendation.resource == "sha256:race"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "active"


async def test_start_then_stop_completes_and_task_is_done():
    """stop() must cancel *and await* the scanner task (mirroring monitor.py's
    stop() pattern), not just call cancel() and return -- otherwise the task
    keeps running past stop() and callers get no signal it actually finished.
    """
    svc = RecommendationService()
    svc.start()
    assert svc._task is not None and not svc._task.done()

    await svc.stop()

    assert svc._task.done()
    assert svc._task.cancelled()
