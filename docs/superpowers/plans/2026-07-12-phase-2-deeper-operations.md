# Phase 2 — Deeper Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the copilot genuinely useful for day-to-day local dev operations: Compose support, container exec, DB provisioning, batch/auto-resolving approvals, history trimming, scheduled cost scans, restart-loop alerts, and per-tool timeouts + progress.

**Architecture:** All new agent capabilities are MCP tools on the existing stdio servers, gated by the existing `ToolGateway` (classification → approval → audit). Background intelligence (cost scans, restart-loop detection) lives in backend services next to `monitor`, publishing over the in-process `EventBus` → `/ws/events`. Approval UX changes ride the existing `RunManager` pause/resume machinery.

**Tech Stack:** FastAPI, pydantic-ai 2.9.0, MCP FastMCP (official `mcp` package), docker-py, SQLAlchemy 2 (aiosqlite), React 18 + zustand + Tailwind v4.

## Global Constraints

- pydantic-ai is **2.9.0**; its API differs from older docs: `MCPToolset(StdioTransport(...), process_tool_call=...)`, `FunctionToolResultEvent.part`, `Agent.run(..., deferred_tool_results=, event_stream_handler=)`. Verify against the installed package before editing agent plumbing.
- **Every new tool** gets: a row in `backend/app/tools/classification.py` (unknown tools fail closed as DESTRUCTIVE), a `dry_run: bool = False` last param if mutating, secret redaction on outputs, and a gateway-path/classification test.
- `tests/test_gateway.py` is the security contract — it must stay green; extend it for new approval/audit behavior.
- Run all backend commands from `backend/`: `uv run pytest -q`, `uv run python ...`. One venv serves both Python packages (`mcp-servers/` is an editable path dep).
- SQLite returns **tz-naive datetimes** — re-attach `tzinfo=timezone.utc` before comparing (existing pattern in `run_manager.py` / `gateway.py`).
- No migrations framework: schema changes must be **additive new tables only** (`init_db()` runs `create_all`).
- Docker tool output conventions: return plain dicts, errors as `{"error": True, "type": ..., "message": ...}` via `_err(e)`, logs capped at `tail<=2000` lines / last 60,000 chars.
- Tests that spawn MCP servers or touch Docker need Docker Desktop running (`tests/test_agent_loop.py` pattern). `test_gateway.py`-style tests must not.
- Provider quirks live only in `backend/app/llm/models.py`.

---

### Task 0: Initialize git repo

The repo has never been under version control (PROJECT_STATUS known gap). Every later task ends in a commit, so this comes first.

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
node_modules/
frontend/dist/
.DS_Store
*.log
.superpowers/
```

- [ ] **Step 2: Init and commit**

```bash
cd /Users/Pruthvi.Parade@gruve.ai/Desktop/Experiments/infra-copilot
git init
git add -A
git commit -m "chore: initial commit — Phase 1 MVP"
```

- [ ] **Step 3: Verify baseline is green**

Run: `cd backend && uv run pytest tests/test_gateway.py -q` → all pass.
Run: `cd frontend && npm run build` → exits 0.

---

### Task 1: Per-tool timeouts

Replace the single global 180s `asyncio.wait_for` in the gateway with a per-tool lookup. Long tools added later (compose_up, pull_image, backups) depend on this.

**Files:**
- Modify: `backend/app/tools/classification.py`
- Modify: `backend/app/tools/gateway.py` (the `asyncio.wait_for(executor(...), timeout=settings.tool_timeout_seconds)` call)
- Test: `backend/tests/test_classification.py` (new)

**Interfaces:**
- Produces: `timeout_for(server: str, tool: str) -> float` in `classification.py`, used by the gateway and available to later tasks registering long-running tools in `TIMEOUTS`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_classification.py
from app.config import settings
from app.tools.classification import Tier, classify, timeout_for


def test_unknown_tool_fails_closed():
    assert classify("docker", "definitely_not_a_tool") is Tier.DESTRUCTIVE


def test_timeout_default_matches_settings():
    assert timeout_for("docker", "list_containers") == settings.tool_timeout_seconds


def test_timeout_override_for_pull_image():
    assert timeout_for("docker", "pull_image") == 600.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_classification.py -q`
Expected: FAIL — `ImportError: cannot import name 'timeout_for'`.

- [ ] **Step 3: Implement**

In `backend/app/tools/classification.py`, add (import `settings` from `..config`):

```python
# Per-tool timeout overrides in seconds. Anything absent uses the global default.
TIMEOUTS: dict[str, float] = {
    "docker.pull_image": 600.0,
}


def timeout_for(server: str, tool: str) -> float:
    return TIMEOUTS.get(f"{server}.{tool}", settings.tool_timeout_seconds)
```

In `backend/app/tools/gateway.py`, import `timeout_for` and change the execution line:

