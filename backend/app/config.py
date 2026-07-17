from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: Path = Path.home() / ".infra-copilot"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    approval_ttl_seconds: int = 900          # pending approvals expire after 15 min
    tool_timeout_seconds: float = 180.0      # per-tool-call execution timeout
    max_tool_calls_per_turn: int = 25        # runaway-loop guard

    monitor_interval_seconds: float = 5.0    # docker + system snapshot cadence
    k8s_interval_seconds: float = 15.0       # k8s snapshot cadence
    history_window_minutes: int = 60         # in-memory metrics retention
    history_char_budget: int = 400_000       # agent message history character budget

    cost_scan_interval_hours: float = 24.0   # deterministic waste-scan cadence

    model_config = {"env_prefix": "INFRA_COPILOT_"}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "infra-copilot.db"

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
