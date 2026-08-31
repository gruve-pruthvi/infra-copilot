// Auth state for the local operator session.
//
// The backend enforces auth only once a PIN is configured, so `configured`
// drives whether the UI shows a login gate at all.
//
// The session token is deliberately NOT stored in JS-reachable storage: the
// httpOnly cookie set by the backend authenticates every same-origin fetch,
// SSE stream and websocket on its own. Mirroring it into localStorage would
// negate httpOnly, and putting it in a websocket query string would leak a
// live credential into proxy and access logs. (The Bearer header the backend
// also accepts is for non-browser clients such as the test suite and CLI.)

import { create } from "zustand";
import { api, ApiError } from "../api/client";

export type Role = "viewer" | "operator" | "admin";

export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
  role: Role;
  roles: Role[];
  default_role: Role;
}

interface AuthState {
  status: AuthStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  setup: (pin: string, defaultRole: Role) => Promise<void>;
  login: (pin: string) => Promise<void>;
  logout: () => Promise<void>;
  elevate: (pin: string) => Promise<void>;
  /** Called by the API layer when the backend rejects a token. */
  onUnauthorized: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: null,
  loading: true,
  error: null,

  refresh: async () => {
    try {
      const status = await api.get<AuthStatus>("/api/auth/status");
      set({ status, loading: false, error: null });
    } catch (e) {
      // A backend that is merely down must not look like a logged-out state.
      set({ loading: false, error: e instanceof ApiError ? e.message : String(e) });
    }
  },

  setup: async (pin, defaultRole) => {
    await api.post("/api/auth/setup", { pin, default_role: defaultRole });
    await get().refresh();
  },

  login: async (pin) => {
    await api.post("/api/auth/login", { pin });
    await get().refresh();
  },

  logout: async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      await get().refresh();
    }
  },

  elevate: async (pin) => {
    await api.post("/api/auth/elevate", { pin });
    await get().refresh();
  },

  onUnauthorized: () => {
    const status = get().status;
    if (status) set({ status: { ...status, authenticated: false, role: "viewer" } });
  },
}));

/** True when the current role may invoke tools of the given tier. */
export function roleAllows(role: Role | undefined, tier: "read" | "mutating" | "destructive"): boolean {
  if (tier === "read") return true;
  if (tier === "mutating") return role === "operator" || role === "admin";
  return role === "admin";
}
