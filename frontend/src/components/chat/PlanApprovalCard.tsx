// Inline approval card: plan summary, tool + tier, args JSON, countdown, and
// Approve/Reject. Destructive tier requires typing the target resource name.

import { useEffect, useMemo, useState } from "react";
import type { PendingApproval } from "../../api/types";
import { fmtCountdown, firstStringArg, prettyJson, secondsUntil } from "../../lib/format";
import { TierBadge } from "../TierBadge";

interface Props {
  approval: PendingApproval;
  resolved?: "approved" | "rejected";
  onApprove: (confirmText?: string) => Promise<string | null>;
  onReject: () => Promise<string | null>;
}

export function PlanApprovalCard({ approval, resolved, onApprove, onReject }: Props) {
  const [remaining, setRemaining] = useState(() => secondsUntil(approval.expires_at));
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setInterval(() => setRemaining(secondsUntil(approval.expires_at)), 1000);
    return () => clearInterval(t);
  }, [approval.expires_at]);

  const expired = remaining <= 0;
  const destructive = approval.tier === "destructive";
  const expectedHint = useMemo(() => firstStringArg(approval.args), [approval.args]);
  const confirmOk = !destructive || (expectedHint ? confirmText === expectedHint : confirmText.length > 0);
  const actionable = !resolved && !expired && !busy;

  const act = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setError(null);
    const err = await fn();
    if (err) setError(err);
    setBusy(false);
  };

  const borderCls = destructive ? "border-danger/50" : "border-warn/40";

  return (
    <div className={`rounded-md border ${borderCls} bg-panel`}>
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
        <span className="font-mono text-[10px] uppercase tracking-widest text-warn">
          approval required
        </span>
        <TierBadge tier={approval.tier} />
        <span className="ml-auto font-mono text-[11px] text-dim">
          {resolved ? "" : expired ? "expired" : `expires in ${fmtCountdown(remaining)}`}
        </span>
      </div>
      <div className="space-y-2.5 px-3 py-2.5">
        <p className="whitespace-pre-wrap leading-relaxed text-fg">{approval.plan_summary}</p>
        <div className="font-mono text-xs text-dim">
          {approval.server}
          <span className="text-faint">.</span>
          <span className="text-fg">{approval.tool}</span>
        </div>
        <pre className="overflow-x-auto rounded bg-ink p-2 font-mono text-[12px] leading-relaxed text-dim">
          {prettyJson(approval.args)}
        </pre>

        {destructive && !resolved && (
          <div>
            <label className="mb-1 block text-xs text-dim">
              Type the resource name to confirm
              {expectedHint && (
                <span className="ml-1 font-mono text-danger">({expectedHint})</span>
              )}
            </label>
            <input
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              disabled={!actionable}
              placeholder={expectedHint ?? "resource name"}
              className="w-full rounded border border-edge bg-ink px-2 py-1.5 font-mono text-xs text-fg outline-none placeholder:text-faint focus:border-danger/60"
            />
          </div>
        )}

        {error && <p className="text-xs text-danger">{error}</p>}

        <div className="flex items-center gap-2 pt-0.5">
          {resolved ? (
            <span
              className={`font-mono text-[11px] uppercase tracking-wider ${
                resolved === "approved" ? "text-ok" : "text-danger"
              }`}
            >
              {resolved}
            </span>
          ) : (
            <>
              <button
                type="button"
                disabled={!actionable || !confirmOk}
                onClick={() =>
                  act(() => onApprove(destructive ? confirmText : undefined))
                }
                className="rounded border border-ok/50 bg-ok/10 px-3 py-1 text-xs font-medium text-ok hover:bg-ok/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Approve
              </button>
              <button
                type="button"
                disabled={!actionable}
                onClick={() => act(onReject)}
                className="rounded border border-edge2 px-3 py-1 text-xs text-dim hover:border-danger/50 hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
              >
                Reject
              </button>
              {expired && <span className="text-xs text-faint">This approval has expired.</span>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
