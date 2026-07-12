# infra-copilot

A local AI infrastructure control platform: deploy, monitor, troubleshoot, and
optimize your local Docker / Kubernetes environment through natural language —
with plan-before-execute safety and explicit approval for every mutating action.

Vision: [docs/Local AI Infrastructure Control Platform.md](docs/Local%20AI%20Infrastructure%20Control%20Platform.md)
· Status & roadmap: [PROJECT_STATUS.md](PROJECT_STATUS.md)

## What's in the MVP

- **Chat copilot** (multi-provider: Anthropic / OpenAI / Gemini) with streamed
  responses and visible tool calls
- **Docker control** — full CRUD, gated by a server-side approval flow:
  mutating/destructive tools *cannot* execute without a matching, unconsumed,
  unexpired approval record (enforced in the ToolGateway, not by prompting)
- **Kubernetes visibility** (read-only) with graceful degradation when no
  cluster is configured
- **Live dashboard** — containers with CPU/mem, K8s pods/deployments, host
  metrics with history charts (WebSocket-pushed, no refresh)
- **Log viewer** with live tail
- **RCA mode** — "why is X restarting?" gathers logs/events/stats and cites evidence
- **Cost mode** — finds idle containers, dangling images, orphan volumes
- **Action history** — append-only audit of every tool call (args redacted)
- **Dry-run mode** — global toggle that forces `dry_run=True` on all mutations

## Architecture

```
frontend (Vite/React/TS) ── REST + SSE + WebSocket ──▶ backend (FastAPI)
                                                        ├─ agent (pydantic-ai, multi-provider)
                                                        ├─ ToolGateway (classification → approval → audit)
                                                        ├─ MCP stdio servers: docker / k8s / monitoring
                                                        └─ monitor loop → event bus → /ws/events
```

## Quickstart

Prereqs: Docker Desktop running, `uv`, Node 20+, an API key for at least one
provider (Anthropic / OpenAI / Google).

```bash
make install     # uv sync + npm install
make dev         # backend :8000 + frontend :5173
```

Open http://localhost:5173 → **Settings** → paste an API key → **Test** → chat.

Optional test fixtures (RCA + cost-analysis targets):

```bash
make seed        # healthy nginx, crash-looping postgres, idle busybox, orphan volume
make seed-clean
```

Try:

- "What's running and how much memory is each container using?"
- "Deploy nginx on port 8080" → plan → approval card → approve
- "Why does seed-broken-postgres keep restarting?"
- "Find resource waste and propose a cleanup."

## Tests

```bash
make test
```

Gateway enforcement tests (`backend/tests/test_gateway.py`) prove mutating
tools are blocked without approval, on args mismatch, on reuse, and after
expiry. `test_agent_loop.py` exercises the real MCP servers end-to-end with a
test model (needs Docker; no API key).

## Safety model

1. Static tool classification (`backend/app/tools/classification.py`):
   `read | mutating | destructive`; unknown tools fail closed as destructive.
2. Mutating calls pause the agent run; the UI shows the exact tool + args.
3. On approve, the gateway re-verifies the DB record (args-hash match,
   single-use, 15-min expiry) before executing. Destructive actions also
   require typing the resource name.
4. Every attempt — executed, denied, dry-run, failed — is written to the
   append-only `actions` audit log with secrets redacted.