```python
result = await asyncio.wait_for(executor(effective_args), timeout=timeout_for(server, tool))
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_classification.py tests/test_gateway.py -q`
Expected: PASS (gateway suite unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/classification.py backend/app/tools/gateway.py backend/tests/test_classification.py
git commit -m "feat: per-tool timeout overrides in gateway"
```

---

### Task 2: Docker Compose tools

Four new tools on the docker MCP server, shelling out to the `docker compose` CLI (docker-py has no compose API). Pairs with the existing `write_manifest` tool (agent writes a compose file into `~/.infra-copilot/workspace`, then runs it).

**Files:**
- Modify: `mcp-servers/docker_tools/server.py`
- Modify: `backend/app/tools/classification.py` (SAFETY rows + TIMEOUTS)
- Modify: `backend/app/agents/prompts.py` (mention compose workflow)
- Test: `backend/tests/test_classification.py`, `backend/tests/test_compose_tools.py` (new)

**Interfaces:**
- Produces: MCP tools `compose_ps(project_dir, file=None)`, `compose_logs(project_dir, service=None, tail=200, file=None)`, `compose_up(project_dir, file=None, dry_run=False)`, `compose_down(project_dir, file=None, remove_volumes=False, dry_run=False)`. All return dicts per the docker-tools conventions.
- Produces: module helpers `_compose_base(project_dir, file) -> list[str] | dict` and `_run_compose(cmd, timeout=570.0) -> dict` (unit-testable without Docker).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_compose_tools.py
"""Unit tests for compose helpers — no Docker daemon needed."""
from docker_tools.server import _compose_base, _run_compose


def test_compose_base_builds_command(tmp_path):
    cmd = _compose_base(str(tmp_path), None)
    assert cmd == ["docker", "compose", "--project-directory", str(tmp_path)]


def test_compose_base_with_file(tmp_path):
    f = tmp_path / "compose.yaml"
    f.write_text("services: {}\n")
    cmd = _compose_base(str(tmp_path), str(f))
    assert cmd[-2:] == ["-f", str(f)]


def test_compose_base_rejects_missing_dir():
    res = _compose_base("/nonexistent/path/xyz", None)
    assert res["error"] is True


def test_run_compose_missing_binary(monkeypatch):
    res = _run_compose(["definitely-not-a-binary-xyz", "compose"])
    assert res["error"] is True
```

And append to `backend/tests/test_classification.py`:

```python
def test_compose_tools_classified():
    assert classify("docker", "compose_ps") is Tier.READ
    assert classify("docker", "compose_logs") is Tier.READ
    assert classify("docker", "compose_up") is Tier.MUTATING
    assert classify("docker", "compose_down") is Tier.DESTRUCTIVE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_compose_tools.py tests/test_classification.py -q`
Expected: FAIL — import errors / DESTRUCTIVE fallbacks.

- [ ] **Step 3: Implement the tools**

Append to `mcp-servers/docker_tools/server.py` (imports `subprocess`, `json`, `from pathlib import Path` at top):

```python
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
```

Note: `compose_ps` on some compose versions returns one JSON array instead of JSON-lines; if `json.loads(line)` fails during manual verification, parse `res["output"]` with a single `json.loads` fallback.

- [ ] **Step 4: Register classification + timeouts**

In `backend/app/tools/classification.py` SAFETY table add:

```python
"docker.compose_ps": Tier.READ,
"docker.compose_logs": Tier.READ,
"docker.compose_up": Tier.MUTATING,
"docker.compose_down": Tier.DESTRUCTIVE,
```

In `TIMEOUTS` add `"docker.compose_up": 600.0, "docker.compose_down": 300.0`.

In `backend/app/agents/prompts.py`, in the "How to work" section, add one line:

```
- To run multi-service stacks: generate a compose file with write_manifest into the workspace, then use compose_up with project_dir set to the workspace directory. Use compose_ps / compose_logs to verify.
```

- [ ] **Step 5: Run tests, then verify against Docker (manual)**

Run: `cd backend && uv run pytest tests/test_compose_tools.py tests/test_classification.py tests/test_gateway.py -q` → PASS.
Manual (Docker Desktop running): `cd backend && uv run python -c "from docker_tools.server import compose_up; import tempfile, pathlib; d=tempfile.mkdtemp(); pathlib.Path(d,'compose.yaml').write_text('services:\n  web:\n    image: nginx:alpine\n'); print(compose_up(d, dry_run=True))"` → prints `dry_run: True` with resolved config.

- [ ] **Step 6: Commit**

```bash
git add mcp-servers/docker_tools/server.py backend/app/tools/classification.py backend/app/agents/prompts.py backend/tests/
git commit -m "feat: docker compose tools (up/down/ps/logs) behind gateway"
```

---

### Task 3: `exec_in_container` tool

Mutating-tier diagnostic exec ("check if the config file exists inside the container"). Output capped, fully audited by the gateway like every tool.

**Files:**
- Modify: `mcp-servers/docker_tools/server.py`
- Modify: `backend/app/tools/classification.py`
- Test: `backend/tests/test_classification.py`

**Interfaces:**
- Produces: MCP tool `exec_in_container(container: str, command: str | list[str], workdir: str | None = None, dry_run: bool = False) -> dict` returning `{container, exit_code, output}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_classification.py`:

```python
def test_exec_in_container_is_mutating():
    assert classify("docker", "exec_in_container") is Tier.MUTATING
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_classification.py -q` → FAIL (classified DESTRUCTIVE by fallback).

- [ ] **Step 3: Implement**

Append to `mcp-servers/docker_tools/server.py`:

```python
@mcp.tool(annotations=MUTATING)
def exec_in_container(container: str, command: str | list[str], workdir: str | None = None, dry_run: bool = False) -> dict:
    """Run a command inside a running container and return exit code + output (capped)."""
    try:
        c = _docker().containers.get(container)
        if dry_run:
            return {"dry_run": True, "would": "exec", "container": c.name, "command": command, "workdir": workdir}
        exit_code, output = c.exec_run(command, workdir=workdir, demux=False)
        text = (output or b"").decode(errors="replace")[-60_000:]
        return {"container": c.name, "exit_code": exit_code, "output": text}
    except Exception as e:
        return _err(e)
```

Add to SAFETY: `"docker.exec_in_container": Tier.MUTATING,`

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_classification.py tests/test_gateway.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp-servers/docker_tools/server.py backend/app/tools/classification.py backend/tests/test_classification.py
git commit -m "feat: exec_in_container diagnostic tool (mutating tier)"
```

---

### Task 4: `check_connectivity` tool + database-provisioning prompt flow

Guided "Add PostgreSQL / Redis" flows are mostly prompt work; the missing primitive is a TCP connectivity check against a published port.

**Files:**
- Modify: `mcp-servers/monitoring_tools/server.py`
- Modify: `backend/app/tools/classification.py`
- Modify: `backend/app/agents/prompts.py`
- Test: `backend/tests/test_connectivity.py` (new), `backend/tests/test_classification.py`

**Interfaces:**
- Produces: MCP tool `check_connectivity(host: str = "127.0.0.1", port: int = 5432, timeout_seconds: float = 5.0) -> dict` returning `{reachable: bool, latency_ms?|reason?}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_connectivity.py
import socket
import threading

from monitoring_tools.server import check_connectivity


def test_reachable_port():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(target=lambda: srv.accept(), daemon=True)
    t.start()
    res = check_connectivity(host="127.0.0.1", port=port, timeout_seconds=2.0)
    srv.close()
    assert res["reachable"] is True
    assert res["latency_ms"] >= 0


def test_unreachable_port():
    res = check_connectivity(host="127.0.0.1", port=1, timeout_seconds=0.5)
    assert res["reachable"] is False
    assert "reason" in res
```

Append to `backend/tests/test_classification.py`:

```python
def test_check_connectivity_is_read():
    assert classify("monitoring", "check_connectivity") is Tier.READ
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_connectivity.py tests/test_classification.py -q` → FAIL (import error).

- [ ] **Step 3: Implement**

Append to `mcp-servers/monitoring_tools/server.py` (import `socket`, `time` at top):

```python
@mcp.tool(annotations=READ_ONLY)
def check_connectivity(host: str = "127.0.0.1", port: int = 5432, timeout_seconds: float = 5.0) -> dict:
    """TCP connectivity check — use after deploying a service to verify its published port answers."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return {"reachable": True, "host": host, "port": int(port),
                    "latency_ms": round((time.monotonic() - start) * 1000, 1)}
    except OSError as e:
        return {"reachable": False, "host": host, "port": int(port),
                "reason": f"{type(e).__name__}: {e}"}
```

Add to SAFETY: `"monitoring.check_connectivity": Tier.READ,`

- [ ] **Step 4: Add the provisioning prompt section**

In `backend/app/agents/prompts.py`, add a new section to `SYSTEM_PROMPT` after the Cost section:

```
## Database provisioning mode
When asked to "add/provision a database" (PostgreSQL, Redis, MySQL, ...):
1. Create a named volume for data (create_volume) so data survives container recreation.
2. Run the official image with the volume mounted at the engine's data path, a published host port, and required env vars. Generate credentials yourself (long random password); NEVER echo the password back in chat — refer to it as "the generated password" and include it only in the run_container env args (the approval card shows it to the user once).
3. After start, verify with check_connectivity against the published host port.
4. Report a connection string TEMPLATE with the password elided, e.g. postgresql://app:<password-you-approved>@localhost:5432/app.
```

- [ ] **Step 5: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_connectivity.py tests/test_classification.py -q` → PASS.

```bash
git add mcp-servers/monitoring_tools/server.py backend/app/tools/classification.py backend/app/agents/prompts.py backend/tests/
git commit -m "feat: check_connectivity tool + database provisioning prompt flow"
```

---

### Task 5: Batch approval endpoint

Backend half of "Approval UX round 2". `RunManager.resolve_approval` already defers resume until the last sibling is resolved, so a batch endpoint is just a loop — resume fires exactly once.

**Files:**
- Modify: `backend/app/api/approvals.py`
- Test: `backend/tests/e2e/test_approval_flow.py`

**Interfaces:**
- Consumes: `run_manager.resolve_approval(approval_id, approved: bool, confirm_text=None, reason=None)` — **match the exact parameter names already used by the single approve/reject handlers in `api/approvals.py`**.
- Produces: `POST /api/approvals/batch` accepting `{approve: [ids], reject: [ids], confirm_texts: {id: text}, reason?: str}` → `{resumed: bool, results: {id: {ok, ...}}}`.

- [ ] **Step 1: Write the failing e2e test**

Append to `backend/tests/e2e/test_approval_flow.py`, copying the existing fixture pattern (`_fake_build_agent` with `TestModel`, `monkeypatch.setattr(rm, "build_agent", ...)`, broadcaster drain helpers). Use TWO mutating tools so one turn produces two pending approvals:

```python
async def test_batch_approve_resumes_once(session_id, monkeypatch):
    # Same _fake_build_agent pattern as the existing test, but:
    #   TestModel(call_tools=["stop_container", "restart_container"])
    _install_fake_agent(monkeypatch, call_tools=["stop_container", "restart_container"])

    q = rm.run_manager.subscribe(session_id)
    await rm.run_manager.start_turn(session_id, "stop and restart seed-nginx")
    events = await _drain_until(q, "turn_paused")
    approval_ids = [e["approval"]["id"] for e in events if e["type"] == "approval_required"]
    assert len(approval_ids) == 2

    # Resolve both through one batch call semantics: first leaves run paused, second resumes.
    r1 = await rm.run_manager.resolve_approval(approval_ids[0], approved=True)
    assert r1["resumed"] is False
    r2 = await rm.run_manager.resolve_approval(approval_ids[1], approved=False, reason="batch reject")
    assert r2["resumed"] is True
    await _drain_until(q, "done")
```

(If the existing file's helpers have different names, reuse those — do not duplicate drain logic.)

Then add an HTTP-level test using `httpx.ASGITransport` against `app.main.app` if the file already has one; otherwise the run-manager-level test above plus the endpoint unit below is sufficient.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/e2e -q -k batch`
Expected: FAIL (helper `_install_fake_agent` may need extracting from the existing test first — do that refactor as part of this step, keeping the existing test green).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/api/approvals.py`:

```python
class BatchDecisions(BaseModel):
    approve: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)
    confirm_texts: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


@router.post("/batch")
async def resolve_batch(body: BatchDecisions):
    results: dict[str, dict] = {}
    resumed = False
    for aid in body.reject:
        try:
            r = await run_manager.resolve_approval(aid, approved=False, reason=body.reason or "batch reject")
            resumed = resumed or bool(r.get("resumed"))
            results[aid] = {"ok": True, **r}
        except KeyError:
            results[aid] = {"ok": False, "error": "not found"}
        except ValueError as e:
            results[aid] = {"ok": False, "error": str(e)}
    for aid in body.approve:
        try:
            r = await run_manager.resolve_approval(aid, approved=True, confirm_text=body.confirm_texts.get(aid))
            resumed = resumed or bool(r.get("resumed"))
            results[aid] = {"ok": True, **r}
        except KeyError:
            results[aid] = {"ok": False, "error": "not found"}
        except ValueError as e:
            results[aid] = {"ok": False, "error": str(e)}
    return {"resumed": resumed, "results": results}
```

(Adjust `resolve_approval` kwargs to the exact existing signature.)

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/e2e tests/test_gateway.py -q` → PASS (needs Docker running for e2e).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/approvals.py backend/tests/e2e/test_approval_flow.py
git commit -m "feat: batch approval resolve endpoint"
```

---

### Task 6: Auto-reject stale approvals on new message

Today a new chat while `awaiting_approval` 409s. New behavior: sending a message auto-rejects all pending approvals for that session (reason "superseded by a new user message") and starts the new turn, feeding the denials back to the model.

**Files:**
- Modify: `backend/app/agents/run_manager.py` (`start_turn`, plus a new `_reject_pending` helper)
- Test: `backend/tests/e2e/test_approval_flow.py`

**Interfaces:**
- Consumes: `DeferredToolResults`, `ToolDenied` from `pydantic_ai` (already imported in run_manager).
- Produces: `start_turn` no longer raises on `awaiting_approval`; approvals end in status `rejected` with `reject_reason="superseded by a new user message"`.

- [ ] **Step 1: SPIKE — verify pydantic-ai 2.9 accepts `user_prompt` together with `deferred_tool_results`**

Run:

```bash
cd backend && uv run python -c "
import inspect
from pydantic_ai import Agent
print(inspect.signature(Agent.run))
"
```

Then check the source for a guard:

```bash
cd backend && uv run python -c "
import inspect, pydantic_ai.agent as a
src = inspect.getsource(a)
import re
print([l for l in src.splitlines() if 'deferred_tool_results' in l and ('raise' in l or 'user_prompt' in l)])
"
```

- If nothing forbids combining them → **primary implementation** (Step 4).
- If combining raises → use the **fallback** in Step 4b.
The e2e test in Step 2 is the real arbiter.

- [ ] **Step 2: Write the failing e2e test**

Append to `backend/tests/e2e/test_approval_flow.py`:

```python
async def test_new_message_auto_rejects_pending(session_id, monkeypatch):
    _install_fake_agent(monkeypatch, call_tools=["stop_container"])
    q = rm.run_manager.subscribe(session_id)
    await rm.run_manager.start_turn(session_id, "stop seed-nginx")
    events = await _drain_until(q, "turn_paused")
    approval_id = next(e["approval"]["id"] for e in events if e["type"] == "approval_required")

    # New message instead of resolving the approval — must NOT raise.
    await rm.run_manager.start_turn(session_id, "actually, never mind — just list containers")
    await _drain_until(q, "done")

    async with session_factory() as db:
        ap = await db.get(Approval, approval_id)
        assert ap.status == "rejected"
        assert "superseded" in (ap.reject_reason or "")

    async with session_factory() as db:
        sess = await db.get(ChatSession, session_id)
        assert sess.status == "idle"  # TestModel's follow-up turn calls no mutating tool, so it completes
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/e2e -q -k auto_rejects`
Expected: FAIL — `RuntimeError` from the `awaiting_approval` guard in `start_turn`.

- [ ] **Step 4: Implement (primary)**

In `run_manager.py`, add:

```python
async def _reject_pending(self, session_id: str) -> DeferredToolResults | None:
    """Reject every pending approval for the session; return denials to feed the paused run."""
    async with session_factory() as db:
        sess = await db.get(ChatSession, session_id)
        call_ids = json.loads(sess.pending_call_ids or "[]")
        if not call_ids:
            return None
        results = DeferredToolResults()
        now = datetime.now(timezone.utc)
        for ap in await self._approvals_for(db, session_id, call_ids):
            if ap.status == "pending":
                ap.status = "rejected"
                ap.resolved_at = now
                ap.reject_reason = "superseded by a new user message"
            denied = ap.status != "approved"
            results.approvals[ap.tool_call_id] = (
                ToolDenied(message="The user sent a new message instead of approving; treat this action as declined.")
                if denied else ToolApproved()
            )
            await bus.publish("approval_resolved", {"id": ap.id, "session_id": session_id, "status": ap.status})
        sess.pending_call_ids = None
        await db.commit()
        return results
```

(`_approvals_for` = however the existing `resolve_approval` loads sibling approvals — reuse that query, don't duplicate it.)

In `start_turn`, replace the `awaiting_approval` RuntimeError guard with:

```python
deferred = None
if sess.status == "awaiting_approval":
    deferred = await self._reject_pending(session_id)
```

and pass `deferred_results=deferred` through `_launch` → `_run` → `agent.run(user_prompt, ..., deferred_tool_results=deferred)`. (`_run` already accepts `deferred_results` for the resume path — this reuses it with a user prompt too.)

- [ ] **Step 4b: Fallback (only if the spike/e2e shows combining is rejected)**

Run two sequential `agent.run` calls inside the same `_run` invocation: first `agent.run(None, message_history=history, deferred_tool_results=deferred, ...)` to close the paused turn (stream its events normally), then feed `result.all_messages()` as history into a second `agent.run(user_prompt, ...)`. Save history from the second result. Keep it inside one task so `is_running` semantics don't change.

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/e2e tests/test_gateway.py -q` → PASS.
Also update `backend/app/api/chat.py` docstring/comment: 409 for `awaiting_approval` no longer applies (only the concurrent-run 409 remains).

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/run_manager.py backend/app/api/chat.py backend/tests/e2e/test_approval_flow.py
git commit -m "feat: new message auto-rejects stale pending approvals and continues"
```

---

### Task 7: Approval UX round 2 — frontend

Make approvals actionable from any page (toast queue with approve/reject), add batch buttons when multiple approvals are pending, and drop the client-side notion that chat is blocked.

**Files:**
- Modify: `frontend/src/stores/liveStore.ts` (toast queue), `frontend/src/stores/chatStore.ts` (batch actions)
- Modify: `frontend/src/components/ApprovalToast.tsx` (embed actions)
- Modify: `frontend/src/pages/ChatPage.tsx` (batch bar)
- Modify: `frontend/src/api/client.ts` — no change needed; batch uses `api.post`

**Interfaces:**
- Consumes: `POST /api/approvals/batch` from Task 5; existing `PlanApprovalCard` props (`approval`, `onApprove(confirmText?)`, `onReject()`).
- Produces: `liveStore.toastQueue: PendingApproval[]` (replaces single `toastApproval`); `chatStore.batchResolve(approve: string[], reject: string[], confirmTexts: Record<string,string>): Promise<string | null>`.

- [ ] **Step 1: Generalize the toast slot into a queue**

In `liveStore.ts`: replace `toastApproval: PendingApproval | null` with `toastQueue: PendingApproval[]`. `approval_pending` → append (dedup by id); `approval_resolved` → filter out; `dismissToast(id)` → filter out. Keep `pendingApprovals` as-is.

- [ ] **Step 2: Make the toast actionable**

In `ApprovalToast.tsx`: render the first item of `toastQueue` (with a "+N more" count chip). Replace the "Review in chat"-only body with an embedded `PlanApprovalCard` (it already takes `onApprove`/`onReject` returning error strings) wired to `useChatStore.getState().approve/reject`. Keep the "Review in chat" link (select session + navigate) and the dismiss `×`. Note: `PlanApprovalCard` already enforces the typed-resource-name gate for destructive tier, so the toast inherits it for free.

- [ ] **Step 3: Add `batchResolve` to chatStore**

```ts
batchResolve: async (approve: string[], reject: string[], confirmTexts: Record<string, string> = {}) => {
  try {
    const res = await api.post<{ resumed: boolean; results: Record<string, { ok: boolean; error?: string }> }>(
      "/api/approvals/batch",
      { approve, reject, confirm_texts: confirmTexts },
    );
    for (const id of approve) if (res.results[id]?.ok) get().onApprovalResolved(id, "approved");
    for (const id of reject) if (res.results[id]?.ok) get().onApprovalResolved(id, "rejected");
    if (res.resumed) get().attachStream(get().currentId!);
    return null;
  } catch (e) {
    return errorMessage(e);
  }
},
```

- [ ] **Step 4: Batch bar in ChatPage**

In `ChatPage.tsx`, above the Composer, when ≥2 unresolved `approval` items are in the transcript, render a bar: "N approvals pending — [Approve all non-destructive] [Reject all]". Approve-all sends only non-destructive ids via `batchResolve(ids, [])`; destructive ones still require their individual typed confirmation cards. Reject-all sends every pending id. Style: `rounded border border-edge bg-panel px-3 py-2 text-xs` with the existing button idiom (`border-ok/50 bg-ok/10 text-ok` / `border-danger/50 ... text-danger`).

- [ ] **Step 5: Verify + commit**

Run: `cd frontend && npm run build` → exits 0 (strict tsc catches store type drift).
Manual: `make dev`, `make seed`, ask "stop seed-nginx and seed-idle", confirm two approval cards + batch bar; navigate to Dashboard and confirm the toast is actionable there.

```bash
git add frontend/src
git commit -m "feat: actionable approval toasts + batch approve/reject UI"
```

---

### Task 8: History trimming

`ChatSession.agent_history` grows unbounded. pydantic-ai 2.9.0 does **not** expose `history_processors` on `Agent.__init__` — the seam is `RunManager._load_history`, where the full message list is already materialized.

**Files:**
- Create: `backend/app/agents/history.py`
- Modify: `backend/app/agents/run_manager.py` (`_load_history`), `backend/app/config.py` (new setting)
- Test: `backend/tests/test_history.py` (new)

**Interfaces:**
- Produces: `trim_history(messages: list[ModelMessage], char_budget: int) -> list[ModelMessage]` — drops oldest whole turns (never splits a tool-call/return pair) until the serialized size fits the budget.
- Produces: `settings.history_char_budget: int = 400_000` (~100k tokens).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_history.py
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agents.history import trim_history


def _turn(i: int, pad: int = 0):
    return [
        ModelRequest(parts=[UserPromptPart(content=f"question {i} " + "x" * pad)]),
        ModelResponse(parts=[TextPart(content=f"answer {i}")]),
    ]


def test_under_budget_untouched():
    msgs = _turn(1) + _turn(2)
    assert trim_history(msgs, 1_000_000) == msgs


def test_drops_oldest_whole_turns():
    msgs = _turn(1, pad=5000) + _turn(2, pad=5000) + _turn(3, pad=5000)
    out = trim_history(msgs, 12_000)
    # Oldest turn(s) dropped; result still starts at a user-prompt boundary.
    assert isinstance(out[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in out[0].parts)
    assert len(out) < len(msgs)


def test_always_keeps_last_turn_even_over_budget():
    msgs = _turn(1, pad=50_000)
    out = trim_history(msgs, 10)
    assert len(out) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_history.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# backend/app/agents/history.py
"""Trim agent history to a character budget at whole-turn boundaries.

pydantic-ai 2.9 does not expose history_processors on Agent, so trimming
happens where RunManager materializes message_history. Cutting only at
ModelRequest-with-UserPromptPart boundaries guarantees tool-call parts are
never separated from their returns.
"""
from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart


def _turn_starts(messages: list[ModelMessage]) -> list[int]:
    return [
        i for i, m in enumerate(messages)
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]


def trim_history(messages: list[ModelMessage], char_budget: int) -> list[ModelMessage]:
    if not messages:
        return messages
    sizes = [len(ModelMessagesTypeAdapter.dump_json([m])) for m in messages]
    if sum(sizes) <= char_budget:
        return messages
    starts = _turn_starts(messages) or [0]
    for start in starts:
        if sum(sizes[start:]) <= char_budget:
            return messages[start:]
    return messages[starts[-1]:]  # last turn always survives
```

In `config.py` add `history_char_budget: int = 400_000` to `Settings`. In `run_manager._load_history`, after `ModelMessagesTypeAdapter.validate_json(...)`:

```python
msgs = trim_history(msgs, settings.history_char_budget)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_history.py tests/e2e -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/history.py backend/app/agents/run_manager.py backend/app/config.py backend/tests/test_history.py
git commit -m "feat: trim agent history to char budget at turn boundaries"
```

---

### Task 9: Recommendations — model, scanner service, API

Deterministic (no-LLM) cost scan on a schedule: idle containers, dangling images, orphan volumes, long-exited containers. Findings persist in a new table and surface on the dashboard (Task 11). Restart-loop alerts (Task 10) write into the same table.

**Files:**
- Modify: `backend/app/db/models.py` (new table — additive only), `backend/app/config.py` (`cost_scan_interval_hours: float = 24.0`)
- Create: `backend/app/services/recommendations.py`
- Create: `backend/app/api/recommendations.py`
- Modify: `backend/app/main.py` (router + lifespan start/stop)
- Test: `backend/tests/test_recommendations.py` (new)

**Interfaces:**
- Produces: table `recommendations` (id, created_at, updated_at, kind, resource, detail TEXT-JSON, status active|dismissed|resolved, dismissed_at; unique (kind, resource)).
- Produces: singleton `recommendations = RecommendationService()` with `start()`, `stop()`, `scan_now()`, and `record(kind, resource, detail)` (used by Task 10).
- Produces: `GET /api/recommendations?status=active` → `{recommendations: [...]}` ; `POST /api/recommendations/{id}/dismiss`.
- Produces: bus topic `recommendations_updated` with payload `{recommendations: [active rows...]}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_recommendations.py
import json

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
    await svc._store([{"kind": "orphan_volume", "resource": "v1", "detail": {}}])  # same again → still 1 row
    async with session_factory() as db:
        rows = (await db.execute(Recommendation.__table__.select())).fetchall()
        assert len(rows) == 1 and rows[0].status == "active"
    await svc._store([])  # gone from scan → resolved
    async with session_factory() as db:
        rows = (await db.execute(Recommendation.__table__.select())).fetchall()
        assert rows[0].status == "resolved"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_recommendations.py -q` → FAIL (imports missing).

- [ ] **Step 3: Add the model**

Append to `backend/app/db/models.py`, **matching the declarative style already used in that file** (same base class, same column helpers/defaults as e.g. `Approval`):

```python
class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("kind", "resource", name="uq_reco_kind_resource"),)

    id = ...            # uuid4().hex primary key, same pattern as other tables
    created_at = ...    # utcnow default, same pattern
    updated_at = ...    # utcnow default + onupdate, same pattern as ChatSession.updated_at
    kind = ...          # str: idle_container | dangling_image | orphan_volume | exited_container | restart_loop
    resource = ...      # str: container/image/volume name
    detail = ...        # TEXT JSON, default "{}"
    status = ...        # str, default "active": active | dismissed | resolved
    dismissed_at = ...  # nullable datetime
```

- [ ] **Step 4: Implement the service**

```python
# backend/app/services/recommendations.py
"""Scheduled deterministic cost/waste scan → recommendations table → dashboard.

Deliberately LLM-free (runs without an API key); the agent is brought in
via the dashboard's "Diagnose" button, not by the scanner.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import docker

from ..config import settings
from ..db.database import session_factory
from ..db.models import Recommendation
from .event_bus import bus
from .monitor import monitor

log = logging.getLogger(__name__)


class RecommendationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="recommendation-scanner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(60)  # let monitor warm up
        while True:
            try:
                await self.scan_now()
            except Exception:
                log.exception("cost scan failed")
            await asyncio.sleep(settings.cost_scan_interval_hours * 3600)

    async def scan_now(self) -> None:
        findings = await asyncio.to_thread(self._scan_sync)
        await self._store(findings)

    def _scan_sync(self) -> list[dict]:
        findings: list[dict] = []
        cli = docker.from_env()
        for img in cli.images.list(filters={"dangling": True}):
            findings.append({"kind": "dangling_image", "resource": img.short_id,
                             "detail": {"size_mb": round(img.attrs.get("Size", 0) / 1e6, 1)}})
        used = {m.get("Name") for c in cli.containers.list(all=True)
                for m in c.attrs.get("Mounts", []) if m.get("Type") == "volume"}
        for v in cli.volumes.list():
            if v.name not in used:
                findings.append({"kind": "orphan_volume", "resource": v.name, "detail": {}})
        # Idle heuristic from the monitor's live snapshot (already has cpu_percent).
        for c in monitor.containers:
            if c.get("status") == "running" and c.get("cpu_percent") is not None and c["cpu_percent"] < 1.0:
                findings.append({"kind": "idle_container", "resource": c["name"],
                                 "detail": {"cpu_percent": c["cpu_percent"],
                                            "memory_mb": c.get("memory_usage_mb")}})
        return findings

    async def record(self, kind: str, resource: str, detail: dict) -> None:
        """Upsert a single finding immediately (used by the restart-loop detector)."""
        await self._store_partial([{"kind": kind, "resource": resource, "detail": detail}])

    async def _store(self, findings: list[dict]) -> None:
        """Full-scan semantics: upsert findings, resolve active rows no longer found."""
        seen = {(f["kind"], f["resource"]) for f in findings}
        async with session_factory() as db:
            existing = (await db.execute(Recommendation.__table__.select())).fetchall()
            by_key = {(r.kind, r.resource): r for r in existing}
            now = datetime.now(timezone.utc)
            for f in findings:
                row = by_key.get((f["kind"], f["resource"]))
                if row is None:
                    db.add(Recommendation(kind=f["kind"], resource=f["resource"],
                                          detail=json.dumps(f["detail"])))
                elif row.status != "dismissed":
                    await db.execute(Recommendation.__table__.update()
                                     .where(Recommendation.id == row.id)
                                     .values(status="active", detail=json.dumps(f["detail"]), updated_at=now))
            scan_kinds = {"dangling_image", "orphan_volume", "idle_container", "exited_container"}
            for (kind, resource), row in by_key.items():
                if kind in scan_kinds and (kind, resource) not in seen and row.status == "active":
                    await db.execute(Recommendation.__table__.update()
                                     .where(Recommendation.id == row.id).values(status="resolved", updated_at=now))
            await db.commit()
        await self._publish()

    async def _store_partial(self, findings: list[dict]) -> None:
        """Upsert without resolving anything (single-finding path)."""
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            for f in findings:
                existing = (await db.execute(
                    Recommendation.__table__.select()
                    .where(Recommendation.kind == f["kind"], Recommendation.resource == f["resource"])
                )).first()
                if existing is None:
                    db.add(Recommendation(kind=f["kind"], resource=f["resource"], detail=json.dumps(f["detail"])))
                elif existing.status != "dismissed":
                    await db.execute(Recommendation.__table__.update()
                                     .where(Recommendation.id == existing.id)
                                     .values(status="active", detail=json.dumps(f["detail"]), updated_at=now))
            await db.commit()
        await self._publish()

    async def _publish(self) -> None:
        async with session_factory() as db:
            rows = (await db.execute(
                Recommendation.__table__.select().where(Recommendation.status == "active")
            )).fetchall()
        payload = [{"id": r.id, "kind": r.kind, "resource": r.resource,
                    "detail": json.loads(r.detail or "{}"), "updated_at": str(r.updated_at)} for r in rows]
        await bus.publish("recommendations_updated", {"recommendations": payload})


recommendations = RecommendationService()
```

(Adapt raw `__table__` queries to `select(Recommendation)` ORM style if that's what the rest of the codebase uses — follow `run_manager.py`'s query idiom.)

- [ ] **Step 5: API + wiring**

```python
# backend/app/api/recommendations.py
import json

from fastapi import APIRouter, HTTPException

from ..db.database import session_factory
from ..db.models import Recommendation
from ..services.recommendations import recommendations
from datetime import datetime, timezone

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("")
async def list_recommendations(status: str = "active"):
    async with session_factory() as db:
        rows = (await db.execute(
            Recommendation.__table__.select().where(Recommendation.status == status)
        )).fetchall()
    return {"recommendations": [
        {"id": r.id, "kind": r.kind, "resource": r.resource,
         "detail": json.loads(r.detail or "{}"), "updated_at": str(r.updated_at)}
        for r in rows
    ]}


@router.post("/{rec_id}/dismiss")
async def dismiss(rec_id: str):
    async with session_factory() as db:
        row = await db.get(Recommendation, rec_id)
        if row is None:
            raise HTTPException(404)
        row.status = "dismissed"
        row.dismissed_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True}


