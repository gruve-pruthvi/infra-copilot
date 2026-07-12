import type { Tier } from "../api/types";

const STYLES: Record<Tier, string> = {
  read: "text-dim border-edge2 bg-raise",
  mutating: "text-warn border-warn/40 bg-warn/10",
  destructive: "text-danger border-danger/40 bg-danger/10",
};

export function TierBadge({ tier }: { tier: Tier }) {
  const cls = STYLES[tier] ?? STYLES.read;
  return (
    <span
      className={`inline-block rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${cls}`}
    >
      {tier}
    </span>
  );
}

const STATUS_STYLES: Record<string, string> = {
  executed: "text-ok border-ok/40 bg-ok/10",
  denied: "text-danger border-danger/40 bg-danger/10",
  failed: "text-danger border-danger/40 bg-danger/10",
  dry_run: "text-info border-info/40 bg-info/10",
};

export function ActionStatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? "text-dim border-edge2 bg-raise";
  return (
    <span
      className={`inline-block rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${cls}`}
    >
      {status}
    </span>
  );
}
