# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local AI infrastructure control platform: chat with an LLM agent that controls Docker / inspects Kubernetes, with a plan-before-execute safety model where every mutating action requires server-side approval. Vision doc in `docs/`, current status and roadmap in `PROJECT_STATUS.md` (keep it updated when completing roadmap items).

## Commands

```bash
make install     # uv sync (backend) + npm install (frontend)
make dev         # backend :8000 + frontend :5173 together
make test        # all backend tests (cd backend && uv run pytest -q)
make seed        # docker test fixtures (crash-looping postgres, idle container, orphan volume)
make seed-clean
```

Single test: `cd backend && uv run pytest tests/test_gateway.py -q` (or `-k name`).

Frontend typecheck + build: `cd frontend && npm run build` (strict tsc; there is no separate lint setup in either package).

`tests/test_agent_loop.py` and `tests/e2e/` need Docker Desktop running (they spawn the real MCP stdio servers with pydantic-ai's TestModel — no API key needed). `tests/test_gateway.py` needs neither.

## Layout / environment

- **One venv for both Python packages**: `mcp-servers/` is an editable path dependency of `backend/` (see `[tool.uv.sources]`). Always `uv sync` / `uv run` from `backend/`; there is no separate venv for mcp-servers. MCP servers are spawned as `sys.executable -m docker_tools.server` etc.
- Runtime data lives in `~/.infra-copilot/` — SQLite DB (chmod 600), generated manifests in `workspace/`. Tests point `INFRA_COPILOT_DATA_DIR` at a temp dir in `conftest.py` *before* importing the app; preserve that ordering.
- No git repo is initialized yet.

## Architecture

```
frontend (Vite/React/TS) ── REST + SSE + WebSocket ──▶ backend (FastAPI)
                                                        ├─ Agent: single "Infra Copilot" (pydantic-ai 2.9)
                                                        │    RCA + Cost are prompt modes, not sub-agents
                                                        ├─ ToolGateway: classify → approve → audit  ← security core
                                                        ├─ MCP stdio servers: docker / k8s / monitoring
                                                        ├─ Native tools: system_history, write_manifest
                                                        └─ Monitor loop → in-process EventBus → /ws/events
```

- **`backend/app/tools/gateway.py` + `classification.py` are the security boundary.** Every agent tool call flows through `ToolGateway.execute`: static tier classification (`read | mutating | destructive`, unknown tools fail closed as destructive), then for mutating/destructive tools a DB approval check (args-hash match, single-use, 15-minute expiry), then an append-only `actions` audit row with secrets redacted. The pydantic-ai `ApprovalRequired` pause is only UX — the DB check is the guarantee. The gateway treats the LLM layer as adversarial; `tests/test_gateway.py` is the contract and must stay green (extend it for any new approval/audit behavior).
- **Chat run lifecycle** is orchestrated by `backend/app/agents/run_manager.py`: run streams via SSE (`api/chat.py`), pauses on `ApprovalRequired` → `DeferredToolRequests`, the frontend shows an approval card, `api/approvals.py` approve/reject resumes the run with `deferred_tool_results=`. New chat while a run is `awaiting_approval` returns 409.
- **The dashboard deliberately bypasses MCP**: `services/monitor.py` calls docker-py/psutil directly and pushes over `/ws/events`. MCP is the *agent's* tool surface only.
- **Global dry-run mode** (settings toggle) forces `dry_run=True` on mutating tools and skips the approval requirement.

## Conventions

- **Every new tool** gets: a row in `backend/app/tools/classification.py` (or it fails closed as destructive), a `dry_run` param if mutating, secret redaction on outputs, and a gateway-path test.
- **Provider quirks live only in `backend/app/llm/models.py`** — nothing else may branch on provider (Anthropic / OpenAI / Gemini, selected per-session in Settings).
- **pydantic-ai is at 2.9.0** and its 2.x API differs from older docs and training data: `MCPToolset(StdioTransport(...), process_tool_call=...)` (not `MCPServerStdio`), `FunctionToolResultEvent.part` (not `.result`), `Agent.run(..., deferred_tool_results=, event_stream_handler=)`. Verify against the installed package before editing agent plumbing.
- Frontend: Tailwind v4 (via `@tailwindcss/vite`), zustand stores, recharts; hand-rolled components, no shadcn/ui. Docker container `ports` is passed through in the raw docker-py dict shape, not an array.