@router.post("/scan")
async def scan_now():
    await recommendations.scan_now()
    return {"ok": True}
```

In `main.py`: `app.include_router(recommendations_api.router)`; in lifespan add `recommendations.start()` after `monitor.start()` and `await recommendations.stop()` on shutdown. In `config.py` add `cost_scan_interval_hours: float = 24.0`.

- [ ] **Step 6: Run tests + commit**

Run: `cd backend && uv run pytest tests/test_recommendations.py -q` → PASS. Then full: `uv run pytest -q` → PASS.

```bash
git add backend/app
git add backend/tests/test_recommendations.py
git commit -m "feat: scheduled cost scan -> recommendations table + API"
```

---

### Task 10: Restart-loop detector

Proactive RCA signal: ≥3 restarts of a container within 5 minutes → `alert` bus event + a `restart_loop` recommendation row. Data (`restart_count`) is already collected per poll in `monitor.py`.

**Files:**
- Modify: `backend/app/services/monitor.py`
- Test: `backend/tests/test_restart_detector.py` (new)

**Interfaces:**
- Produces: pure function `detect_restart_loops(prev_counts, summaries, events, now, window=300.0, threshold=3) -> list[str]` in `monitor.py` (returns container names newly in a loop); MonitorService calls it each docker poll and publishes `alert` `{kind: "restart_loop", container, restarts_5m}` + `recommendations.record(...)`, debounced to once per 5 min per container.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_restart_detector.py
from collections import defaultdict, deque

from app.services.monitor import detect_restart_loops


def _summaries(count):
    return [{"name": "crashy", "restart_count": count}]


def test_three_restarts_in_window_triggers():
    events = defaultdict(lambda: deque(maxlen=20))
    prev = {}
    hits = []
    t = 1000.0
    for i, rc in enumerate([1, 2, 3, 4]):
        hits = detect_restart_loops(prev, _summaries(rc), events, now=t + i * 30)
    assert hits == ["crashy"]


def test_slow_restarts_do_not_trigger():
    events = defaultdict(lambda: deque(maxlen=20))
    prev = {}
    hits = []
    for i, rc in enumerate([1, 2, 3, 4]):
        hits = detect_restart_loops(prev, _summaries(rc), events, now=1000.0 + i * 400)  # >5min apart
    assert hits == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_restart_detector.py -q` → FAIL (import error).

