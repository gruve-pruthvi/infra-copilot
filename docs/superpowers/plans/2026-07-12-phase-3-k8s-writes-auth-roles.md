# Phase 3 — K8s Write Ops, Local Auth, Roles & Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kubernetes write operations through the existing approval gateway, local auth (password + session tokens) on API/WS, a read-only role mode, and volume/database backup tools.

**Architecture:** K8s writes are new tools on the existing k8s MCP stdio server — the gateway enforcement layer needs zero changes beyond classification rows. Auth is a FastAPI HTTP middleware + WS query-param check, opt-in (enabled only once a password is set) so the localhost-first UX is preserved. Role mode is a gateway-level deny that reuses the existing `GatewayDenied` audit path.

**Tech Stack:** kubernetes python client (dynamic client for server-side apply), hashlib.scrypt (stdlib, no new deps), FastAPI middleware, zustand.

**Prerequisite:** Phase 2 plan complete (per-tool timeouts `timeout_for`, batch approvals, git repo). Executable independently except where a task names a Phase 2 artifact.

## Global Constraints

- Same as Phase 2: pydantic-ai 2.9.0 quirks; every new tool needs a `classification.py` row + `dry_run` param + redaction + test; `test_gateway.py` stays green; tz-naive SQLite datetimes; additive-only schema (new tables OK, no column changes); backend commands from `backend/` via `uv run`.
- k8s tool conventions (from `k8s_tools/server.py`): lazy `_load()` with graceful degradation, every result carries `"available": True/False`, `_request_timeout=10` on API calls, logs capped 2000 lines / 60k chars.
- Secrets: never return secret *values* from tools — return key names only. Gateway redaction is defense-in-depth, not the primary control.
- **Deferred by design (do not implement):** promoting RCA/Cost to sub-agents. PROJECT_STATUS gates this on single-agent routing quality degrading as the tool surface grows — re-evaluate after this phase's tools land, don't build it speculatively.

---

### Task 1: K8s mutating tools — scale + rollout restart

First write tools on the k8s server: add MUTATING/DESTRUCTIVE annotation constants and the two simplest write ops. Server-side `dry_run="All"` gives real API-validated previews.

**Files:**
- Modify: `mcp-servers/k8s_tools/server.py`
- Modify: `backend/app/tools/classification.py`
- Test: `backend/tests/test_classification.py`

**Interfaces:**
- Produces: annotation constants `MUTATING`/`DESTRUCTIVE` in `k8s_tools/server.py` (mirroring docker_tools).
- Produces: MCP tools `scale_deployment(deployment, replicas, namespace="default", dry_run=False)` and `rollout_restart(deployment, namespace="default", dry_run=False)`, both returning `{"available": True, ...}` dicts.

- [ ] **Step 1: Write the failing classification tests**

Append to `backend/tests/test_classification.py`:

```python
def test_k8s_write_tools_classified():
    assert classify("k8s", "scale_deployment") is Tier.MUTATING
    assert classify("k8s", "rollout_restart") is Tier.MUTATING
```

Run: `cd backend && uv run pytest tests/test_classification.py -q` → FAIL (DESTRUCTIVE fallback).

- [ ] **Step 2: Implement the tools**

In `mcp-servers/k8s_tools/server.py`, next to the existing `READ_ONLY` constant add:

```python
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
```

Then (imports: `from datetime import datetime, timezone`):

```python
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
    except Exception as e:
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
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}
```

Add SAFETY rows:

```python
"k8s.scale_deployment": Tier.MUTATING,
"k8s.rollout_restart": Tier.MUTATING,
```

- [ ] **Step 3: Run tests**

