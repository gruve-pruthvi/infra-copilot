# Project Status & Roadmap

> Last updated: 2026-08-31 · Test & verification procedures live in
> [`TESTING.md`](TESTING.md) · Companion to the vision document at
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

- [x] ~~**Real-LLM smoke test**~~ — run 2026-08-31 against **Google Gemini**
      (`gemini-3.5-flash` / `gemini-2.5-flash`). All three M2/M3 checkpoints
      pass end to end: "what's running?" (correct tool selection + streaming),
      "deploy nginx on 8080" (pause → approve → execute → audited, replay
      refused), and RCA on `seed-broken-postgres` (pulled logs, diagnosed the
      missing `POSTGRES_PASSWORD`, offered the fix without acting).
      Two real bugs were found by this run and fixed — see *Bugs found by the
      smoke test* below. **Anthropic and OpenAI remain unverified** (no keys).
- [x] ~~**Git repo**~~ — initialized; this doc was stale.
- [x] ~~Chat while `awaiting_approval` is blocked (409) — resolve pending cards
      first.~~ Fixed in Phase 2: a new message now auto-rejects pending
      approvals ("superseded by a new user message") and continues.
- [ ] `agent_history` grows unbounded per session — fine for MVP, needs
      trimming/compaction for long sessions (Phase 2).
- [x] ~~No auth on the backend~~ — local PIN auth + roles delivered in Phase 3.
- [x] ~~Gemini model names in CATALOG~~ — corrected 2026-08-31 against a live
      key. `gemini-3-pro` never existed; **and the `/v1beta/models` listing is
      not a reliable guide** — `gemini-2.5-pro` appears in it but 404s for new
      keys ("no longer available to new users"). Defaults now point at
      round-trip-verified models.
- [ ] OpenAI default model names in CATALOG are still unverified suggestions.

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

## Phase 3 — Kubernetes write ops, auth & roles (✅ complete)

Delivered on branch `phase-3`. 170 backend tests pass; frontend strict build
clean; verified live against real Docker, a local kind cluster, and Gemini.

| Item | Status | Where |
|---|---|---|
| K8s write ops — scale, rollout_restart, apply_manifest, rollback | ✅ | `mcp-servers/k8s_tools/server.py` |
| K8s ConfigMaps — list/get/apply/delete | ✅ | same; `apply` mutating, `delete` destructive |
| K8s Secrets — list (key names only) / apply / delete | ✅ | reads never return values, not even base64; all writes destructive-tier |
| Backup tools — backup_volume, restore_volume, list_backups, pg_dump, pg_restore | ✅ | `mcp-servers/docker_tools/server.py`; restores are destructive |
| Docker-vs-K8s placement policy | ✅ | `backend/app/agents/prompts.py` §Placement |
| Local auth (PIN + session tokens) | ✅ | `backend/app/services/auth.py`, `backend/app/api/auth.py` |
| Role boundaries (viewer/operator/admin) | ✅ | `backend/app/tools/roles.py`, enforced in `gateway.py` |
| Frontend login gate + role badge + elevation | ✅ | `frontend/src/pages/LoginPage.tsx`, `components/RoleBadge.tsx` |
| First-run PIN setup in the UI | ✅ | `SecuritySection` in `frontend/src/pages/SettingsPage.tsx` (the login gate alone could never show the setup form) |
| Promote RCA / Cost to sub-agents | ⏸ deferred | Was always conditional on routing quality degrading. It has not: the live RCA run selected the right tool unaided. Revisit only if it does. |

### Auth & role model (as built)

- **Auth is enforced iff a PIN is configured.** A fresh install stays open, so
  upgrading breaks nothing and you cannot lock yourself out. Set a PIN in the
  UI to turn enforcement on.
- PIN stored only as a salted scrypt hash; tokens only as sha256 digests. The
  browser's sole credential is an httpOnly cookie — the token is deliberately
  never placed in localStorage or a WS query string. A `Bearer` header is
  accepted for non-browser clients.
- Roles cap tiers — viewer→read, operator→+mutating, admin→+destructive —
  enforced **in the ToolGateway** next to tier classification, failing closed
  to viewer. Global dry-run does *not* bypass the ceiling.
- **Scope caveat, documented in `roles.py`:** with a single PIN, `/elevate`
  reuses that same PIN, so anyone holding a viewer token can elevate. Roles
  guard against *mistakes*, not between distinct human principals. Per-user
  credentials were deliberately not built.

### Security review (2026-08-31)

An adversarial review of the auth/role code produced six actionable findings,
all fixed in `fix(security): harden auth from Phase 3 review`:

1. **WS had no Origin check (high).** Browsers do not apply CORS to
   WebSockets, so `cors_origins` protected REST and nothing else — any page
   the operator visited could stream live infra state and container logs from
   a default no-PIN install. Now origin-checked *regardless* of PIN state.
2. **scrypt ran on the event loop** — unauthenticated `/login` could stall the
   whole server. Moved off-thread.
3. **No login throttling** — 4-digit PIN was brute-forceable in minutes. Now
   exponential backoff after 5 failures, shared with `/elevate`.
4. **`PUT /api/settings` was not role-gated** — a viewer could set
   `dry_run_mode`, which *skips the approval requirement globally*. Now admin.
5. **Token was mirrored into localStorage and the WS query string**, negating
   httpOnly and leaking a credential into proxy logs. Removed.
6. **A resumed run kept its start-of-turn ceiling** even after elevation
   lapsed or when a lower-privileged caller resumed it. Now clamped to the
   resolver's live role — privilege can only narrow.

### Bugs found by the smoke test

The first-ever real-LLM run immediately surfaced two defects that no
`TestModel` test could have:

- **Sessions wedged permanently after a mid-run provider error.** A Gemini 503
  landed after a tool executed but before history was saved, leaving a
  tool-call with no tool-return. pydantic-ai then rejected *every* later
  prompt, and neither recovery path applied (status was `error`, not
  `awaiting_approval`; the approval row was already consumed). Fixed by
  `reconcile_tool_calls()` in `agents/history.py`.
- **Audit log said `executed` for tools that failed.** MCP tools report errors
  by *returning* `{"error": true}` rather than raising, so the gateway's
  exception handler never saw them — the security record claimed actions took
  effect when Docker had refused them. Fixed in `gateway.py`.

### Deferred to Phase 4

- Per-user credentials / multi-principal roles (see the scope caveat above).
- Anthropic + OpenAI real-LLM verification (no keys available).

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