- [ ] **Step 3: Implement**

In `monitor.py` add a module-level pure function + service wiring:

```python
def detect_restart_loops(prev_counts: dict[str, int], summaries: list[dict],
                         events: dict[str, deque], now: float,
                         window: float = 300.0, threshold: int = 3) -> list[str]:
    """Mutates prev_counts/events trackers; returns containers currently restart-looping."""
    looping = []
    for c in summaries:
        name, rc = c.get("name"), c.get("restart_count") or 0
        if name is None:
            continue
        prev = prev_counts.get(name)
        if prev is not None and rc > prev:
            events[name].append(now)
        prev_counts[name] = rc
        recent = [t for t in events[name] if now - t <= window]
        if len(recent) >= threshold:
            looping.append(name)
    return looping
```

In `MonitorService.__init__` add:

```python
self._restart_counts: dict[str, int] = {}
self._restart_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
self._restart_alerted: dict[str, float] = {}
```

At the end of each successful docker poll iteration (where the container summaries list is built), add:

```python
now = time.time()
for name in detect_restart_loops(self._restart_counts, summaries, self._restart_events, now):
    if now - self._restart_alerted.get(name, 0) > 300:
        self._restart_alerted[name] = now
        count = len([t for t in self._restart_events[name] if now - t <= 300])
        await bus.publish("alert", {"kind": "restart_loop", "container": name, "restarts_5m": count})
        await recommendations.record("restart_loop", name, {"restarts_5m": count})
```