Run: `cd backend && uv run pytest tests/test_classification.py tests/test_gateway.py -q` → PASS.
Manual (only if a working cluster is configured; the user's kubeconfig is currently stale/401 — the graceful-degradation dict is the expected result then): `cd backend && uv run python -c "from k8s_tools.server import scale_deployment; print(scale_deployment('some-deploy', 2, dry_run=True))"`.

- [ ] **Step 4: Commit**

```bash
git add mcp-servers/k8s_tools/server.py backend/app/tools/classification.py backend/tests/test_classification.py
git commit -m "feat: k8s scale + rollout_restart tools (mutating tier)"
```

---

### Task 2: K8s apply_manifest + rollback_deployment

`apply_manifest` completes the loop with the existing `write_manifest` native tool (generate → approve → apply). Uses the dynamic client with server-side apply. `rollback_deployment` re-applies the previous ReplicaSet's pod template (the python client has no `rollout undo`).

**Files:**
- Modify: `mcp-servers/k8s_tools/server.py`
- Modify: `mcp-servers/pyproject.toml` (add `pyyaml>=6.0` dependency)
- Modify: `backend/app/tools/classification.py`, `backend/app/agents/prompts.py`
- Test: `backend/tests/test_classification.py`, `backend/tests/test_k8s_manifest.py` (new)

**Interfaces:**
- Produces: `apply_manifest(manifest_yaml: str, namespace: str = "default", dry_run: bool = False)` → `{"available", "dry_run", "applied": [{kind, name, namespace}]}`.
- Produces: `rollback_deployment(deployment, namespace="default", dry_run=False)` → previous-revision info.
- Produces: pure helper `_parse_manifest(manifest_yaml) -> list[dict] | dict` (unit-testable without a cluster).

- [ ] **Step 1: SPIKE — verify dynamic-client server-side apply on the installed kubernetes package**

```bash
cd backend && uv run python -c "
from kubernetes.dynamic import DynamicClient
import inspect
print([m for m in dir(DynamicClient) if 'apply' in m.lower()])
from kubernetes.dynamic.resource import Resource
print(inspect.signature(Resource.patch))
"
```

- If `server_side_apply` exists on the client/resource → use it.
- Otherwise use `resource.patch(..., content_type="application/apply-patch+yaml")` and pass `dry_run="All"` as a kwarg; if `patch` rejects a `dry_run` kwarg, fall back to `query_params=[("dryRun", "All"), ("force", "true")]`. Record which variant worked in a code comment.

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_k8s_manifest.py
from k8s_tools.server import _parse_manifest


def test_parse_multi_doc():
    docs = _parse_manifest("""
apiVersion: v1
kind: ConfigMap
metadata:
  name: a
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: b
""")
    assert [d["kind"] for d in docs] == ["ConfigMap", "Deployment"]


def test_parse_rejects_doc_without_name():
    res = _parse_manifest("apiVersion: v1\nkind: ConfigMap\nmetadata: {}\n")
    assert res["error"] is True


def test_parse_rejects_invalid_yaml():
    res = _parse_manifest("{{{{not yaml")
    assert res["error"] is True
```

Append to `backend/tests/test_classification.py`:

```python
def test_k8s_apply_and_rollback_classified():
    assert classify("k8s", "apply_manifest") is Tier.MUTATING
    assert classify("k8s", "rollback_deployment") is Tier.MUTATING
```

Run: `cd backend && uv run pytest tests/test_k8s_manifest.py tests/test_classification.py -q` → FAIL.

- [ ] **Step 3: Implement**

Add `pyyaml>=6.0` to `mcp-servers/pyproject.toml` dependencies, then `cd backend && uv sync`.

In `k8s_tools/server.py` (imports: `import yaml`, `from kubernetes import client, dynamic`):

```python
def _parse_manifest(manifest_yaml: str) -> list[dict] | dict:
    try:
        docs = [d for d in yaml.safe_load_all(manifest_yaml) if d]
    except yaml.YAMLError as e:
        return {"error": True, "type": "InvalidYAML", "message": str(e)}
    if not docs:
        return {"error": True, "type": "EmptyManifest", "message": "no YAML documents found"}
    for d in docs:
        if not isinstance(d, dict) or not d.get("apiVersion") or not d.get("kind") \
                or not d.get("metadata", {}).get("name"):
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
        dyn = dynamic.DynamicClient(client.ApiClient())
        applied = []
        for doc in docs:
            res = dyn.resources.get(api_version=doc["apiVersion"], kind=doc["kind"])
            ns = doc.get("metadata", {}).get("namespace") or namespace
            kwargs = dict(body=doc, name=doc["metadata"]["name"],
                          content_type="application/apply-patch+yaml",
                          field_manager="infra-copilot", force_conflicts=True)
            if res.namespaced:
                kwargs["namespace"] = ns
            if dry_run:
                kwargs["dry_run"] = "All"  # adjust per Step 1 spike outcome
            res.patch(**kwargs)
            applied.append({"kind": doc["kind"], "name": doc["metadata"]["name"],
                            "namespace": ns if res.namespaced else None})
        return {"available": True, "dry_run": dry_run, "applied": applied}
    except Exception as e:
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
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}
```

Add SAFETY rows:

```python
"k8s.apply_manifest": Tier.MUTATING,
"k8s.rollback_deployment": Tier.MUTATING,
```

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_k8s_manifest.py tests/test_classification.py tests/test_gateway.py -q` → PASS.

```bash
git add mcp-servers backend/app/tools/classification.py backend/tests/
git commit -m "feat: k8s apply_manifest (server-side apply) + rollback_deployment"
```

---

### Task 3: K8s configmap/secret management + prompt updates

**Files:**
- Modify: `mcp-servers/k8s_tools/server.py`
- Modify: `backend/app/tools/classification.py`
- Modify: `backend/app/agents/prompts.py` (remove read-only framing; add placement policy)
- Test: `backend/tests/test_classification.py`

