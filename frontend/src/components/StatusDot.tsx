// Container status dot: running=green, restarting=red pulse, otherwise gray.

export function statusKind(status: string | null | undefined): "running" | "restarting" | "exited" {
  const s = (status ?? "").toLowerCase();
  if (s.includes("restarting")) return "restarting";
  if (s.includes("running") || s.includes("up")) return "running";
  return "exited";
}

export function StatusDot({ status }: { status: string | null | undefined }) {
  const kind = statusKind(status);
  const cls =
    kind === "running"
      ? "bg-ok"
      : kind === "restarting"
        ? "bg-danger animate-pulse"
        : "bg-faint";
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${cls}`} />;
}

/** Generic boolean dot for health rows. */
export function BoolDot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${ok ? "bg-ok" : "bg-danger"}`} />;
}