Import `recommendations` lazily inside the method (`from .recommendations import recommendations`) to avoid an import cycle at module load (recommendations already imports monitor).

- [ ] **Step 4: Run tests + manual verify**

Run: `cd backend && uv run pytest tests/test_restart_detector.py -q` → PASS.
Manual: `make seed` (seed-broken-postgres crash-loops), `make dev`, watch backend logs / `GET /api/recommendations` for a `restart_loop` row within ~5 minutes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/monitor.py backend/tests/test_restart_detector.py
git commit -m "feat: restart-loop detector -> alert event + recommendation"
```

---

### Task 11: Dashboard recommendations panel (frontend)

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/stores/liveStore.ts`, `frontend/src/pages/DashboardPage.tsx`

**Interfaces:**
- Consumes: `GET /api/recommendations`, `POST /api/recommendations/{id}/dismiss`, WS topic `recommendations_updated` (payload `{recommendations: [...]}`), WS topic `alert`.
- Produces: `Recommendation` type; `liveStore.recommendations: Recommendation[]`.

- [ ] **Step 1: Types + store**

In `types.ts`:

```ts
export interface Recommendation {
  id: string;
  kind: "idle_container" | "dangling_image" | "orphan_volume" | "exited_container" | "restart_loop";
  resource: string;
  detail: Record<string, unknown>;
  updated_at: string;
}
```