**Interfaces:**
- Produces: `set_configmap(name, data, namespace, dry_run)` MUTATING; `delete_configmap(name, namespace, dry_run)` DESTRUCTIVE; `set_secret(name, data, namespace, dry_run)` DESTRUCTIVE (returns key names only, never values); `delete_secret(name, namespace, dry_run)` DESTRUCTIVE.

- [ ] **Step 1: Failing classification tests**

```python
def test_k8s_config_and_secret_tools_classified():
    assert classify("k8s", "set_configmap") is Tier.MUTATING
    assert classify("k8s", "delete_configmap") is Tier.DESTRUCTIVE
    assert classify("k8s", "set_secret") is Tier.DESTRUCTIVE
    assert classify("k8s", "delete_secret") is Tier.DESTRUCTIVE
```

Run → FAIL, then implement.

- [ ] **Step 2: Implement the tools**

```python
@mcp.tool(annotations=MUTATING)
def set_configmap(name: str, data: dict, namespace: str = "default", dry_run: bool = False) -> dict:
    """Create or update a ConfigMap with the given string data."""
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        body = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": name, "namespace": namespace},
                "data": {str(k): str(v) for k, v in (data or {}).items()}}
        kwargs = _write_kwargs(dry_run)
        try:
            core.read_namespaced_config_map(name, namespace, _request_timeout=10)
            core.replace_namespaced_config_map(name, namespace, body, **kwargs)
            op = "updated"
        except Exception:
            core.create_namespaced_config_map(namespace, body, **kwargs)
            op = "created"
        return {"available": True, "dry_run": dry_run, "configmap": name, "op": op,
                "keys": sorted(body["data"].keys())}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_configmap(name: str, namespace: str = "default", dry_run: bool = False) -> dict:
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        if dry_run:
            core.read_namespaced_config_map(name, namespace, _request_timeout=10)
            return {"available": True, "dry_run": True, "would_delete": name}
        core.delete_namespaced_config_map(name, namespace, _request_timeout=10)
        return {"available": True, "deleted": name}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=DESTRUCTIVE)
def set_secret(name: str, data: dict, namespace: str = "default", dry_run: bool = False) -> dict:
    """Create or update an Opaque Secret. Returns key names only — never values."""
    import base64
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {"name": name, "namespace": namespace},
                "data": {str(k): base64.b64encode(str(v).encode()).decode()
                         for k, v in (data or {}).items()}}
        kwargs = _write_kwargs(dry_run)
        try:
            core.read_namespaced_secret(name, namespace, _request_timeout=10)
            core.replace_namespaced_secret(name, namespace, body, **kwargs)
            op = "updated"
        except Exception:
            core.create_namespaced_secret(namespace, body, **kwargs)
            op = "created"
        return {"available": True, "dry_run": dry_run, "secret": name, "op": op,
                "keys": sorted(body["data"].keys())}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_secret(name: str, namespace: str = "default", dry_run: bool = False) -> dict:
    apis = _load()
    if bad := _unavailable(apis):
        return bad
    core, apps = apis
    try:
        if dry_run:
            core.read_namespaced_secret(name, namespace, _request_timeout=10)
            return {"available": True, "dry_run": True, "would_delete": name}
        core.delete_namespaced_secret(name, namespace, _request_timeout=10)
        return {"available": True, "deleted": name}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}
```

Add the four SAFETY rows from Step 1's test.

- [ ] **Step 3: Prompt updates**

In `backend/app/agents/prompts.py`:

1. Rewrite the "Kubernetes" section — it currently says K8s is read-only. New text:

```
## Kubernetes
You can now CHANGE cluster state: apply_manifest (create/update from YAML), scale_deployment,
rollout_restart, rollback_deployment, set_configmap, set_secret, and delete_* variants. All writes
go through the same plan-then-approve flow as Docker. Prefer: write_manifest to draft YAML, show
the plan, then apply_manifest after approval. Always dry_run first when the user asks "what would
happen". Never put secret values in chat text — pass them only inside set_secret args.
```

2. Add a placement-policy section (vision §10):

