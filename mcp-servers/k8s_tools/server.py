"""Kubernetes MCP server (stdio).

Every tool degrades gracefully: if there is no kubeconfig or the cluster is
unreachable, tools return {"available": false, "reason": ...} instead of
raising, so the model can explain the situation to the user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml
from kubernetes import client, dynamic
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("k8s-tools")

READ_ONLY = ToolAnnotations(readOnlyHint=True)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_loaded = False


def _load() -> tuple[Any, Any] | dict[str, Any]:
    """Return (CoreV1Api, AppsV1Api) or an unavailable-dict."""
    global _loaded
    try:
        from kubernetes import client, config

        if not _loaded:
            config.load_kube_config()
            _loaded = True
        return client.CoreV1Api(), client.AppsV1Api()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _unavailable(res: Any) -> dict | None:
    return res if isinstance(res, dict) else None


@mcp.tool(annotations=READ_ONLY)
def list_contexts() -> dict:
    """List kubeconfig contexts and which one is active."""
    try:
        from kubernetes import config

        contexts, active = config.list_kube_config_contexts()
        return {
            "available": True,
            "contexts": [c["name"] for c in contexts],
            "active": active["name"] if active else None,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def list_namespaces() -> dict:
    """List Kubernetes namespaces."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        items = core.list_namespace(_request_timeout=10).items
        return {"available": True, "namespaces": [ns.metadata.name for ns in items]}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def list_pods(namespace: str = "default") -> dict:
    """List pods in a namespace with status, restarts, and node."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        pods = core.list_namespaced_pod(namespace, _request_timeout=10).items
        out = []
        for p in pods:
            restarts = sum(cs.restart_count for cs in (p.status.container_statuses or []))
            waiting_reasons = [
                cs.state.waiting.reason
                for cs in (p.status.container_statuses or [])
                if cs.state and cs.state.waiting
            ]
            out.append(
                {
                    "name": p.metadata.name,
                    "phase": p.status.phase,
                    "ready": all(cs.ready for cs in (p.status.container_statuses or [])) if p.status.container_statuses else False,
                    "restarts": restarts,
                    "waiting_reasons": waiting_reasons,
                    "node": p.spec.node_name,
                    "started": str(p.status.start_time) if p.status.start_time else None,
                }
            )
        return {"available": True, "namespace": namespace, "pods": out}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def list_deployments(namespace: str = "default") -> dict:
    """List deployments in a namespace with replica readiness."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    _, apps = apis
    try:
        deps = apps.list_namespaced_deployment(namespace, _request_timeout=10).items
        return {
            "available": True,
            "namespace": namespace,
            "deployments": [
                {
                    "name": d.metadata.name,
                    "replicas_desired": d.spec.replicas,
                    "replicas_ready": d.status.ready_replicas or 0,
                    "images": [c.image for c in d.spec.template.spec.containers],
                }
                for d in deps
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def list_services(namespace: str = "default") -> dict:
    """List services in a namespace."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        svcs = core.list_namespaced_service(namespace, _request_timeout=10).items
        return {
            "available": True,
            "namespace": namespace,
            "services": [
                {
                    "name": s.metadata.name,
                    "type": s.spec.type,
                    "cluster_ip": s.spec.cluster_ip,
                    "ports": [{"port": p.port, "target": str(p.target_port), "node_port": p.node_port} for p in (s.spec.ports or [])],
                }
                for s in svcs
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def get_pod_logs(pod: str, namespace: str = "default", container: str | None = None, tail: int = 200) -> dict:
    """Fetch recent logs from a pod."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        logs = core.read_namespaced_pod_log(
            pod, namespace, container=container, tail_lines=min(tail, 2000), _request_timeout=15
        )
        if len(logs) > 60_000:
            logs = logs[-60_000:]
        return {"available": True, "pod": pod, "logs": logs}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def list_events(namespace: str = "default", limit: int = 50) -> dict:
    """Recent Kubernetes events in a namespace (scheduling failures, image pull errors, OOM kills...)."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        events = core.list_namespaced_event(namespace, _request_timeout=10).items
        events.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
        return {
            "available": True,
            "namespace": namespace,
            "events": [
                {
                    "type": e.type,
                    "reason": e.reason,
                    "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "message": e.message,
                    "count": e.count,
                    "last_seen": str(e.last_timestamp or e.event_time or ""),
                }
                for e in events[:limit]
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=READ_ONLY)
def describe_pod(pod: str, namespace: str = "default") -> dict:
    """Detailed pod info: containers, images, resources, conditions, container states."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, _ = apis
    try:
        p = core.read_namespaced_pod(pod, namespace, _request_timeout=10)
        containers = []
        statuses = {cs.name: cs for cs in (p.status.container_statuses or [])}
        for c in p.spec.containers:
            cs = statuses.get(c.name)
            state = None
            if cs and cs.state:
                if cs.state.running:
                    state = {"running": {"started_at": str(cs.state.running.started_at)}}
                elif cs.state.waiting:
                    state = {"waiting": {"reason": cs.state.waiting.reason, "message": cs.state.waiting.message}}
                elif cs.state.terminated:
                    state = {"terminated": {"reason": cs.state.terminated.reason, "exit_code": cs.state.terminated.exit_code}}
            containers.append(
                {
                    "name": c.name,
                    "image": c.image,
                    "resources": {"requests": c.resources.requests, "limits": c.resources.limits} if c.resources else None,
                    "restart_count": cs.restart_count if cs else None,
                    "state": state,
                }
            )
        return {
            "available": True,
            "pod": p.metadata.name,
            "namespace": namespace,
            "phase": p.status.phase,
            "node": p.spec.node_name,
            "conditions": [{"type": c.type, "status": c.status, "reason": c.reason} for c in (p.status.conditions or [])],
            "containers": containers,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _write_kwargs(dry_run: bool) -> dict:
    kwargs = {"_request_timeout": 15}
    if dry_run:
        kwargs["dry_run"] = "All"  # server-side dry run: fully validated, nothing persisted
    return kwargs


@mcp.tool(annotations=MUTATING)
def scale_deployment(deployment: str, replicas: int, namespace: str = "default", dry_run: bool = False) -> dict:
    """Scale a deployment to the given replica count."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        res = apps.patch_namespaced_deployment_scale(
            deployment, namespace, {"spec": {"replicas": int(replicas)}}, **_write_kwargs(dry_run)
        )
        return {"available": True, "dry_run": dry_run, "deployment": deployment,
                "namespace": namespace, "replicas": res.spec.replicas}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=MUTATING)
def rollout_restart(deployment: str, namespace: str = "default", dry_run: bool = False) -> dict:
    """Trigger a rolling restart (patches the pod template restartedAt annotation)."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        ts = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations":
                {"kubectl.kubernetes.io/restartedAt": ts}}}}}
        apps.patch_namespaced_deployment(deployment, namespace, body, **_write_kwargs(dry_run))
        return {"available": True, "dry_run": dry_run, "deployment": deployment,
                "namespace": namespace, "restarted_at": ts}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _parse_manifest(manifest_yaml: str) -> list[dict] | dict:
    """Parse one or more YAML documents; pure, no cluster access (unit-testable)."""
    try:
        docs = [d for d in yaml.safe_load_all(manifest_yaml) if d]
    except yaml.YAMLError as e:
        return {"error": True, "type": "InvalidYAML", "message": str(e)}
    if not docs:
        return {"error": True, "type": "EmptyManifest", "message": "no YAML documents found"}
    for d in docs:
        meta = d.get("metadata") if isinstance(d, dict) else None
        if not isinstance(d, dict) or not d.get("apiVersion") or not d.get("kind") \
                or not isinstance(meta, dict) or not meta.get("name"):
            return {"error": True, "type": "InvalidManifest",
                    "message": "every document needs apiVersion, kind and metadata.name"}
    return docs


