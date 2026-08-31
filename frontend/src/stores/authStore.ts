// Auth state for the local operator session.
//
// The backend enforces auth only once a PIN is configured, so `configured`
// drives whether the UI shows a login gate at all. The token also lives in
// localStorage: the httpOnly cookie covers same-origin fetches, but the
// websocket helper needs to pass ?token= explicitly.

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

const TOKEN_KEY = "infra_copilot_token";

export function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setStoredToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // private mode / storage disabled — the cookie still covers fetches
  }
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
    const res = await api.post<{ token: string }>("/api/auth/setup", {
      pin,
      default_role: defaultRole,
    });
    setStoredToken(res.token);
    await get().refresh();
  },

  login: async (pin) => {
    const res = await api.post<{ token: string }>("/api/auth/login", { pin });
    setStoredToken(res.token);
    await get().refresh();
  },

  logout: async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setStoredToken(null);
      await get().refresh();
    }
  },

  elevate: async (pin) => {
    await api.post("/api/auth/elevate", { pin });
    await get().refresh();
  },

  onUnauthorized: () => {
    setStoredToken(null);
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
