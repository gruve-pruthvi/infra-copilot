import { NavLink, Link } from "react-router-dom";
import { useLiveStore } from "../stores/liveStore";
import { BoolDot } from "./StatusDot";
import { RoleBadge } from "./RoleBadge";

const NAV = [
  { to: "/", label: "Chat" },
  { to: "/dashboard", label: "Dashboard" },
  { to: "/logs", label: "Logs" },
  { to: "/history", label: "History" },
  { to: "/settings", label: "Settings" },
];

function HealthRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-2">
      <BoolDot ok={ok} />
      <span className="text-dim">{label}</span>
      <span className={`ml-auto font-mono text-[10px] ${ok ? "text-faint" : "text-danger"}`}>
        {detail}
      </span>
    </div>
  );
}

export function Sidebar() {
  const health = useLiveStore((s) => s.health);
  const wsConnected = useLiveStore((s) => s.wsConnected);
  const pendingCount = useLiveStore((s) => s.pendingApprovals.length);

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-edge bg-panel">
      <div className="border-b border-edge px-4 py-3.5">
        <div className="font-mono text-sm font-semibold tracking-tight text-fg">
          infra<span className="text-accent">-copilot</span>
        </div>
        <div className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-faint">
          local ai infra control
        </div>
      </div>

      <nav className="flex-1 space-y-0.5 p-2">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-2 rounded px-3 py-1.5 text-sm ${
                isActive
                  ? "border-l-2 border-accent bg-raise pl-2.5 text-fg"
                  : "border-l-2 border-transparent pl-2.5 text-dim hover:bg-raise hover:text-fg"
              }`
            }
          >
            {item.label}
            {item.label === "Chat" && pendingCount > 0 && (
              <span className="ml-auto rounded-full border border-warn/50 bg-warn/15 px-1.5 font-mono text-[10px] text-warn">
                {pendingCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <RoleBadge />

      <div className="space-y-1.5 border-t border-edge px-4 py-3 text-xs">
        <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-faint">
          health
        </div>
        {health === null ? (
          <div className="text-faint">checking…</div>
        ) : !health.reachable ? (
          <HealthRow ok={false} label="Backend" detail="down" />
        ) : (
          <>
            <HealthRow ok={health.docker} label="Docker" detail={health.docker ? "ok" : "down"} />
            <HealthRow ok={health.k8s} label="K8s" detail={health.k8s ? "ok" : "unavailable"} />
            {health.providerConfigured ? (
              <HealthRow ok label="Provider" detail="ok" />
            ) : (
              <div className="flex items-center gap-2">
                <BoolDot ok={false} />
                <span className="text-dim">Provider</span>
                <Link
                  to="/settings"
                  className="ml-auto font-mono text-[10px] text-warn underline decoration-warn/50 hover:text-fg"
                >
                  no API key
                </Link>
              </div>
            )}
          </>
        )}
        <div className="flex items-center gap-2 pt-1">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${wsConnected ? "bg-accent" : "bg-faint"}`}
          />
          <span className="font-mono text-[10px] text-faint">
            {wsConnected ? "live feed connected" : "live feed reconnecting…"}
          </span>
        </div>
      </div>
    </aside>
  );
}
