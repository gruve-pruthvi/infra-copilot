"""Docker MCP server (stdio).

Read, mutating, and destructive Docker tools. Every mutating/destructive tool
accepts ``dry_run`` and performs validation-only when it is set. Approval
gating happens in the backend's ToolGateway — this server assumes its caller
has already been authorized, but still never acts when dry_run=True.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import docker.errors
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("docker-tools")

_client: docker.DockerClient | None = None

READ_ONLY = ToolAnnotations(readOnlyHint=True)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def _docker() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _err(e: Exception) -> dict[str, Any]:
    return {"error": True, "type": type(e).__name__, "message": str(e)}


def _container_summary(c: Any) -> dict[str, Any]:
    attrs = c.attrs
    state = attrs.get("State", {})
    return {
        "id": c.short_id,
        "name": c.name,
        "image": (c.image.tags[0] if c.image and c.image.tags else attrs.get("Config", {}).get("Image", "")),
        "status": c.status,
        "state": {
            "running": state.get("Running"),
            "restarting": state.get("Restarting"),
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
        },
        "restart_count": attrs.get("RestartCount", 0),
        "created": attrs.get("Created"),
        "ports": attrs.get("NetworkSettings", {}).get("Ports") or {},
        "labels": attrs.get("Config", {}).get("Labels") or {},
    }


def _calc_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw docker stats sample into friendly percentages/bytes."""
    cpu_pct = 0.0
    try:
        cpu = raw["cpu_stats"]
        pre = raw["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        n_cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        if sys_delta > 0 and cpu_delta >= 0:
            cpu_pct = (cpu_delta / sys_delta) * n_cpus * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    mem = raw.get("memory_stats", {})
    usage = mem.get("usage", 0) or 0
    # cgroup v2 reports inactive_file cache inside usage
    usage -= mem.get("stats", {}).get("inactive_file", 0) or 0
    return {
        "cpu_percent": round(cpu_pct, 2),
        "memory_usage_bytes": max(usage, 0),
        "memory_limit_bytes": mem.get("limit", 0),
        "memory_percent": round((max(usage, 0) / mem["limit"]) * 100.0, 2) if mem.get("limit") else 0.0,
    }


# --------------------------------------------------------------------------
# READ tools
# --------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def list_containers(all: bool = True) -> dict:
    """List Docker containers (running and stopped by default)."""
    try:
        return {"containers": [_container_summary(c) for c in _docker().containers.list(all=all)]}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def inspect_container(container: str) -> dict:
    """Full inspect data for one container (by name or id). Env var values that look like secrets are redacted."""
    try:
        attrs = _docker().containers.get(container).attrs
        env = attrs.get("Config", {}).get("Env") or []
        redacted = []
        for item in env:
            key = item.split("=", 1)[0]
            if any(s in key.lower() for s in ("password", "secret", "token", "key")):
                redacted.append(f"{key}=***REDACTED***")
            else:
                redacted.append(item)
        attrs.setdefault("Config", {})["Env"] = redacted
        return attrs
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def container_stats(container: str) -> dict:
    """One-shot CPU / memory stats for a container."""
    try:
        c = _docker().containers.get(container)
        raw = c.stats(stream=False)
        return {"container": c.name, **_calc_stats(raw)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def all_container_stats() -> dict:
    """CPU / memory stats for every running container. Primary input for cost/waste analysis."""
    try:
        out = []
        for c in _docker().containers.list():
            try:
                raw = c.stats(stream=False)
                out.append({"id": c.short_id, "name": c.name, "image": c.image.tags[0] if c.image.tags else "", **_calc_stats(raw)})
            except Exception as e:  # noqa: BLE001
                out.append({"id": c.short_id, "name": c.name, **_err(e)})
        return {"stats": out}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def get_container_logs(container: str, tail: int = 200, since_minutes: int | None = None) -> dict:
    """Fetch recent logs from a container (stdout+stderr)."""
    try:
        c = _docker().containers.get(container)
        kwargs: dict[str, Any] = {"tail": min(tail, 2000), "timestamps": True}
        if since_minutes:
            kwargs["since"] = datetime.now(timezone.utc).timestamp() - since_minutes * 60
        logs = c.logs(**kwargs).decode("utf-8", errors="replace")
        if len(logs) > 60_000:
            logs = logs[-60_000:]
        return {"container": c.name, "logs": logs}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def list_images(dangling_only: bool = False) -> dict:
    """List local Docker images. dangling_only=True lists untagged (dangling) images."""
    try:
        filters = {"dangling": True} if dangling_only else None
        images = _docker().images.list(filters=filters)
        return {
            "images": [
                {
                    "id": i.short_id,
                    "tags": i.tags,
                    "size_bytes": i.attrs.get("Size", 0),
                    "created": i.attrs.get("Created"),
                }
                for i in images
            ]
        }
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def list_volumes() -> dict:
    """List Docker volumes and whether each is used by any container."""
    try:
        client = _docker()
        used: set[str] = set()
        for c in client.containers.list(all=True):
            for m in c.attrs.get("Mounts", []):
                if m.get("Type") == "volume" and m.get("Name"):
                    used.add(m["Name"])
        vols = client.volumes.list()
        return {
            "volumes": [
                {"name": v.name, "driver": v.attrs.get("Driver"), "created": v.attrs.get("CreatedAt"), "in_use": v.name in used}
                for v in vols
            ]
        }
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def list_networks() -> dict:
    """List Docker networks."""
    try:
        return {
            "networks": [
                {"id": n.short_id, "name": n.name, "driver": n.attrs.get("Driver"), "containers": list((n.attrs.get("Containers") or {}).keys())}
                for n in _docker().networks.list()
            ]
        }
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def docker_disk_usage() -> dict:
    """Docker disk usage summary (images, containers, volumes, build cache) — like `docker system df`."""
    try:
        df = _docker().df()
        def _sum(items: list, key: str = "Size") -> int:
            return sum((i.get(key) or 0) for i in (items or []))
        return {
            "images_count": len(df.get("Images") or []),
            "images_size_bytes": _sum(df.get("Images")),
            "containers_count": len(df.get("Containers") or []),
            "containers_size_bytes": _sum(df.get("Containers"), "SizeRw"),
            "volumes_count": len(df.get("Volumes") or []),
            "volumes_size_bytes": sum(((v.get("UsageData") or {}).get("Size") or 0) for v in (df.get("Volumes") or [])),
        }
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=READ_ONLY)
def docker_events_recent(minutes: int = 30) -> dict:
    """Recent Docker daemon events (create/start/die/oom/stop...) in the last N minutes. Useful for root-cause analysis."""
    try:
        now = datetime.now(timezone.utc).timestamp()
        events = []
        for raw in _docker().events(since=int(now - minutes * 60), until=int(now), decode=True):
            events.append(
                {
                    "time": raw.get("time"),
                    "type": raw.get("Type"),
                    "action": raw.get("Action"),
                    "actor": (raw.get("Actor") or {}).get("Attributes", {}).get("name"),
                    "exit_code": (raw.get("Actor") or {}).get("Attributes", {}).get("exitCode"),
                }
            )
            if len(events) >= 300:
                break
        return {"events": events}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------
# MUTATING tools
# --------------------------------------------------------------------------

@mcp.tool(annotations=MUTATING)
def run_container(
    image: str,
    name: str | None = None,
    ports: dict[str, int] | None = None,
    env: dict[str, str] | None = None,
    volumes: dict[str, str] | None = None,
    command: str | None = None,
    restart_policy: str = "no",
    detach: bool = True,
    dry_run: bool = False,
) -> dict:
    """Create and start a container.

    ports maps container port ("80/tcp" or "80") to host port. volumes maps
    volume-name-or-host-path to container path. restart_policy: no|always|unless-stopped|on-failure.
    """
    try:
        client = _docker()
        port_map = {(k if "/" in k else f"{k}/tcp"): v for k, v in (ports or {}).items()}
        vol_map = {k: {"bind": v, "mode": "rw"} for k, v in (volumes or {}).items()}
        conflicts = []
        if name:
            try:
                client.containers.get(name)
                conflicts.append(f"container name '{name}' already in use")
            except docker.errors.NotFound:
                pass
        if dry_run:
            image_local = True
            try:
                client.images.get(image)
            except docker.errors.ImageNotFound:
                image_local = False
            return {
                "dry_run": True,
                "would_create": {"image": image, "name": name, "ports": port_map, "env_keys": sorted((env or {}).keys()), "volumes": vol_map, "restart_policy": restart_policy},
                "image_present_locally": image_local,
                "conflicts": conflicts,
            }
        if conflicts:
            return {"error": True, "message": "; ".join(conflicts)}
        try:
            client.images.get(image)
        except docker.errors.ImageNotFound:
            client.images.pull(image)
        container = client.containers.run(
            image,
            command=command,
            name=name,
            ports=port_map or None,
            environment=env or None,
            volumes=vol_map or None,
            restart_policy={"Name": restart_policy} if restart_policy != "no" else None,
            detach=detach,
        )
        container.reload()
        return {"created": _container_summary(container)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _simple_container_action(container: str, action: str, dry_run: bool, **kwargs: Any) -> dict:
    try:
        c = _docker().containers.get(container)
        if dry_run:
            return {"dry_run": True, "would": action, "container": c.name, "current_status": c.status}
        getattr(c, action)(**kwargs)
        c.reload()
        return {"container": c.name, "action": action, "status": c.status}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=MUTATING)
def start_container(container: str, dry_run: bool = False) -> dict:
    """Start a stopped container."""
    return _simple_container_action(container, "start", dry_run)


@mcp.tool(annotations=MUTATING)
def stop_container(container: str, timeout: int = 10, dry_run: bool = False) -> dict:
    """Stop a running container."""
    return _simple_container_action(container, "stop", dry_run, timeout=timeout)


@mcp.tool(annotations=MUTATING)
def restart_container(container: str, timeout: int = 10, dry_run: bool = False) -> dict:
    """Restart a container."""
    return _simple_container_action(container, "restart", dry_run, timeout=timeout)


@mcp.tool(annotations=MUTATING)
def pull_image(image: str, dry_run: bool = False) -> dict:
    """Pull an image from a registry. May take a while for large images."""
    try:
        if dry_run:
            return {"dry_run": True, "would_pull": image}
        img = _docker().images.pull(image)
        return {"pulled": image, "id": img.short_id, "tags": img.tags}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=MUTATING)
