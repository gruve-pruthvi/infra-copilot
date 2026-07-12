// Audit trail of tool actions: GET /api/actions seeded, live rows prepended
// from the action_logged WS topic. Rows expand to show args/result/error.

import { Fragment, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { ActionRecord } from "../api/types";
import { ActionStatusBadge, TierBadge } from "../components/TierBadge";
import { fmtDateTime, fmtDuration, prettyJson } from "../lib/format";
import { useLiveStore } from "../stores/liveStore";

const TIERS = ["", "read", "mutating", "destructive"] as const;
const STATUSES = ["", "executed", "denied", "failed", "dry_run"] as const;

export function HistoryPage() {
  const [tier, setTier] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [actions, setActions] = useState<ActionRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const liveActions = useLiveStore((s) => s.liveActions);

  useEffect(() => {
    let cancelled = false;
    setActions(null);
    setError(null);
    const qs = new URLSearchParams({ limit: "200" });
    if (tier) qs.set("tier", tier);
    api
      .get<ActionRecord[]>(`/api/actions?${qs.toString()}`)
      .then((r) => {
        if (!cancelled) setActions(r);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tier]);

  const rows = useMemo(() => {
    const base = actions ?? [];
    const known = new Set(base.map((a) => String(a.id)));
    const fresh = liveActions.filter(
      (a) => !known.has(String(a.id)) && (!tier || a.tier === tier),
    );
    const merged = [...fresh, ...base];
    return status ? merged.filter((a) => a.status === status) : merged;
  }, [actions, liveActions, tier, status]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-4">
        <div className="mb-3 flex items-center gap-3">
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-faint">
            Action history
          </h1>
          <select
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            className="ml-auto rounded border border-edge bg-panel px-2 py-1 font-mono text-xs text-fg outline-none focus:border-accent/50"
          >
            {TIERS.map((t) => (
              <option key={t} value={t}>
                {t || "all tiers"}
              </option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded border border-edge bg-panel px-2 py-1 font-mono text-xs text-fg outline-none focus:border-accent/50"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "all statuses"}
              </option>
            ))}
          </select>
        </div>

        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
            Failed to load actions: {error}
          </div>
        )}
        {!error && actions === null && (
          <div className="rounded border border-edge bg-panel p-4 text-center text-xs text-faint">
            Loading…
          </div>
        )}
        {!error && actions !== null && rows.length === 0 && (
          <div className="rounded border border-edge bg-panel p-4 text-center text-xs text-faint">
            No actions recorded.
          </div>
        )}

        {rows.length > 0 && (
          <div className="overflow-x-auto rounded border border-edge bg-panel">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="border-b border-edge text-[10px] uppercase tracking-wider text-faint">
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Tool</th>
                  <th className="px-3 py-2 font-medium">Tier</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 text-right font-medium">Duration</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => {
                  const id = String(a.id);
                  const expanded = expandedId === id;
                  return (
                    <Fragment key={id}>
                      <tr
                        className="cursor-pointer border-b border-edge/60 last:border-0 hover:bg-raise/50"
                        onClick={() => setExpandedId(expanded ? null : id)}
                      >
                        <td className="whitespace-nowrap px-3 py-2 text-dim">
                          {fmtDateTime(a.created_at)}
                        </td>
                        <td className="px-3 py-2 text-fg">
                          <span className="text-faint">{a.server}.</span>
                          {a.tool}
                        </td>
                        <td className="px-3 py-2">
                          <TierBadge tier={a.tier} />
                        </td>
                        <td className="px-3 py-2">
                          <ActionStatusBadge status={a.status} />
                        </td>
                        <td className="px-3 py-2 text-right text-dim">
                          {fmtDuration(a.duration_ms)}
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="border-b border-edge/60 bg-ink/60 last:border-0">
                          <td colSpan={5} className="px-3 py-2.5">
                            <div className="grid gap-3 md:grid-cols-2">
                              <div>
                                <div className="mb-1 text-[10px] uppercase tracking-wider text-faint">
                                  args
                                </div>
                                <pre className="overflow-x-auto rounded bg-ink p-2 text-[11px] leading-relaxed text-dim">
                                  {a.args !== undefined && a.args !== null
                                    ? prettyJson(a.args)
                                    : "(not included in live event)"}
                                </pre>
                              </div>
                              <div>
                                <div className="mb-1 text-[10px] uppercase tracking-wider text-faint">
                                  {a.error ? "error" : "result"}
                                </div>
                                <pre
                                  className={`max-h-56 overflow-auto whitespace-pre-wrap rounded bg-ink p-2 text-[11px] leading-relaxed ${
                                    a.error ? "text-danger" : "text-fg"
                                  }`}
                                >
                                  {a.error ?? a.result_summary ?? "—"}
                                </pre>
                                {a.session_id && (
                                  <div className="mt-1.5 text-[10px] text-faint">
                                    session: {a.session_id}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
