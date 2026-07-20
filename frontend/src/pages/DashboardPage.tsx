import { useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { ContainerInfo, HistoryPoint, Recommendation, SystemMetrics } from "../api/types";
import { StatusDot } from "../components/StatusDot";
import { fmtClock, fmtPercent, humanBytes, toMs } from "../lib/format";
import { useLiveStore } from "../stores/liveStore";

// Docker port map: {"6443/tcp": [{HostIp, HostPort}], "80/tcp": null}
function fmtPorts(ports: ContainerInfo["ports"]): string {
  if (!ports || typeof ports !== "object") return "—";
  const entries = Object.entries(ports);
  if (entries.length === 0) return "—";
  const parts = entries.map(([containerPort, bindings]) => {
    const hosts = [...new Set((bindings ?? []).map((b) => b.HostPort).filter(Boolean))];
    return hosts.length > 0 ? `${hosts.join(",")}→${containerPort}` : containerPort;
  });
  return parts.join("  ");
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-2 font-mono text-[11px] uppercase tracking-widest text-faint">{children}</h2>
  );
}

function draftFor(r: Recommendation): string {
  switch (r.kind) {
    case "restart_loop":
      return `Why does container ${r.resource} keep restarting? Diagnose with logs and events.`;
    case "idle_container":
      return `Container ${r.resource} looks idle. Confirm and propose a cleanup.`;
    case "dangling_image":
      return `Review dangling image ${r.resource} and propose a cleanup.`;
    case "orphan_volume":
      return `Volume ${r.resource} is not attached to any container. Is it safe to remove?`;
    default:
      return `Investigate ${r.resource}.`;
  }
}

function detailLine(r: Recommendation): string {
  switch (r.kind) {
    case "restart_loop": {
      const restarts = r.detail.restarts_5m;
      return `${restarts ?? "?"} restarts in the last 5 minutes.`;
    }
    case "idle_container":
      return "No CPU/network activity observed recently.";
    case "dangling_image":
      return "Untagged image not referenced by any container.";
    case "orphan_volume":
      return "Volume is not attached to any container.";
    case "exited_container":
      return "Container has exited.";
    default:
      return "";
  }
}

const KIND_LABEL: Record<Recommendation["kind"], string> = {
  idle_container: "idle",
  dangling_image: "dangling image",
  orphan_volume: "orphan volume",
  exited_container: "exited",
  restart_loop: "restart loop",
};