def create_volume(name: str, dry_run: bool = False) -> dict:
    """Create a named Docker volume."""
    try:
        if dry_run:
            return {"dry_run": True, "would_create_volume": name}
        v = _docker().volumes.create(name)
        return {"created_volume": v.name}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=MUTATING)
def create_network(name: str, driver: str = "bridge", dry_run: bool = False) -> dict:
    """Create a Docker network."""
    try:
        if dry_run:
            return {"dry_run": True, "would_create_network": name, "driver": driver}
        n = _docker().networks.create(name, driver=driver)
        return {"created_network": n.name, "id": n.short_id}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------
# DESTRUCTIVE tools
# --------------------------------------------------------------------------

@mcp.tool(annotations=DESTRUCTIVE)
def remove_container(container: str, force: bool = False, dry_run: bool = False) -> dict:
    """Remove a container. force=True also kills a running container."""
    try:
        c = _docker().containers.get(container)
        if dry_run:
            return {"dry_run": True, "would_remove": c.name, "running": c.status == "running", "force": force}
        name = c.name
        c.remove(force=force)
        return {"removed_container": name}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=DESTRUCTIVE)
def remove_image(image: str, force: bool = False, dry_run: bool = False) -> dict:
    """Remove a local image."""
    try:
        if dry_run:
            img = _docker().images.get(image)
            return {"dry_run": True, "would_remove_image": image, "tags": img.tags, "size_bytes": img.attrs.get("Size", 0)}
        _docker().images.remove(image, force=force)
        return {"removed_image": image}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=DESTRUCTIVE)