```
## Docker vs Kubernetes placement
When the user asks to "deploy X" without specifying where:
- Default to Docker for single containers, fast iteration, and local dev dependencies (databases, caches).
- Suggest Kubernetes when the request involves multiple coordinated services, replicas/scaling,
  rollout/rollback semantics, or the user says "production-like".
- State which one you chose and why in one sentence; offer the other as an alternative.
```

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_classification.py tests/test_gateway.py -q` → PASS.

```bash
git add mcp-servers/k8s_tools/server.py backend/app/tools/classification.py backend/app/agents/prompts.py backend/tests/test_classification.py
git commit -m "feat: k8s configmap/secret tools + write-enabled prompts + placement policy"
```

---

### Task 4: Volume backup/restore + pg_dump tools

Backups land in `~/.infra-copilot/workspace/backups/` (same sandbox root as `write_manifest`). Backup/pg_dump are MUTATING (they run containers / exec but don't damage anything); restore is DESTRUCTIVE (overwrites volume contents).

**Files:**
- Modify: `mcp-servers/docker_tools/server.py`
- Modify: `backend/app/tools/classification.py` (rows + 600s TIMEOUTS entries)
- Test: `backend/tests/test_classification.py`, `backend/tests/test_backup_paths.py` (new)

**Interfaces:**
- Produces: `backup_volume(name, dry_run=False)` → `{volume, archive, path, size_bytes}`; `restore_volume(name, archive, dry_run=False)`; `pg_dump_database(container, dry_run=False)` → `{container, archive, path, size_bytes}`.
- Produces: helper `_backups_dir() -> Path` and `_safe_archive_path(archive) -> Path | dict` (sandbox check, unit-testable).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_backup_paths.py
from docker_tools.server import _backups_dir, _safe_archive_path


def test_backups_dir_is_under_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("INFRA_COPILOT_DATA_DIR", str(tmp_path))
    d = _backups_dir()
    assert d.is_dir() and str(d).startswith(str(tmp_path))


def test_safe_archive_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("INFRA_COPILOT_DATA_DIR", str(tmp_path))
    res = _safe_archive_path("../../etc/passwd")
    assert isinstance(res, dict) and res["error"] is True


def test_safe_archive_accepts_plain_name(tmp_path, monkeypatch):
    monkeypatch.setenv("INFRA_COPILOT_DATA_DIR", str(tmp_path))
    (_backups_dir() / "vol-1.tar.gz").write_bytes(b"x")
    p = _safe_archive_path("vol-1.tar.gz")
    assert p.name == "vol-1.tar.gz"
```

Append to `backend/tests/test_classification.py`:

```python
def test_backup_tools_classified():
    assert classify("docker", "backup_volume") is Tier.MUTATING
    assert classify("docker", "restore_volume") is Tier.DESTRUCTIVE
    assert classify("docker", "pg_dump_database") is Tier.MUTATING
```

Run → FAIL.

- [ ] **Step 2: Implement**

In `mcp-servers/docker_tools/server.py` (imports: `import os, time`, `from pathlib import Path`):

```python
def _backups_dir() -> Path:
    root = Path(os.environ.get("INFRA_COPILOT_DATA_DIR", str(Path.home() / ".infra-copilot")))
    d = root / "workspace" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_archive_path(archive: str) -> Path | dict:
    d = _backups_dir()
    p = (d / archive).resolve()
    if not p.is_relative_to(d.resolve()):
        return {"error": True, "type": "PathOutsideSandbox",
                "message": "archive must be a filename inside the backups directory"}
    if not p.exists():
        return {"error": True, "type": "NotFound", "message": f"no such archive: {archive}"}
    return p


@mcp.tool(annotations=MUTATING)
def backup_volume(name: str, dry_run: bool = False) -> dict:
    """Archive a volume's contents to workspace/backups/<name>-<ts>.tar.gz via a helper container."""
    try:
        cli = _docker()
        v = cli.volumes.get(name)  # raises if missing
        if dry_run:
            return {"dry_run": True, "would_backup": v.name, "dest_dir": str(_backups_dir())}
        ts = time.strftime("%Y%m%d-%H%M%S")
        fname = f"{v.name}-{ts}.tar.gz"
        dest = _backups_dir()
        cli.containers.run(
            "alpine:3.20", ["tar", "czf", f"/backup/{fname}", "-C", "/data", "."],
            remove=True,
            volumes={v.name: {"bind": "/data", "mode": "ro"},
                     str(dest): {"bind": "/backup", "mode": "rw"}},
        )
        size = (dest / fname).stat().st_size
        return {"volume": v.name, "archive": fname, "path": str(dest / fname), "size_bytes": size}
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=DESTRUCTIVE)
def restore_volume(name: str, archive: str, dry_run: bool = False) -> dict:
    """Overwrite a volume's contents from a backups/ archive. Creates the volume if missing."""
    try:
        cli = _docker()
        src = _safe_archive_path(archive)
        if isinstance(src, dict):
            return src
        if dry_run:
            return {"dry_run": True, "would_restore": name, "from_archive": src.name,
                    "size_bytes": src.stat().st_size}
        try:
            cli.volumes.get(name)
        except Exception:
            cli.volumes.create(name)
        cli.containers.run(
            "alpine:3.20", ["sh", "-c", f"rm -rf /data/* /data/..?* /data/.[!.]* ; tar xzf /backup/{src.name} -C /data"],
            remove=True,
            volumes={name: {"bind": "/data", "mode": "rw"},
                     str(_backups_dir()): {"bind": "/backup", "mode": "ro"}},
        )
        return {"restored": name, "from_archive": src.name}
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=MUTATING)
def pg_dump_database(container: str, dry_run: bool = False) -> dict:
    """pg_dump inside a running postgres container -> workspace/backups/<container>-<ts>.sql."""
    try:
        c = _docker().containers.get(container)
        if dry_run:
            return {"dry_run": True, "would_dump": c.name, "dest_dir": str(_backups_dir())}
        code, out = c.exec_run(
            ["sh", "-lc", 'pg_dump -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-postgres}"'],
            demux=False,
        )
        if code != 0:
            return {"error": True, "type": "PgDumpFailed",
                    "message": (out or b"").decode(errors="replace")[-4000:]}
        ts = time.strftime("%Y%m%d-%H%M%S")
        fname = f"{c.name}-{ts}.sql"
        path = _backups_dir() / fname
        path.write_bytes(out or b"")
        return {"container": c.name, "archive": fname, "path": str(path),
                "size_bytes": path.stat().st_size}
    except Exception as e:
        return _err(e)
```

