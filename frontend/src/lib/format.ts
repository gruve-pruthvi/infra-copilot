// Formatting helpers shared across pages.

export function humanBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let u = -1;
  do {
    v /= 1024;
    u += 1;
  } while (v >= 1024 && u < units.length - 1);
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[u]}`;
}

export function fmtPercent(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** Tolerant timestamp → ms. Accepts ISO strings, epoch seconds, or epoch ms. */
export function toMs(ts: number | string | null | undefined): number {
  if (ts === null || ts === undefined) return NaN;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const n = Date.parse(ts);
  return Number.isNaN(n) ? NaN : n;
}

export function fmtClock(ts: number | string): string {
  const ms = toMs(ts);
  if (Number.isNaN(ms)) return String(ts);
  return new Date(ms).toLocaleTimeString([], { hour12: false });
}

export function fmtDateTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const ms = toMs(ts);
  if (Number.isNaN(ms)) return String(ts);
  const d = new Date(ms);
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${d.toLocaleTimeString([], { hour12: false })}`;
}

/** Seconds remaining until an ISO timestamp (negative when past). */
export function secondsUntil(iso: string): number {
  return Math.floor((toMs(iso) - Date.now()) / 1000);
}

export function fmtCountdown(seconds: number): string {
  if (seconds <= 0) return "expired";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? "null";
  } catch {
    return String(v);
  }
}

/** First string value in an args object — used as the destructive-confirm hint. */
export function firstStringArg(args: Record<string, unknown> | null | undefined): string | null {
  if (!args) return null;
  for (const v of Object.values(args)) {
    if (typeof v === "string" && v.length > 0) return v;
  }
  return null;
}
