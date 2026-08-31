// Login gate. Doubles as first-run setup when no PIN is configured yet.

import { useState } from "react";
import { errorMessage } from "../api/client";
import { useAuthStore, type Role } from "../stores/authStore";

const ROLE_HELP: Record<Role, string> = {
  viewer: "Read-only. Can inspect everything, change nothing.",
  operator: "Can start, stop and deploy. Cannot delete or run destructive tools.",
  admin: "Full access, including destructive actions.",
};

export function LoginPage({ configured }: { configured: boolean }) {
  const setup = useAuthStore((s) => s.setup);
  const login = useAuthStore((s) => s.login);

  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [role, setRole] = useState<Role>("admin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSetup = !configured;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (isSetup && pin !== confirm) {
      setError("PINs do not match");
      return;
    }
    if (isSetup && pin.length < 4) {
      setError("PIN must be at least 4 characters");
      return;
    }
    setBusy(true);
    try {
      if (isSetup) await setup(pin, role);
      else await login(pin);
    } catch (err) {
      setError(errorMessage(err));
      setPin("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-bg p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-edge bg-panel p-6"
      >
        <div>
          <div className="font-mono text-sm font-semibold tracking-tight text-fg">
            infra<span className="text-accent">-copilot</span>
          </div>
          <div className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-faint">
            {isSetup ? "first-run setup" : "locked"}
          </div>
        </div>

        {isSetup && (
          <p className="text-xs leading-relaxed text-dim">
            Set a PIN to protect this instance. Until you do, anyone who can reach this port can
            control your Docker and Kubernetes.
          </p>
        )}

        <label className="block space-y-1">
          <span className="font-mono text-[10px] uppercase tracking-widest text-faint">PIN</span>
          <input
            type="password"
            autoFocus
            autoComplete={isSetup ? "new-password" : "current-password"}
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            className="w-full rounded border border-edge bg-raise px-3 py-2 font-mono text-sm text-fg outline-none focus:border-accent"
          />
        </label>

        {isSetup && (
          <>
            <label className="block space-y-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-faint">
                Confirm PIN
              </span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="w-full rounded border border-edge bg-raise px-3 py-2 font-mono text-sm text-fg outline-none focus:border-accent"
              />
            </label>

            <label className="block space-y-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-faint">
                Default role
              </span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="w-full rounded border border-edge bg-raise px-3 py-2 text-sm text-fg outline-none focus:border-accent"
              >
                <option value="admin">admin</option>
                <option value="operator">operator</option>
                <option value="viewer">viewer</option>
              </select>
              <span className="block text-[11px] leading-relaxed text-faint">{ROLE_HELP[role]}</span>
            </label>
          </>
        )}

        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !pin}
          className="w-full rounded bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-40"
        >
          {busy ? "…" : isSetup ? "Set PIN & continue" : "Unlock"}
        </button>
      </form>
    </div>
  );
}
