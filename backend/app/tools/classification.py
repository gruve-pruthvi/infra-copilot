"""Authoritative tool safety classification.

The gateway trusts ONLY this table — never MCP server annotations, which
travel with the (in principle untrusted) server process. Any tool not listed
here is treated as DESTRUCTIVE, so new tools fail closed.
"""

from __future__ import annotations

import enum

from ..config import settings


class Tier(str, enum.Enum):
    READ = "read"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


SAFETY: dict[str, Tier] = {
    # docker — read
    "docker.list_containers": Tier.READ,
    "docker.inspect_container": Tier.READ,
    "docker.container_stats": Tier.READ,
    "docker.all_container_stats": Tier.READ,
    "docker.get_container_logs": Tier.READ,
    "docker.list_images": Tier.READ,
    "docker.list_volumes": Tier.READ,
    "docker.list_networks": Tier.READ,
    "docker.docker_disk_usage": Tier.READ,
    "docker.docker_events_recent": Tier.READ,
    "docker.compose_ps": Tier.READ,
    "docker.compose_logs": Tier.READ,
    # docker — mutating
    "docker.run_container": Tier.MUTATING,
    "docker.start_container": Tier.MUTATING,
    "docker.stop_container": Tier.MUTATING,
    "docker.restart_container": Tier.MUTATING,
    "docker.pull_image": Tier.MUTATING,
    "docker.create_volume": Tier.MUTATING,
    "docker.create_network": Tier.MUTATING,
    "docker.compose_up": Tier.MUTATING,
    # docker — destructive
    "docker.remove_container": Tier.DESTRUCTIVE,
    "docker.remove_image": Tier.DESTRUCTIVE,
    "docker.remove_volume": Tier.DESTRUCTIVE,
    "docker.prune_images": Tier.DESTRUCTIVE,
    "docker.prune_volumes": Tier.DESTRUCTIVE,
    "docker.compose_down": Tier.DESTRUCTIVE,
    # k8s — read-only in MVP
    "k8s.list_contexts": Tier.READ,
    "k8s.list_namespaces": Tier.READ,
    "k8s.list_pods": Tier.READ,
    "k8s.list_deployments": Tier.READ,
    "k8s.list_services": Tier.READ,
    "k8s.get_pod_logs": Tier.READ,
    "k8s.list_events": Tier.READ,
    "k8s.describe_pod": Tier.READ,
    # monitoring
    "monitoring.system_overview": Tier.READ,
    "monitoring.top_processes": Tier.READ,
    # backend-native tools
    "native.system_history": Tier.READ,
    "native.write_manifest": Tier.MUTATING,
}


def classify(server: str, tool: str) -> Tier:
    return SAFETY.get(f"{server}.{tool}", Tier.DESTRUCTIVE)


# Per-tool timeout overrides in seconds. Anything absent uses the global default.
TIMEOUTS: dict[str, float] = {
    "docker.pull_image": 600.0,
    "docker.compose_up": 600.0,
    "docker.compose_down": 300.0,
}


def timeout_for(server: str, tool: str) -> float:
    return TIMEOUTS.get(f"{server}.{tool}", settings.tool_timeout_seconds)
