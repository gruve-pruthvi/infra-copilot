"""Settings persistence + in-memory cache.

Provider API keys are write-only through the API: GET returns masked values.
The gateway and LLM layer read from the in-memory cache.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db.database import session_factory
from ..db.models import Setting

SECRET_KEYS = {"anthropic_api_key", "openai_api_key", "google_api_key", "auth_pin_hash"}

DEFAULTS = {
    "active_provider": "anthropic",
    "active_model": "claude-opus-4-8",
    "dry_run_mode": "false",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "google_api_key": "",
    # Local auth. Empty pin hash == auth not configured == enforcement off.
    "auth_pin_hash": "",
    "auth_default_role": "admin",
}


class SettingsStore:
    def __init__(self) -> None:
        self._cache: dict[str, str] = dict(DEFAULTS)

    async def load(self) -> None:
        async with session_factory() as db:
            rows = (await db.execute(select(Setting))).scalars().all()
        self._cache = dict(DEFAULTS)
        for row in rows:
            self._cache[row.key] = row.value

    def get(self, key: str, default: str = "") -> str:
        return self._cache.get(key, default)

    @property
    def dry_run_mode(self) -> bool:
        return self._cache.get("dry_run_mode", "false").lower() == "true"

    def masked(self) -> dict[str, str]:
        out = {}
        for k, v in self._cache.items():
            if k in SECRET_KEYS and v:
                out[k] = f"{'*' * 8}{v[-4:]}" if len(v) > 4 else "*" * 8
            else:
                out[k] = v
        return out

    async def update(self, values: dict[str, str]) -> None:
        async with session_factory() as db:
            for key, value in values.items():
                if key not in DEFAULTS:
                    continue
                row = await db.get(Setting, key)
                if row is None:
                    db.add(Setting(key=key, value=value))
                else:
                    row.value = value
                self._cache[key] = value
            await db.commit()


store = SettingsStore()
