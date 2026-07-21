# Project Status & Roadmap

> Last updated: 2026-07-21 · Companion to the vision document at
> [`docs/Local AI Infrastructure Control Platform.md`](docs/Local%20AI%20Infrastructure%20Control%20Platform.md)
> and the README quickstart.

This document tracks what has been built (Phase 1 / MVP), how it maps to the
vision, and what the next phases should deliver.

---

## Phase 1 — MVP (✅ complete)

Scope followed §18 of the vision doc ("Recommended MVP Scope"). All milestones
were built and verified on 2026-07-11.

### What was built

| Area | Status | Where |
|---|---|---|
| Chat-based AI interface (streamed, tool calls visible) | ✅ | `frontend/src/pages/ChatPage.tsx`, `backend/app/api/chat.py` |
| Multi-provider LLM (Anthropic / OpenAI / Gemini, per-session) | ✅ | `backend/app/llm/models.py` (pydantic-ai 2.9) |
| Docker control — full CRUD via natural language | ✅ | `mcp-servers/docker_tools/server.py` (25 tools) |
| Approval flow before mutating actions (server-side enforced) | ✅ | `backend/app/tools/gateway.py` + `classification.py` |
| Kubernetes visibility (read-only, graceful degradation) | ✅ | `mcp-servers/k8s_tools/server.py` |
| System monitoring dashboard (live, WebSocket-pushed) | ✅ | `backend/app/services/monitor.py`, `frontend/.../DashboardPage.tsx` |
| Container log reading + live tail | ✅ | `backend/app/api/logs.py`, `LogsPage.tsx` |
| Basic RCA explanations (evidence-driven prompt mode) | ✅ | `backend/app/agents/prompts.py` §RCA + evidence tools |
| Basic cost-optimization suggestions | ✅ | `prompts.py` §Cost + `all_container_stats`, `list_volumes`, `docker_disk_usage`, `list_images(dangling)` |
| Action history / append-only audit log (secrets redacted) | ✅ | `actions` table, `HistoryPage.tsx` |
| Dry-run mode (global toggle; mutations become previews) | ✅ | gateway + every mutating tool's `dry_run` param |
| MCP tool servers (stdio, modular) | ✅ | `mcp-servers/` — docker, k8s, monitoring |
| Manifest generation (sandboxed to `~/.infra-copilot/workspace`) | ✅ | `write_manifest` native tool |
| Settings (provider keys write-only/masked, model picker, test connection) | ✅ | `backend/app/api/settings.py`, `SettingsPage.tsx` |

### Architecture as built

```
frontend (Vite/React/TS) ── REST + SSE + WebSocket ──▶ backend (FastAPI)
                                                        ├─ Agent: single "Infra Copilot" (pydantic-ai 2.9)
                                                        │    RCA + Cost = prompt modes, not sub-agents
                                                        ├─ ToolGateway: classify → approve → audit
                                                        │    (deterministic code — the "safety agent")
                                                        ├─ MCP stdio servers: docker / k8s / monitoring
                                                        ├─ Native tools: system_history, write_manifest
                                                        └─ Monitor loop → in-process EventBus → /ws/events
Persistence: SQLite (~/.infra-copilot/infra-copilot.db, chmod 600)
```

Deliberate MVP deviations from the vision doc (documented decisions, not gaps):

- **One agent instead of a multi-agent orchestrator.** RCA and Cost Advisor are
  prompt modes; the Safety "agent" is the deterministic ToolGateway (strictly
  stronger than an LLM gate). Promote to sub-agents in Phase 3 if routing
  quality demands it.
- **No MQTT.** In-process asyncio EventBus + WebSockets cover all MVP eventing.
  Revisit only when out-of-process consumers exist.
- **Dashboard bypasses MCP.** The monitor loop calls docker-py/psutil directly;
  MCP is the *agent's* tool surface. Fewer moving parts, same modularity where
  it matters.
- **Tailwind hand-rolled components instead of shadcn/ui** (dependency-light,
  no functional difference).

### Safety model (implemented + tested)

1. Static tool→tier table (`read | mutating | destructive`); unknown tools fail
   closed as destructive.
2. Mutating calls pause the run (`ApprovalRequired` → `DeferredToolRequests`);
   the UI shows the exact tool + args.
3. On approve, the gateway re-verifies the DB record: args-hash match,
   single-use, 15-minute expiry. Destructive actions additionally require
   typing the resource name.
4. Every attempt (executed / denied / failed / dry_run) is audited with
   secrets redacted — in the audit row *and* in results fed back to the model.

### Verification status

- `backend/tests/test_gateway.py` — 10 adversarial enforcement tests (no
  approval / wrong args / replay / expiry / dry-run / redaction / default-deny).
- `backend/tests/test_agent_loop.py` — real MCP stdio servers + agent loop
  (TestModel, no API key; needs Docker).
- `backend/tests/e2e/test_approval_flow.py` — full pause → approve/reject →
  resume → audit cycle through the RunManager.
- Frontend: `npm run build` clean (strict tsc); browser-verified against the
  live backend, including backend-down states.
- `make seed` fixtures verified: crash-looping postgres (RCA target), idle
  container + orphan volume + healthy nginx (cost/dashboard targets).

### Known gaps / immediate TODOs (still Phase 1)

- [ ] **Real-LLM smoke test on all three providers** — never run (no API key
      was available at build time). Paste a key in Settings → Test → run the
      M2/M3 checkpoints: "what's running?", "deploy nginx on port 8080"
      (approve), "why does seed-broken-postgres keep restarting?".
- [ ] **Git repo** — not initialized (was never requested). `git init` +
      initial commit recommended before further work.
