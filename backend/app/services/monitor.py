"""Background monitoring loop.

Polls Docker + host metrics every few seconds (and Kubernetes less often),
keeps the latest snapshots plus an in-memory metrics ring buffer, and
publishes updates on the event bus so the UI refreshes live.

This path intentionally talks to docker-py / psutil / kubernetes directly
(not through MCP): the MCP servers are the *agent's* tool surface; the
dashboard needs no LLM hop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any

import psutil

from ..config import settings
from .event_bus import bus

log = logging.getLogger(__name__)


class MonitorService:
    def __init__(self) -> None:
        self.containers: list[dict[str, Any]] = []
        self.docker_available = False
        self.docker_error: str | None = None
        self.system: dict[str, Any] = {}
        self.k8s: dict[str, Any] = {"available": False, "reason": "not polled yet"}
        maxlen = int(settings.history_window_minutes * 60 / settings.monitor_interval_seconds)
        self.history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._docker_client = None
        self._tasks: list[asyncio.Task] = []

    # -- docker ------------------------------------------------------------

    def _get_docker(self):
        import docker

        if self._docker_client is None:
            self._docker_client = docker.from_env()
        return self._docker_client

    def _poll_docker_sync(self) -> list[dict[str, Any]]:
        client = self._get_docker()
        out = []
        for c in client.containers.list(all=True):
            attrs = c.attrs
            state = attrs.get("State", {})
            entry = {
                "id": c.short_id,
                "name": c.name,
                "image": c.image.tags[0] if c.image and c.image.tags else attrs.get("Config", {}).get("Image", ""),
                "status": c.status,
                "restart_count": attrs.get("RestartCount", 0),
                "exit_code": state.get("ExitCode"),
                "started_at": state.get("StartedAt"),
                "ports": attrs.get("NetworkSettings", {}).get("Ports") or {},
                "cpu_percent": None,
                "memory_usage_bytes": None,
                "memory_percent": None,
            }
            if c.status == "running":
                try:
                    raw = c.stats(stream=False)
                    entry.update(_calc_stats(raw))
                except Exception:  # noqa: BLE001
                    pass
            out.append(entry)
        return out

    # -- system ------------------------------------------------------------

    def _poll_system_sync(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "ts": time.time(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count(),
            "memory_percent": vm.percent,
            "memory_used_bytes": vm.used,
            "memory_total_bytes": vm.total,
            "disk_percent": disk.percent,
            "disk_used_bytes": disk.used,
            "disk_total_bytes": disk.total,
        }

    # -- k8s ---------------------------------------------------------------

    def _poll_k8s_sync(self) -> dict[str, Any]:
        try:
            from kubernetes import client, config

            config.load_kube_config()
            core = client.CoreV1Api()
            apps = client.AppsV1Api()
            namespaces = [ns.metadata.name for ns in core.list_namespace(_request_timeout=5).items]
            pods = []
            for p in core.list_pod_for_all_namespaces(_request_timeout=5).items:
                restarts = sum(cs.restart_count for cs in (p.status.container_statuses or []))
                pods.append(
                    {
                        "name": p.metadata.name,
                        "namespace": p.metadata.namespace,
                        "phase": p.status.phase,
                        "restarts": restarts,
                    }
                )
            deployments = [
                {
                    "name": d.metadata.name,
                    "namespace": d.metadata.namespace,
                    "ready": f"{d.status.ready_replicas or 0}/{d.spec.replicas or 0}",
                }
                for d in apps.list_deployment_for_all_namespaces(_request_timeout=5).items
            ]
            return {"available": True, "namespaces": namespaces, "pods": pods, "deployments": deployments}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": f"{type(e).__name__}: {e}"}

    # -- loops ---------------------------------------------------------------

    async def _docker_system_loop(self) -> None:
        while True:
            try:
                self.containers = await asyncio.to_thread(self._poll_docker_sync)
                self.docker_available = True
                self.docker_error = None
            except Exception as e:  # noqa: BLE001
                self.docker_available = False
                self.docker_error = str(e)
                self._docker_client = None
                self.containers = []
            try:
                self.system = await asyncio.to_thread(self._poll_system_sync)
                self.history.append(
                    {
                        "ts": self.system["ts"],
                        "cpu_percent": self.system["cpu_percent"],
                        "memory_percent": self.system["memory_percent"],
                    }
                )
            except Exception:  # noqa: BLE001
                log.exception("system poll failed")
            bus.publish("containers_snapshot", {"available": self.docker_available, "error": self.docker_error, "containers": self.containers})
            bus.publish("system_metrics", self.system)
            await asyncio.sleep(settings.monitor_interval_seconds)

    async def _k8s_loop(self) -> None:
        while True:
            self.k8s = await asyncio.to_thread(self._poll_k8s_sync)
            bus.publish("k8s_snapshot", self.k8s)
            await asyncio.sleep(settings.k8s_interval_seconds)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._docker_system_loop(), name="monitor-docker"),
            asyncio.create_task(self._k8s_loop(), name="monitor-k8s"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t

    def history_series(self) -> list[dict[str, Any]]:
        return list(self.history)


def _calc_stats(raw: dict[str, Any]) -> dict[str, Any]:
    cpu_pct = 0.0
    try:
        cpu = raw["cpu_stats"]
        pre = raw["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        n_cpus = cpu.get("online_cpus") or 1
        if sys_delta > 0 and cpu_delta >= 0:
            cpu_pct = (cpu_delta / sys_delta) * n_cpus * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    mem = raw.get("memory_stats", {})
    usage = (mem.get("usage", 0) or 0) - (mem.get("stats", {}).get("inactive_file", 0) or 0)
    return {
        "cpu_percent": round(cpu_pct, 2),
        "memory_usage_bytes": max(usage, 0),
        "memory_percent": round((max(usage, 0) / mem["limit"]) * 100.0, 2) if mem.get("limit") else None,
    }


monitor = MonitorService()
