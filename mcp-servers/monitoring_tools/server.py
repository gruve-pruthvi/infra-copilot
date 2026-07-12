"""System monitoring MCP server (stdio) — READ-ONLY.

psutil-based host metrics plus process listing. Per-container stats live in
docker_tools (all_container_stats); metric *history* is served natively by the
backend (its in-memory ring buffer), not by this server.
"""

from __future__ import annotations

import psutil
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("monitoring-tools")

READ_ONLY = ToolAnnotations(readOnlyHint=True)


@mcp.tool(annotations=READ_ONLY)
def system_overview() -> dict:
    """Current host snapshot: CPU, memory, disk, network, load average."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    load = psutil.getloadavg()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_count": psutil.cpu_count(),
        "load_avg": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "memory": {
            "total_bytes": vm.total,
            "used_bytes": vm.used,
            "available_bytes": vm.available,
            "percent": vm.percent,
        },
        "disk_root": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": disk.percent,
        },
        "network": {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv},
    }


@mcp.tool(annotations=READ_ONLY)
def top_processes(by: str = "memory", limit: int = 15) -> dict:
    """Top processes by 'memory' or 'cpu'. Useful to explain what is consuming RAM/CPU."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "username"]):
        try:
            info = p.info
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "memory_bytes": info["memory_info"].rss if info["memory_info"] else 0,
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "user": info["username"],
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "memory_bytes" if by != "cpu" else "cpu_percent"
    procs.sort(key=lambda x: x[key], reverse=True)
    return {"by": by, "processes": procs[: max(1, min(limit, 50))]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
