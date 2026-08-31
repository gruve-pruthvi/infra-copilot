// Sidebar role indicator + PIN elevation prompt.

import { useState } from "react";
import { errorMessage } from "../api/client";
import { useAuthStore } from "../stores/authStore";

const TONE: Record<string, string> = {
  admin: "border-danger/50 bg-danger/10 text-danger",
  operator: "border-accent/50 bg-accent/10 text-accent",
  viewer: "border-edge bg-raise text-dim",
};

export function RoleBadge() {
  const status = useAuthStore((s) => s.status);
  const elevate = useAuthStore((s) => s.elevate);
  const logout = useAuthStore((s) => s.logout);

  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!status?.configured) return null;

  const role = status.role;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await elevate(pin);
      setOpen(false);
      setPin("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5 border-t border-edge px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-widest text-faint">role</span>
        <span
          className={`ml-auto rounded-full border px-1.5 font-mono text-[10px] ${
            TONE[role] ?? TONE.viewer
          }`}
        >
          {role}
        </span>
      </div>

      {role !== "admin" && !open && (
        <button
          onClick={() => setOpen(true)}
          className="w-full rounded border border-edge px-2 py-1 text-left font-mono text-[10px] text-dim hover:bg-raise hover:text-fg"
        >
          elevate to admin…
        </button>
      )}

      {open && (
        <form onSubmit={submit} className="space-y-1">
          <input
            type="password"
            autoFocus
            placeholder="PIN"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            className="w-full rounded border border-edge bg-raise px-2 py-1 font-mono text-[11px] text-fg outline-none focus:border-accent"
          />
          {error && <div className="text-[10px] text-danger">{error}</div>}
          <div className="flex gap-1">
            <button
              type="submit"
              disabled={busy || !pin}
              className="flex-1 rounded bg-accent px-2 py-1 font-mono text-[10px] text-bg disabled:opacity-40"
            >
              elevate
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setPin("");
                setError(null);
              }}
              className="rounded border border-edge px-2 py-1 font-mono text-[10px] text-dim hover:text-fg"
            >
              cancel
            </button>
          </div>
        </form>
      )}

      <button
        onClick={() => void logout()}
        className="w-full text-left font-mono text-[10px] text-faint hover:text-fg"
      >
        log out
      </button>
    </div>
  );
}
