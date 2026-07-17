"""Contract tests for the deterministic cost/waste scanner.

Two halves: `_scan_sync` (pure, docker-py + monitor snapshot mocked out) and
`_store` (async, hits the real test DB via `db_ready`).
"""

from __future__ import annotations

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