@mcp.tool(annotations=MUTATING)
def apply_manifest(manifest_yaml: str, namespace: str = "default", dry_run: bool = False) -> dict:
    """Server-side apply of one or more YAML documents (create-or-update)."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    docs = _parse_manifest(manifest_yaml)
    if isinstance(docs, dict):
        return {"available": True, **docs}
    try:
        # SPIKE (Phase 3 Task 2): DynamicClient.server_side_apply exists natively on the
        # installed kubernetes package (confirmed via dir(DynamicClient)); Resource.patch is
        # not a plain method (AttributeError on the class), it's proxied dynamically, so we
        # call the client-level helper instead of the brief's resource.patch(...) fallback.
        dyn = dynamic.DynamicClient(client.ApiClient())
        applied = []
        for doc in docs:
            res = dyn.resources.get(api_version=doc["apiVersion"], kind=doc["kind"])
            ns = doc.get("metadata", {}).get("namespace") or namespace
            kwargs: dict[str, Any] = {"field_manager": "infra-copilot", "force_conflicts": True}
            if res.namespaced:
                kwargs["namespace"] = ns
            if dry_run:
                kwargs["dry_run"] = "All"
            dyn.server_side_apply(res, body=doc, name=doc["metadata"]["name"], **kwargs)
            applied.append({"kind": doc["kind"], "name": doc["metadata"]["name"],
                            "namespace": ns if res.namespaced else None})
        return {"available": True, "dry_run": dry_run, "applied": applied}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=MUTATING)
def rollback_deployment(deployment: str, namespace: str = "default", dry_run: bool = False) -> dict:
    """Roll a deployment back to its previous ReplicaSet revision."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        rss = apps.list_namespaced_replica_set(namespace, _request_timeout=15).items
        owned = [rs for rs in rss if any(
            o.kind == "Deployment" and o.name == deployment
            for o in (rs.metadata.owner_references or []))]
        owned.sort(key=lambda rs: int((rs.metadata.annotations or {})
                                      .get("deployment.kubernetes.io/revision", "0")))
        if len(owned) < 2:
            return {"available": True, "error": True, "message": "no previous revision to roll back to"}
        prev = owned[-2]
        revision = (prev.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
        if dry_run:
            return {"available": True, "dry_run": True, "deployment": deployment,
                    "would_rollback_to_revision": revision}
        tpl = prev.spec.template
        if tpl.metadata and tpl.metadata.labels:
            tpl.metadata.labels.pop("pod-template-hash", None)
        body = {"spec": {"template": client.ApiClient().sanitize_for_serialization(tpl)}}
        apps.patch_namespaced_deployment(deployment, namespace, body, _request_timeout=15)
        return {"available": True, "deployment": deployment, "rolled_back_to_revision": revision}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