def remove_volume(name: str, force: bool = False, dry_run: bool = False) -> dict:
    """Remove a Docker volume. Data in the volume is permanently lost."""
    try:
        v = _docker().volumes.get(name)
        if dry_run:
            return {"dry_run": True, "would_remove_volume": name}
        v.remove(force=force)
        return {"removed_volume": name}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=DESTRUCTIVE)
def prune_images(dry_run: bool = False) -> dict:
    """Remove all dangling images."""
    try:
        if dry_run:
            dangling = _docker().images.list(filters={"dangling": True})
            return {"dry_run": True, "would_remove_count": len(dangling), "would_reclaim_bytes": sum(i.attrs.get("Size", 0) for i in dangling)}
        res = _docker().images.prune(filters={"dangling": True})
        return {"removed": len(res.get("ImagesDeleted") or []), "reclaimed_bytes": res.get("SpaceReclaimed", 0)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(annotations=DESTRUCTIVE)
def prune_volumes(dry_run: bool = False) -> dict:
    """Remove all unused (dangling) volumes. Data is permanently lost."""
    try:
        if dry_run:
            unused = json.loads(json.dumps(list_volumes()))  # reuse logic
            names = [v["name"] for v in unused.get("volumes", []) if not v.get("in_use")]
            return {"dry_run": True, "would_remove": names}
        res = _docker().volumes.prune()
        return {"removed": res.get("VolumesDeleted") or [], "reclaimed_bytes": res.get("SpaceReclaimed", 0)}
    except Exception as e:  # noqa: BLE001
        return _err(e)



# --------------------------------------------------------------------------
# Docker Compose tools
# --------------------------------------------------------------------------


def _compose_base(project_dir: str, file: str | None) -> list[str] | dict:
    d = Path(project_dir).expanduser()
    if not d.is_dir():
        return {"error": True, "type": "NotADirectory", "message": f"{project_dir} is not a directory"}
    cmd = ["docker", "compose", "--project-directory", str(d)]
    if file:
        cmd += ["-f", str(Path(file).expanduser())]
    return cmd


def _run_compose(cmd: list[str], timeout: float = 570.0) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"error": True, "type": "DockerCLIMissing", "message": "docker CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": True, "type": "Timeout", "message": f"compose command exceeded {timeout}s"}
    out = (p.stdout or "")[-60_000:]
    err = (p.stderr or "")[-20_000:]
    if p.returncode != 0:
        return {"error": True, "type": "ComposeError", "message": err or out, "exit_code": p.returncode}
    return {"exit_code": 0, "output": out, "stderr": err}