function RecommendationsSection() {
  const recommendations = useLiveStore((s) => s.recommendations);
  const setRecommendations = useLiveStore((s) => s.setRecommendations);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get<{ recommendations: Recommendation[] }>("/api/recommendations")
      .then((r) => setRecommendations(r.recommendations ?? []))
      .catch(() => {
        // best-effort seed; WS updates will still arrive
      });
  }, [setRecommendations]);

  const diagnose = (r: Recommendation) => {
    navigate("/", { state: { draft: draftFor(r) } });
  };

  const dismiss = async (r: Recommendation) => {
    try {
      await api.post(`/api/recommendations/${r.id}/dismiss`);
    } catch {
      // ignore; WS/refresh will reconcile state either way
    }
    setRecommendations(recommendations.filter((x) => x.id !== r.id));
  };

  if (recommendations.length === 0) return null;

  return (
    <section>
      <SectionTitle>Recommendations</SectionTitle>
      <div className="rounded border border-edge bg-panel">
        {recommendations.map((r) => (
          <div
            key={r.id}
            className="flex items-center gap-3 border-b border-edge/60 px-3 py-2.5 last:border-0"
          >
            <span
              className={`shrink-0 rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${
                r.kind === "restart_loop"
                  ? "border-warn/40 bg-warn/10 text-warn"
                  : "border-edge2 bg-raise text-dim"
              }`}
            >
              {KIND_LABEL[r.kind]}
            </span>
            <span className="shrink-0 font-mono text-xs text-fg">{r.resource}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-dim">{detailLine(r)}</span>
            <div className="flex shrink-0 gap-1.5">
              <button
                type="button"
                onClick={() => diagnose(r)}
                className="whitespace-nowrap rounded border border-edge2 px-2 py-0.5 text-[11px] text-dim hover:border-accent/50 hover:text-accent"
              >
                Diagnose
              </button>
              <button
                type="button"
                onClick={() => dismiss(r)}
                className="whitespace-nowrap rounded border border-edge2 px-2 py-0.5 text-[11px] text-dim hover:border-danger/50 hover:text-danger"
              >
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ContainersSection() {
  const snapshot = useLiveStore((s) => s.containers);
  const navigate = useNavigate();

  const askCopilot = (c: ContainerInfo) => {
    navigate("/", { state: { draft: `Diagnose container ${c.name}` } });
  };

  return (
    <section>
      <SectionTitle>Containers</SectionTitle>
      {snapshot === null ? (
        <div className="rounded border border-edge bg-panel p-4 text-xs text-faint">
          Waiting for live feed…
        </div>
      ) : !snapshot.available ? (
        <div className="rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          Docker unavailable{snapshot.error ? `: ${snapshot.error}` : "."}
        </div>
      ) : snapshot.containers.length === 0 ? (
        <div className="rounded border border-edge bg-panel p-4 text-xs text-faint">
          No containers found.
        </div>
      ) : (
        <div className="overflow-x-auto rounded border border-edge bg-panel">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-edge text-[10px] uppercase tracking-wider text-faint">
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Image</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 text-right font-medium">CPU%</th>
                <th className="px-3 py-2 text-right font-medium">Mem</th>
                <th className="px-3 py-2 font-medium">Ports</th>
                <th className="px-3 py-2 text-right font-medium">Restarts</th>
                <th className="px-3 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {snapshot.containers.map((c) => (
                <tr key={c.id} className="border-b border-edge/60 last:border-0 hover:bg-raise/50">
                  <td className="px-3 py-2 text-fg">{c.name}</td>
                  <td className="max-w-52 truncate px-3 py-2 text-dim" title={c.image}>
                    {c.image}
                  </td>
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1.5">
                      <StatusDot status={c.status} />
                      <span className="text-dim">{c.status}</span>
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-fg">{fmtPercent(c.cpu_percent)}</td>
                  <td className="px-3 py-2 text-right text-fg">{humanBytes(c.memory_usage_bytes)}</td>
                  <td className="px-3 py-2 text-dim">{fmtPorts(c.ports)}</td>
                  <td
                    className={`px-3 py-2 text-right ${c.restart_count > 0 ? "text-warn" : "text-dim"}`}
                  >
                    {c.restart_count}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={() => navigate(`/logs?container=${encodeURIComponent(c.id)}`)}
                        className="whitespace-nowrap rounded border border-edge2 px-2 py-0.5 text-[11px] text-dim hover:border-accent/50 hover:text-accent"
                      >
                        Logs
                      </button>
                      <button
                        type="button"
                        onClick={() => askCopilot(c)}
                        className="whitespace-nowrap rounded border border-edge2 px-2 py-0.5 text-[11px] text-dim hover:border-accent/50 hover:text-accent"
                      >
                        Ask copilot
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const PHASE_STYLE: Record<string, string> = {
  Running: "text-ok border-ok/40 bg-ok/10",
  Succeeded: "text-info border-info/40 bg-info/10",
  Pending: "text-warn border-warn/40 bg-warn/10",
  Failed: "text-danger border-danger/40 bg-danger/10",
};

function K8sSection() {
  const k8s = useLiveStore((s) => s.k8s);

  return (
    <section>
      <SectionTitle>Kubernetes</SectionTitle>
      {k8s === null ? (
        <div className="rounded border border-edge bg-panel p-4 text-xs text-faint">
          Waiting for live feed…
        </div>
      ) : !k8s.available ? (
        <div className="rounded border border-edge bg-panel p-3 text-xs text-faint">
          Kubernetes unavailable{k8s.reason ? ` — ${k8s.reason}` : "."}
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded border border-edge bg-panel p-3">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-faint">Pods</div>
            {(k8s.namespaces ?? [...new Set((k8s.pods ?? []).map((p) => p.namespace))]).map((ns) => {
              const pods = (k8s.pods ?? []).filter((p) => p.namespace === ns);
              if (pods.length === 0) return null;
              return (
                <div key={ns} className="mb-2.5 last:mb-0">
                  <div className="mb-1 font-mono text-xs text-accent">{ns}</div>
                  <div className="space-y-1">
                    {pods.map((p) => (
                      <div key={`${p.namespace}/${p.name}`} className="flex items-center gap-2 font-mono text-xs">
                        <span className="truncate text-fg">{p.name}</span>
                        <span
                          className={`ml-auto shrink-0 rounded border px-1.5 py-px text-[10px] ${
                            PHASE_STYLE[p.phase] ?? "text-dim border-edge2 bg-raise"
                          }`}
                        >
                          {p.phase}
                        </span>
                        {p.restarts > 0 && (
                          <span className="shrink-0 text-[10px] text-warn">↻{p.restarts}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
            {(k8s.pods ?? []).length === 0 && <div className="text-xs text-faint">No pods.</div>}
          </div>
          <div className="rounded border border-edge bg-panel p-3">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-faint">
              Deployments
            </div>
            <div className="space-y-1">
              {(k8s.deployments ?? []).map((d) => (
                <div key={`${d.namespace}/${d.name}`} className="flex items-center gap-2 font-mono text-xs">
                  <span className="text-faint">{d.namespace}/</span>
                  <span className="truncate text-fg">{d.name}</span>
                  <span className="ml-auto shrink-0 text-dim">{d.ready}</span>
                </div>
              ))}
              {(k8s.deployments ?? []).length === 0 && (
                <div className="text-xs text-faint">No deployments.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-edge bg-panel px-3.5 py-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-faint">{label}</div>
      <div className="mt-1 font-mono text-xl text-fg">{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[11px] text-dim">{sub}</div>}
    </div>
  );
}

function SystemSection() {
  const system = useLiveStore((s) => s.system);
  const history = useLiveStore((s) => s.systemHistory);
  const seedSystemHistory = useLiveStore((s) => s.seedSystemHistory);

  useEffect(() => {
    api
      .get<{ current: SystemMetrics | null; history: HistoryPoint[] }>("/api/dashboard/system")
      .then((r) => seedSystemHistory(r.history ?? []))
      .catch(() => {
        // live WS points still accumulate; seed is best-effort
      });
  }, [seedSystemHistory]);

  const data = useMemo(
    () =>
      history
        .map((p) => ({
          ts: toMs(p.ts),
          cpu: p.cpu_percent,
          mem: p.memory_percent,
        }))
        .filter((p) => !Number.isNaN(p.ts)),
    [history],
  );

  return (
    <section>
      <SectionTitle>System</SectionTitle>
      {system === null ? (
        <div className="rounded border border-edge bg-panel p-4 text-xs text-faint">
          Waiting for system metrics…
        </div>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="CPU"
              value={fmtPercent(system.cpu_percent)}
              sub={`${system.cpu_count} cores`}
            />
            <StatTile
              label="Memory"
              value={fmtPercent(system.memory_percent)}
              sub={`${humanBytes(system.memory_used_bytes)} / ${humanBytes(system.memory_total_bytes)}`}
            />
            <StatTile
              label="Disk"
              value={fmtPercent(system.disk_percent)}
              sub={`${humanBytes(system.disk_used_bytes)} / ${humanBytes(system.disk_total_bytes)}`}
            />
            <StatTile label="Samples" value={String(data.length)} sub="last 60 min" />
          </div>
          <div className="rounded border border-edge bg-panel p-3">
            {data.length < 2 ? (
              <div className="py-8 text-center text-xs text-faint">
                Accumulating metric history…
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke="#1e2636" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="ts"
                    type="number"
                    scale="time"
                    domain={["dataMin", "dataMax"]}
                    tickFormatter={(v: number) => fmtClock(v)}
                    tick={{ fill: "#5c6478", fontSize: 10, fontFamily: "monospace" }}
                    stroke="#1e2636"
                  />
                  <YAxis
                    domain={[0, 100]}
                    tick={{ fill: "#5c6478", fontSize: 10, fontFamily: "monospace" }}
                    stroke="#1e2636"
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#10141d",
                      border: "1px solid #2a3448",
                      borderRadius: 4,
                      fontFamily: "monospace",
                      fontSize: 12,
                    }}
                    labelStyle={{ color: "#8b94a7" }}
                    labelFormatter={(v) => fmtClock(v as number)}
                    formatter={(value) => [`${Number(value).toFixed(1)}%`]}
                  />
                  <Legend
                    wrapperStyle={{ fontFamily: "monospace", fontSize: 11, color: "#8b94a7" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="cpu"
                    name="cpu %"
                    stroke="#22d3ee"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="mem"
                    name="memory %"
                    stroke="#fbbf24"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export function DashboardPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-6 p-4">
        <RecommendationsSection />
        <ContainersSection />
        <K8sSection />
        <SystemSection />
      </div>
    </div>
  );
}