Add SAFETY rows (`docker.backup_volume` MUTATING, `docker.restore_volume` DESTRUCTIVE, `docker.pg_dump_database` MUTATING) and TIMEOUTS entries (600.0 for all three).

Note: the dump is buffered in memory before writing — fine for local dev databases; documented limitation for huge DBs.

- [ ] **Step 3: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_backup_paths.py tests/test_classification.py tests/test_gateway.py -q` → PASS.
Manual (Docker running): create a volume with a file in it, `backup_volume`, `restore_volume` into a fresh volume, diff contents.

```bash
git add mcp-servers/docker_tools/server.py backend/app/tools/classification.py backend/tests/
git commit -m "feat: volume backup/restore + pg_dump tools (sandboxed to workspace/backups)"
```

---

### Task 5: Auth backend — password hash, tokens, endpoints, middleware

Opt-in local auth: everything stays open until a password is set (localhost-first UX preserved; PROJECT_STATUS gates LAN exposure on this). Stdlib scrypt — no new dependencies. Session tokens are opaque, stored hashed, 7-day expiry.

**Files:**
- Create: `backend/app/services/auth.py`
- Create: `backend/app/api/auth.py`
- Modify: `backend/app/db/models.py` (new `auth_tokens` table — additive), `backend/app/main.py` (middleware + router + lifespan `auth.load()`)
- Test: `backend/tests/test_auth.py` (new)

**Interfaces:**
- Produces: `services/auth.py` singleton `auth` with: `load()` (read hash from settings table into memory), `enabled() -> bool`, `set_password(new: str)`, `verify_password(pw: str) -> bool`, `create_token() -> str` (returns raw token), `validate_token(raw: str) -> bool`, `revoke_token(raw: str)`. Module functions `hash_password(pw) -> str`, `check_password(pw, stored) -> bool` (pure, unit-testable).
- Produces: table `auth_tokens` (token_hash PK, created_at, expires_at).
- Produces: `POST /api/auth/login {password} -> {token}`; `GET /api/auth/status -> {enabled}`; `POST /api/auth/set-password {current_password?, new_password}`; `POST /api/auth/logout`.
- Produces: HTTP middleware returning 401 for all paths except `PUBLIC_PATHS = {"/healthz", "/api/auth/login", "/api/auth/status"}` and `/ws/*` (WS checked in Task 6) when auth is enabled and no valid `Authorization: Bearer <token>` header is present.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_auth.py
import httpx
import pytest

from app.services.auth import auth, check_password, hash_password


def test_hash_roundtrip():
    h = hash_password("hunter2")
    assert h.startswith("scrypt$")
    assert check_password("hunter2", h)
    assert not check_password("wrong", h)
    assert not check_password("hunter2", "garbage")


async def test_login_flow(db_ready):
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        # Auth disabled -> everything open.
        r = await client.get("/api/sessions")
        assert r.status_code == 200

        await auth.set_password("pin-1234")
        assert auth.enabled()

        # Now protected.
        r = await client.get("/api/sessions")
        assert r.status_code == 401
        # Public paths still open.
        assert (await client.get("/api/auth/status")).status_code == 200
        assert (await client.get("/healthz")).status_code == 200

        # Bad login.
        r = await client.post("/api/auth/login", json={"password": "nope"})
        assert r.status_code == 401
        # Good login -> token works.
        r = await client.post("/api/auth/login", json={"password": "pin-1234"})
        token = r.json()["token"]
        r = await client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

        # Logout revokes.
        await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        r = await client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
```

(If `db_ready`/app-lifespan interaction needs `asgi_lifespan`-style startup, follow whatever pattern existing HTTP-level tests use; if none exist, call `await init_db()` and `await auth.load()` directly in the test before making requests. Reset the password to empty at test end so other tests aren't poisoned: add an `auth.set_password("")`/`disable()` teardown.)

Run: `cd backend && uv run pytest tests/test_auth.py -q` → FAIL.

- [ ] **Step 2: Implement `services/auth.py`**

```python
# backend/app/services/auth.py
"""Opt-in local auth. Password hash lives in the settings table (key
auth_password_hash) but is deliberately NOT in SettingsStore.DEFAULTS, so the
generic PUT /api/settings whitelist can never touch it — only
/api/auth/set-password can."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from ..db.database import session_factory
from ..db.models import AuthToken, Setting

_HASH_KEY = "auth_password_hash"
TOKEN_TTL = timedelta(days=7)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, salt_hex, h_hex = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(h.hex(), h_hex)
    except (ValueError, AttributeError):
        return False


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    def __init__(self) -> None:
        self._hash: str = ""

    async def load(self) -> None:
        async with session_factory() as db:
            row = await db.get(Setting, _HASH_KEY)
            self._hash = row.value if row else ""

    def enabled(self) -> bool:
        return bool(self._hash)

    def verify_password(self, pw: str) -> bool:
        return self.enabled() and check_password(pw, self._hash)

    async def set_password(self, new: str) -> None:
        self._hash = hash_password(new) if new else ""
        async with session_factory() as db:
            row = await db.get(Setting, _HASH_KEY)
            if row is None:
                db.add(Setting(key=_HASH_KEY, value=self._hash))
            else:
                row.value = self._hash
            if not self._hash:  # disabling auth revokes all tokens
                await db.execute(AuthToken.__table__.delete())
            await db.commit()

    async def create_token(self) -> str:
        raw = secrets.token_urlsafe(32)
        async with session_factory() as db:
            db.add(AuthToken(token_hash=_token_hash(raw),
                             expires_at=datetime.now(timezone.utc) + TOKEN_TTL))
            await db.commit()
        return raw

    async def validate_token(self, raw: str) -> bool:
        if not raw:
            return False
        async with session_factory() as db:
            row = await db.get(AuthToken, _token_hash(raw))
            if row is None:
                return False
            expires = row.expires_at
            if expires.tzinfo is None:  # SQLite tz-naive pattern
                expires = expires.replace(tzinfo=timezone.utc)
            return expires > datetime.now(timezone.utc)

    async def revoke_token(self, raw: str) -> None:
        async with session_factory() as db:
            row = await db.get(AuthToken, _token_hash(raw))
            if row:
                await db.delete(row)
                await db.commit()


auth = AuthService()
```

Add to `db/models.py` (match file style):

```python
class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token_hash = ...   # str primary key
    created_at = ...   # utcnow default
    expires_at = ...   # datetime, not null
```

- [ ] **Step 3: Endpoints + middleware**

```python
# backend/app/api/auth.py
import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.auth import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failures: list[float] = []  # naive in-memory login rate limit


class LoginBody(BaseModel):
    password: str


class SetPasswordBody(BaseModel):
    current_password: str | None = None
    new_password: str


@router.get("/status")
async def status():
    return {"enabled": auth.enabled()}


@router.post("/login")
async def login(body: LoginBody):
    now = time.monotonic()
    recent = [t for t in _failures if now - t < 60]
    if len(recent) >= 5:
        raise HTTPException(429, "too many failed attempts; wait a minute")
    if not auth.verify_password(body.password):
        _failures.append(now)
        await asyncio.sleep(0.5)  # slow brute force
        raise HTTPException(401, "invalid password")
    _failures.clear()
    return {"token": await auth.create_token()}


@router.post("/set-password")
async def set_password(body: SetPasswordBody):
    if auth.enabled() and not auth.verify_password(body.current_password or ""):
        raise HTTPException(401, "current password incorrect")
    await auth.set_password(body.new_password)
    return {"ok": True, "enabled": auth.enabled()}


@router.post("/logout")
async def logout(request: Request):
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    await auth.revoke_token(token)
    return {"ok": True}
```

In `main.py`:

```python
PUBLIC_PATHS = {"/healthz", "/api/auth/login", "/api/auth/status"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not auth.enabled():
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/ws"):  # WS auth handled in the endpoints
        return await call_next(request)
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if token and await auth.validate_token(token):
        return await call_next(request)
    return JSONResponse({"detail": "unauthorized"}, status_code=401)
```

Register `auth` router; add `await auth.load()` to lifespan after `store.load()`. Note: `set-password` (when auth already enabled) is itself behind the middleware — correct, changing the password requires being logged in AND knowing the current password.

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_auth.py tests/test_gateway.py -q` → PASS. Full suite: `uv run pytest -q` (verify no other HTTP test broke — auth is disabled by default so nothing should).

```bash
git add backend/app backend/tests/test_auth.py
git commit -m "feat: opt-in local auth (scrypt password, bearer tokens, middleware)"
```

---

### Task 6: Auth on WebSockets + frontend login

**Files:**
- Modify: `backend/app/api/ws.py`, `backend/app/api/logs.py` (WS endpoint)
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/sse.ts`, `frontend/src/api/ws.ts`, `frontend/src/api/types.ts`
- Create: `frontend/src/stores/authStore.ts`, `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/SettingsPage.tsx`, `frontend/src/hooks/useHealthPoll.ts`

**Interfaces:**
- Consumes: `GET /api/auth/status`, `POST /api/auth/login`, `POST /api/auth/set-password` (Task 5).
- Produces: WS endpoints close with code **4401** when auth is enabled and `?token=` is missing/invalid.
- Produces: `useAuthStore` with `{ token: string | null, authEnabled: boolean | null, setToken, logout, checkStatus() }`, token persisted to `localStorage["infra-copilot-token"]`.

- [ ] **Step 1: Backend WS checks**

At the top of the `/ws/events` handler in `ws.py` and the `/ws/logs/{id}` handler in `logs.py`, before `accept()` (or immediately after, per the existing accept pattern):

```python
if auth.enabled():
    token = websocket.query_params.get("token", "")
    if not await auth.validate_token(token):
        await websocket.close(code=4401)
        return
```

Extend `tests/test_auth.py` with a WS check if the project's test stack supports WS (httpx doesn't) — otherwise cover via the manual step below and note it.

- [ ] **Step 2: authStore + API-layer token injection**

```ts
// frontend/src/stores/authStore.ts
import { create } from "zustand";

const KEY = "infra-copilot-token";

interface AuthState {
  token: string | null;
  authEnabled: boolean | null; // null = not yet checked
  setToken: (t: string | null) => void;
  setAuthEnabled: (b: boolean) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(KEY),
  authEnabled: null,
  setToken: (t) => {
    if (t) localStorage.setItem(KEY, t);
    else localStorage.removeItem(KEY);
    set({ token: t });
  },
  setAuthEnabled: (b) => set({ authEnabled: b }),
  logout: () => {
    localStorage.removeItem(KEY);
    set({ token: null });
  },
}));

export function authHeaders(): Record<string, string> {
  const t = useAuthStore.getState().token;
  return t ? { Authorization: `Bearer ${t}` } : {};
}
```

- `client.ts`: spread `...authHeaders()` into request headers; in the non-2xx path, on `status === 401` call `useAuthStore.getState().logout()` before throwing.
- `sse.ts`: spread `...authHeaders()` into the fetch headers.
- `ws.ts`: in `wsUrl(path)`, append `?token=<token>` when present (`&token=` if the path already has a query).

- [ ] **Step 3: Login gate + settings section**

`LoginPage.tsx` — centered panel (`rounded border border-edge bg-panel p-6 w-80`), mono uppercase title "infra-copilot", one password input, submit → `api.post<{token}>("/api/auth/login", {password})` → `setToken`, inline error line on failure (reuse `errorMessage`).

`App.tsx` — on mount, `api.get<{enabled: boolean}>("/api/auth/status")` → `setAuthEnabled`. Render logic before the normal layout:

```tsx
const { authEnabled, token } = useAuthStore();
if (authEnabled === null) return null;              // brief blank while checking
if (authEnabled && !token) return <LoginPage />;    // gate everything
// else normal <BrowserRouter> layout
```

Key the WS bootstrap effect on `token` so the events socket reconnects with the token after login.

`SettingsPage.tsx` — new "Security" section: when auth disabled, one "Set password" field; when enabled, "current password" + "new password" fields and a "Disable auth" path (empty new password) → `POST /api/auth/set-password`. Follow the existing section/save-message idioms.

`useHealthPoll.ts` — no change needed (`/healthz` is public); 401s from other calls already trigger logout via `client.ts`.

- [ ] **Step 4: Verify + commit**

Run: `cd frontend && npm run build` → 0. `cd backend && uv run pytest -q` → PASS.
Manual: `make dev` → Settings → set password → app reloads to login gate on next visit; wrong password shows error; after login, dashboard WS connects (check the live dot); `wscat`/browser devtools confirm `/ws/events` without token closes with 4401; logout via clearing localStorage returns to gate.

```bash
git add frontend/src backend/app/api backend/tests/test_auth.py
git commit -m "feat: login gate, token injection (REST/SSE/WS), settings security section"
```

---

### Task 7: Role boundaries — read-only mode

Global operator/read-only switch (single-user MVP semantics; per-user roles arrive only if multi-user auth ever lands). Read-only denies every non-READ tool **in the gateway**, before dry-run/approval logic, using the existing `GatewayDenied` audit path — so denials are audited like any other.

**Files:**
- Modify: `backend/app/services/settings_store.py` (DEFAULTS), `backend/app/api/settings.py` (validation), `backend/app/tools/gateway.py`
- Modify: `frontend/src/pages/SettingsPage.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/api/types.ts`
- Test: `backend/tests/test_gateway.py`

**Interfaces:**
- Produces: setting `role_mode: "operator" | "read_only"` (default `"operator"`) in `SettingsStore.DEFAULTS`.
- Produces: gateway behavior — non-READ call in read-only mode → audit row `status="denied"`, result `{"error": True, "denied": True, ...}`, executor **never called**.

- [ ] **Step 1: Write the failing gateway test**

Append to `backend/tests/test_gateway.py`, reusing its `FakeCtx` / `make_executor` / `last_action` helpers:

```python
async def test_read_only_mode_denies_mutating_even_with_approval(db_ready, session_id, monkeypatch):
    monkeypatch.setitem(store._cache, "role_mode", "read_only")
    approval_id = await add_approval(session_id, "docker", "stop_container", {"container": "x"})
    ctx = FakeCtx(session_id=session_id, tool_call_id=..., tool_call_approved=True)  # match helper shape
    executor, calls = make_executor()
    result = await gateway.execute(ctx, "docker", "stop_container", {"container": "x"}, executor)
    assert result["denied"] is True
    assert calls == []  # executor never ran
    action = await last_action()
    assert action.status == "denied"


async def test_read_only_mode_allows_read(db_ready, session_id, monkeypatch):
    monkeypatch.setitem(store._cache, "role_mode", "read_only")
    ctx = FakeCtx(session_id=session_id, tool_call_id=..., tool_call_approved=False)
    executor, calls = make_executor()
    await gateway.execute(ctx, "docker", "list_containers", {}, executor)
    assert len(calls) == 1
```

(Adopt the exact helper call shapes already in the file — the assertions are the contract.)

Run: `cd backend && uv run pytest tests/test_gateway.py -q -k read_only` → FAIL.

- [ ] **Step 2: Implement**

`settings_store.py` — add `"role_mode": "operator"` to `DEFAULTS`.

`settings.py` PUT validation — alongside the existing `dry_run_mode` check:

```python
if "role_mode" in values and values["role_mode"] not in ("operator", "read_only"):
    raise HTTPException(400, "role_mode must be 'operator' or 'read_only'")
```

`gateway.py` — at the top of the non-READ branch in `execute()`, **before** the dry-run check, raise through the existing denied path:

```python
if store.get("role_mode") == "read_only":
    raise GatewayDenied("read-only mode is enabled; mutating and destructive actions are disabled")
```

Ensure this raise is inside the same `try` whose `except GatewayDenied` writes the denied audit row and returns the `{"error": True, "denied": True, ...}` dict (widen that try's scope if it currently only wraps `_consume_approval`).

- [ ] **Step 3: Frontend toggle + indicator**

- `types.ts`: add `role_mode: "operator" | "read_only"` to `SettingsValues`.
- `SettingsPage.tsx`: radio pair "Operator / Read-only" in a "Safety" group next to the dry-run checkbox, saved through the existing PUT flow.
- `Sidebar.tsx`: when settings report read-only (fetch once alongside health or pipe through `/healthz` — simplest: add `"role_mode": store.get("role_mode")` to the `/healthz` payload in `main.py` and `HealthView`), show a `warn`-colored `READ-ONLY` pill above the health rows.

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_gateway.py -q` → PASS (all pre-existing tests too — they run with `role_mode=operator` default). `cd frontend && npm run build` → 0.

```bash
git add backend/app frontend/src backend/tests/test_gateway.py
git commit -m "feat: read-only role mode enforced in gateway"
```

---

### Task 8: Close out Phase 3

**Files:**
- Modify: `PROJECT_STATUS.md`, `README.md` (safety model + auth mention), `CLAUDE.md` (if facts changed)

- [ ] **Step 1: Full verification**

```bash
cd backend && uv run pytest -q     # all green (Docker Desktop running)
cd frontend && npm run build       # exits 0
```

Manual smoke (`make dev`):
1. K8s (needs a working cluster; with the stale kubeconfig, verify graceful-degradation messages instead): "scale deployment X to 3 replicas" → approval card → approve → verify via `list_deployments`.
2. "Create a configmap app-config with LOG_LEVEL=debug" → approve → applied.
3. Backups: "back up seed-orphan-volume" → approve → archive listed under `~/.infra-copilot/workspace/backups/`.
4. Auth: set password, reload → login gate; wrong PIN rejected; WS reconnects after login.
5. Read-only mode: toggle on, ask "stop seed-nginx" → gateway denies, History shows the denied audit row, sidebar shows READ-ONLY pill.

- [ ] **Step 2: Update docs**

- `PROJECT_STATUS.md`: add "Phase 3 (✅ complete)" section (same table format); explicitly record the deferred decision: *"RCA/Cost sub-agent promotion re-evaluated after Phase 3 tool-surface growth — not needed yet / needed because X"*. Move remaining vision items (MQTT, self-healing, multi-env, packaging, OTel) under Phase 4.
- `README.md`: extend the safety-model section with read-only mode and auth; note K8s is no longer read-only.

- [ ] **Step 3: Commit**

```bash
git add PROJECT_STATUS.md README.md CLAUDE.md
git commit -m "docs: mark Phase 3 complete; record sub-agent deferral decision"
```