In `liveStore.ts`: add `recommendations: Recommendation[]`, a `setRecommendations` action, and a `handleFrame` case for `recommendations_updated` (replace list) and `alert` (no state needed beyond the list refresh the paired `recommendations_updated` triggers).

- [ ] **Step 2: RecommendationsSection component**

In `DashboardPage.tsx`, add as the **first** section (above `ContainersSection`), following the existing `SectionTitle` + `rounded border border-edge bg-panel` conventions. Seed via `useEffect` → `api.get<{recommendations: Recommendation[]}>("/api/recommendations")` → `setRecommendations`. Per row: kind chip (use `warn` color for `restart_loop`, `dim` otherwise), resource name (`font-mono`), human detail line, and two buttons:

- **Diagnose** → `navigate("/", { state: { draft: draftFor(rec) } })` (same pattern as the existing "Ask copilot" button), where:

```ts
function draftFor(r: Recommendation): string {
  switch (r.kind) {
    case "restart_loop": return `Why does container ${r.resource} keep restarting? Diagnose with logs and events.`;
    case "idle_container": return `Container ${r.resource} looks idle. Confirm and propose a cleanup.`;
    case "dangling_image": return `Review dangling image ${r.resource} and propose a cleanup.`;
    case "orphan_volume": return `Volume ${r.resource} is not attached to any container. Is it safe to remove?`;
    default: return `Investigate ${r.resource}.`;
  }
}
```