- [x] ~~Chat while `awaiting_approval` is blocked (409) — resolve pending cards
      first.~~ Fixed in Phase 2: a new message now auto-rejects pending
      approvals ("superseded by a new user message") and continues.
- [ ] `agent_history` grows unbounded per session — fine for MVP, needs
      trimming/compaction for long sessions (Phase 2).
- [ ] No auth on the backend — acceptable for localhost-only MVP; must be
      addressed before any non-localhost exposure (Phase 3).
- [ ] OpenAI/Gemini default model names in `llm/models.py` CATALOG are
      suggestions; the model field is free-form — verify against current
      provider catalogs when first configuring them.

---

## Phase 2 — Deeper operations (✅ complete)

Goal: make the copilot genuinely useful for day-to-day local dev operations.
All 12 items below were delivered.

### What was built

| Area | Status | Where |
|---|---|---|
| Per-tool timeouts | ✅ | `backend/app/tools/classification.py` (`timeout_for`), `backend/app/tools/gateway.py` |
| Docker Compose tools | ✅ | `mcp-servers/docker_tools/server.py` (`compose_up/down/ps/logs`) |
| `exec_in_container` diagnostics tool | ✅ | `mcp-servers/docker_tools/server.py`; classified **DESTRUCTIVE** (promoted from the original MUTATING plan — see `backend/app/tools/classification.py`) |
| `check_connectivity` + DB provisioning prompt | ✅ | `mcp-servers/monitoring_tools/server.py` (`check_connectivity`), `backend/app/agents/prompts.py` |
| Batch approvals | ✅ | `backend/app/api/approvals.py`, `frontend/src/components/ApprovalToast.tsx` |
| Auto-reject stale approvals on new message | ✅ | `backend/app/agents/run_manager.py` |
| Frontend approval toast queue + batch UI | ✅ | `frontend/src/components/ApprovalToast.tsx` |
| Agent-history trimming | ✅ | `backend/app/agents/history.py` |
| Recommendations table + scheduled cost scanner + API | ✅ | `backend/app/services/recommendations.py`, `backend/app/api/recommendations.py` |
| Restart-loop detector | ✅ | `backend/app/services/monitor.py` (`detect_restart_loops`, `_check_restart_loops`) |
| Dashboard recommendations panel | ✅ | `frontend/src/pages/DashboardPage.tsx` |
| Streaming tool-progress | ✅ | `backend/app/services/progress.py` |

### Known limitation

- **Tool-progress attribution is per-server, not per-call.** `backend/app/services/progress.py`
  tracks progress by MCP server process, so two concurrent sessions hitting the
  same MCP server (e.g. two chats both pulling docker images) can see
  cosmetically mis-routed progress text. Cosmetic only — it does not affect
  correctness of the underlying tool call or the gateway's approval/audit path.

### Verification status

46 backend tests pass; frontend strict build clean; browser + real-LLM smoke
deferred.

## Phase 3 — Kubernetes write ops & multi-agent (vision §8, §10)

| Item | Notes |
|---|---|
| **K8s write operations** | `apply_manifest`, `scale`, `rollout_restart`, `rollback`, configmap/secret management — all gated as mutating/destructive through the existing gateway (the enforcement layer needs zero changes; add tools + classification rows). |
| **Docker-vs-K8s placement decisions** | Prompt policy from vision §10: Docker for fast local dev, K8s for multi-service/production-like. |
| **Promote RCA / Cost to sub-agents** | Only if single-agent routing quality degrades as the tool surface grows; pydantic-ai supports agent-as-tool delegation. Orchestrator stays thin. |
| **Local auth** (vision §7.2) | Password/PIN + session tokens on the API and WS endpoints; prerequisite for any LAN exposure. |
| **Role boundaries** (vision §16) | Read-only mode / operator mode; per-tier permissions per user. |
| **Backup tools** (vision §6) | Volume snapshot/restore, `pg_dump` for provisioned databases (destructive-tier restore). |

## Phase 4 — Platform maturity (vision §13, §17, §19)

| Item | Notes |
|---|---|
| **MQTT event bus** | Only when there are out-of-process consumers (other machines, plugins). Swap `EventBus` publish calls — subscriber API already isolates this. |
| **Self-observability** (vision §17) | Request/latency/token metrics per run, tool failure rates, model response quality dashboards; OpenTelemetry export (pydantic-ai has native OTel support). |
| **Self-healing** (vision §19) | Policy-gated auto-remediation: pre-approved runbooks (e.g. "restart on OOM, max 2×/hour") executed without a human in the loop but fully audited. |
| **Multi-environment control** | Remote Docker hosts / multiple kubeconfig contexts as first-class "environments". |
| **Packaging** | Single-command install (uvx / brew / Docker image bundling backend+frontend), auto-start, versioned releases. |
| **Web search tool** (vision §11.6) | Optional, allowlisted; for docs lookup during RCA. |

---

## Conventions for future phases

- **Every new tool** gets: a row in `backend/app/tools/classification.py`
  (or it fails closed as destructive), a `dry_run` param if mutating, secret
  redaction on its outputs, and a gateway-path test.
- **Provider quirks stay in `backend/app/llm/models.py`** — nothing else in the
  codebase may branch on provider.
- **pydantic-ai is at 2.9.0** and its 2.x API differs from older docs
  (`MCPToolset`/`StdioTransport`, `FunctionToolResultEvent.part`,
  `deferred_tool_results=` on `Agent.run`). Verify against the installed
  package before editing agent plumbing.
- **The gateway tests are the contract.** Any change to approval/audit logic
  must keep `tests/test_gateway.py` green and extend it for new behavior.