@mcp.tool(annotations=READ_ONLY)
def compose_ps(project_dir: str, file: str | None = None) -> dict:
    """List services of a compose project (docker compose ps)."""
    cmd = _compose_base(project_dir, file)
    if isinstance(cmd, dict):
        return cmd
    res = _run_compose(cmd + ["ps", "--format", "json", "--all"])
    if res.get("error"):
        return res
    services = [json.loads(line) for line in res["output"].splitlines() if line.strip()]
    return {"services": services}


@mcp.tool(annotations=READ_ONLY)
def compose_logs(project_dir: str, service: str | None = None, tail: int = 200, file: str | None = None) -> dict:
    """Logs for a compose project or one service."""
    cmd = _compose_base(project_dir, file)
    if isinstance(cmd, dict):
        return cmd
    args = ["logs", "--no-color", "--tail", str(min(tail, 2000))]
    if service:
        args.append(service)
    res = _run_compose(cmd + args)
    if res.get("error"):
        return res
    return {"logs": res["output"]}


@mcp.tool(annotations=MUTATING)
def compose_up(project_dir: str, file: str | None = None, dry_run: bool = False) -> dict:
    """Start a compose project (docker compose up -d). dry_run validates and returns the resolved config."""
    cmd = _compose_base(project_dir, file)
    if isinstance(cmd, dict):
        return cmd
    if dry_run:
        res = _run_compose(cmd + ["config"])
        if res.get("error"):
            return res
        return {"dry_run": True, "would": "up -d", "resolved_config": res["output"]}
    res = _run_compose(cmd + ["up", "-d", "--quiet-pull"])
    if res.get("error"):
        return res
    return {"started": True, "output": res["stderr"] or res["output"], **compose_ps(project_dir, file)}


@mcp.tool(annotations=DESTRUCTIVE)
def compose_down(project_dir: str, file: str | None = None, remove_volumes: bool = False, dry_run: bool = False) -> dict:
    """Stop and remove a compose project. remove_volumes also deletes named volumes."""
    cmd = _compose_base(project_dir, file)
    if isinstance(cmd, dict):
        return cmd
    if dry_run:
        return {"dry_run": True, "would": "down" + (" -v" if remove_volumes else ""), **compose_ps(project_dir, file)}
    args = ["down"] + (["-v"] if remove_volumes else [])
    res = _run_compose(cmd + args)
    if res.get("error"):
        return res
    return {"stopped": True, "output": res["stderr"] or res["output"]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