- **Dismiss** → `api.post(\`/api/recommendations/${r.id}/dismiss\`)` then remove from store.

Render nothing (no empty panel) when the list is empty.

- [ ] **Step 3: Verify + commit**

Run: `cd frontend && npm run build` → exits 0.
Manual: `make seed && make dev`, `curl -X POST localhost:8000/api/recommendations/scan`, dashboard shows orphan volume / idle container cards; Diagnose lands in chat with a prefilled draft.

```bash
git add frontend/src
git commit -m "feat: dashboard recommendations panel with diagnose/dismiss"
```

---

### Task 12: Tool progress events (pull_image + SSE plumbing)

Long tool calls currently show a silent spinner. Wire MCP progress notifications → gateway-tracked in-flight call → session broadcaster → SSE `tool_progress` → `ToolCallCard` progress line. Scoped to `pull_image` as the proving tool (compose_up subprocess output can follow the same channel later).

**Known limitation (accept + document):** the MCP progress handler is per-server, not per-call. We attribute progress to the call currently routed through the gateway for that server. With two sessions running tools on the same server simultaneously, a progress line could land on the wrong card — cosmetic only.

**Files:**
- Create: `backend/app/services/progress.py`
- Modify: `backend/app/tools/toolsets.py` (progress_handler), `backend/app/tools/gateway.py` (in-flight tracking), `backend/app/agents/run_manager.py` (session emitter + SSE passthrough)
- Modify: `mcp-servers/docker_tools/server.py` (`pull_image` reports progress)
- Modify: `frontend/src/api/types.ts`, `frontend/src/stores/chatStore.ts`, `frontend/src/components/chat/ToolCallCard.tsx`
- Test: `backend/tests/test_progress.py` (new)

**Interfaces:**
- Produces: `app/services/progress.py` with `register_session(session_id, emit)`, `unregister_session(session_id)`, `set_inflight(server, session_id, tool_call_id)`, `clear_inflight(server)`, `emit_progress(server, message)`.
- Produces: SSE event `{type: "tool_progress", tool_call_id: string, text: string}` added to the `ChatEvent` union.

- [ ] **Step 1: SPIKE — confirm the pydantic-ai 2.9 progress hook**

```bash
cd backend && uv run python -c "
import inspect
from pydantic_ai.mcp import MCPToolset
sig = inspect.signature(MCPToolset.__init__)
print('progress_handler' in sig.parameters, sig.parameters.get('progress_handler'))
"
```

Expected: `True` with an annotation like `ProgressFnT | None` (`async def (progress: float, total: float | None, message: str | None)`). If the parameter is absent or per-call only, stop and re-scope this task to gateway-synthesized start/finish progress messages only.

- [ ] **Step 2: Write the failing test for the registry**

```python
# backend/tests/test_progress.py
from app.services import progress


def test_emit_routes_to_registered_session():
    got = []
    progress.register_session("s1", got.append)
    progress.set_inflight("docker", "s1", "call_1")
    progress.emit_progress("docker", "layer abc: downloading")
    assert got == [{"type": "tool_progress", "tool_call_id": "call_1", "text": "layer abc: downloading"}]
    progress.clear_inflight("docker")
    progress.emit_progress("docker", "ignored")
    assert len(got) == 1
    progress.unregister_session("s1")


def test_emit_without_registration_is_noop():
    progress.emit_progress("docker", "nobody listening")  # must not raise
```

Run: `cd backend && uv run pytest tests/test_progress.py -q` → FAIL.

- [ ] **Step 3: Implement the registry**

```python
# backend/app/services/progress.py
"""Routes MCP progress notifications to the SSE stream of the session whose
tool call is currently in flight. Attribution is per-server (see Task 12
limitation note)."""
from __future__ import annotations

from typing import Callable

_emitters: dict[str, Callable[[dict], None]] = {}      # session_id -> emit(payload)
_inflight: dict[str, tuple[str, str]] = {}             # server_id -> (session_id, tool_call_id)


def register_session(session_id: str, emit: Callable[[dict], None]) -> None:
    _emitters[session_id] = emit


def unregister_session(session_id: str) -> None:
    _emitters.pop(session_id, None)


def set_inflight(server: str, session_id: str, tool_call_id: str) -> None:
    _inflight[server] = (session_id, tool_call_id)


def clear_inflight(server: str) -> None:
    _inflight.pop(server, None)


def emit_progress(server: str, message: str) -> None:
    target = _inflight.get(server)
    if not target:
        return
    session_id, tool_call_id = target
    emit = _emitters.get(session_id)
    if emit:
        emit({"type": "tool_progress", "tool_call_id": tool_call_id, "text": message})
```

- [ ] **Step 4: Wire backend plumbing**

`toolsets.py` — build each `MCPToolset` with a progress handler:

```python
def _make_progress_handler(server_id: str):
    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        emit_progress(server_id, message or f"{progress:g}/{total:g}" if total else (message or f"{progress:g}"))
    return on_progress

# in the MCPToolset(...) construction:
progress_handler=_make_progress_handler(server_id),
```

`gateway.py` — around the executor await (READ and non-READ paths both):

```python
progress.set_inflight(server, session_id, ctx.tool_call_id)
try:
    result = await asyncio.wait_for(executor(effective_args), timeout=timeout_for(server, tool))
finally:
    progress.clear_inflight(server)
```

`run_manager.py` — in `_run`, register an emitter for the session that pushes onto the broadcaster (use the Broadcaster's existing non-blocking `put_nowait` fan-out; add a small sync `publish_nowait(payload)` method to `Broadcaster` mirroring its async publish):

```python
progress.register_session(session_id, broadcaster.publish_nowait)
try:
    ...existing run body...
finally:
    progress.unregister_session(session_id)
```

`tool_progress` payloads then flow to SSE automatically (broadcaster events are already serialized verbatim by `chat.py`'s `_sse`).

- [ ] **Step 5: Make `pull_image` report progress**

Replace the existing `pull_image` in `mcp-servers/docker_tools/server.py` with an async, Context-aware version (FastMCP injects `Context`; import `from mcp.server.fastmcp import Context` and `import asyncio, threading` at top):

```python
@mcp.tool(annotations=MUTATING)
async def pull_image(image: str, dry_run: bool = False, ctx: Context | None = None) -> dict:
    """Pull an image, streaming layer progress."""
    try:
        if dry_run:
            return {"dry_run": True, "would_pull": image}
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def worker() -> None:
            try:
                for line in _docker().api.pull(image, stream=True, decode=True):
                    loop.call_soon_threadsafe(q.put_nowait, line)
                loop.call_soon_threadsafe(q.put_nowait, None)
            except Exception as e:  # surface pull errors through the queue
                loop.call_soon_threadsafe(q.put_nowait, {"__error__": str(e)})
                loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=worker, daemon=True).start()
        last_status: dict[str, str] = {}
        while (line := await q.get()) is not None:
            if "__error__" in line:
                return {"error": True, "type": "PullError", "message": line["__error__"]}
            status, layer = line.get("status"), line.get("id")
            if status and status != last_status.get(layer or ""):
                last_status[layer or ""] = status
                if ctx is not None:
                    await ctx.report_progress(progress=0, total=None,
                                              message=f"{layer + ': ' if layer else ''}{status}")
        img = _docker().images.get(image)
        return {"pulled": image, "id": img.short_id, "tags": img.tags}
    except Exception as e:
        return _err(e)
```

- [ ] **Step 6: Frontend**

`types.ts` — extend the `ChatEvent` union:

```ts
| { type: "tool_progress"; tool_call_id: string; text: string }
```

`chatStore.ts` — in `handleEvent`, patch the matching `ToolCallRecord` (add optional `progress?: string` to `ToolCallRecord`):

```ts
case "tool_progress":
  patchToolCall(ev.tool_call_id, { progress: ev.text });
  break;
```

`ToolCallCard.tsx` — when `!result_summary && progress`, show `progress` in the header instead of the bare `running…` label (`font-mono text-[11px] text-dim truncate`).

- [ ] **Step 7: Run tests + manual verify + commit**

Run: `cd backend && uv run pytest tests/test_progress.py tests/test_gateway.py -q` → PASS. `cd frontend && npm run build` → 0.
Manual: `make dev`, ask "pull the redis:7 image" → approve → the tool card shows layer statuses streaming.

```bash
git add backend/app mcp-servers/docker_tools/server.py frontend/src backend/tests/test_progress.py
git commit -m "feat: streaming tool progress (pull_image) via MCP progress notifications"
```

---

### Task 13: Close out Phase 2

**Files:**
- Modify: `PROJECT_STATUS.md`, `CLAUDE.md` (if command/architecture facts changed)

- [ ] **Step 1: Full verification suite**

```bash
cd backend && uv run pytest -q                # all green (Docker Desktop running)
cd frontend && npm run build                  # exits 0
make seed                                     # then manual smoke:
```

Manual smoke against the running app (`make dev`): compose flow ("generate a compose file for nginx+redis and run it"), exec ("check if /etc/nginx/nginx.conf exists inside seed-nginx"), batch approvals, new-message-during-approval, recommendations panel after `POST /api/recommendations/scan`, restart-loop alert from seed-broken-postgres.

- [ ] **Step 2: Update PROJECT_STATUS.md**

Move each delivered Phase 2 row into a "Phase 2 (✅ complete)" section mirroring the Phase 1 format; note the Task 12 attribution limitation; keep undelivered items (if any) listed under Phase 3 or a "deferred" note.

- [ ] **Step 3: Commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: mark Phase 2 complete in PROJECT_STATUS"
```
