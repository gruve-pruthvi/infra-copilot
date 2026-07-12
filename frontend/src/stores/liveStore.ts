// Global live-data store fed by the /ws/events WebSocket plus the /healthz
// poll. Holds the latest snapshots so every page renders from one source.

import { create } from "zustand";
import type {
  ActionRecord,
  ContainersSnapshot,
  HealthView,
  HistoryPoint,
  K8sSnapshot,
  PendingApproval,
  SystemMetrics,
} from "../api/types";
import { toMs } from "../lib/format";
import { useChatStore } from "./chatStore";

const HISTORY_WINDOW_MS = 60 * 60 * 1000; // keep ~60 min of metric points

interface LiveState {
  wsConnected: boolean;
  containers: ContainersSnapshot | null;
  system: SystemMetrics | null;
  systemHistory: HistoryPoint[];
  historySeeded: boolean;
  k8s: K8sSnapshot | null;
  pendingApprovals: PendingApproval[];
  liveActions: ActionRecord[];
  sessionStatus: Record<string, string>;
  health: HealthView | null;
  toastApproval: PendingApproval | null;

  handleFrame: (topic: string, payload: unknown) => void;
  seedSystemHistory: (points: HistoryPoint[]) => void;
  setHealth: (h: HealthView) => void;
  removePendingApproval: (id: string) => void;
  dismissToast: () => void;
}

function trimHistory(points: HistoryPoint[]): HistoryPoint[] {
  const cutoff = Date.now() - HISTORY_WINDOW_MS;
  const kept = points.filter((p) => {
    const ms = toMs(p.ts);
    return Number.isNaN(ms) || ms >= cutoff;
  });
  return kept.length > 1500 ? kept.slice(kept.length - 1500) : kept;
}

export const useLiveStore = create<LiveState>((set) => ({
  wsConnected: false,
  containers: null,
  system: null,
  systemHistory: [],
  historySeeded: false,
  k8s: null,
  pendingApprovals: [],
  liveActions: [],
  sessionStatus: {},
  health: null,
  toastApproval: null,

  handleFrame: (topic, payload) => {
    switch (topic) {
      case "__open":
        set({ wsConnected: true });
        break;
      case "__close":
        set({ wsConnected: false });
        break;
      case "containers_snapshot":
        set({ containers: payload as ContainersSnapshot });
        break;
      case "system_metrics": {
        const m = payload as SystemMetrics;
        set((s) => ({
          system: m,
          systemHistory: trimHistory([
            ...s.systemHistory,
            { ts: m.ts, cpu_percent: m.cpu_percent, memory_percent: m.memory_percent },
          ]),
        }));
        break;
      }
      case "k8s_snapshot":
        set({ k8s: payload as K8sSnapshot });
        break;
      case "action_logged": {
        const a = payload as ActionRecord;
        set((s) =>
          s.liveActions.some((x) => x.id === a.id)
            ? s
            : { liveActions: [a, ...s.liveActions].slice(0, 200) },
        );
        break;
      }
      case "approval_pending": {
        const a = payload as PendingApproval;
        set((s) =>
          s.pendingApprovals.some((x) => x.id === a.id)
            ? s
            : { pendingApprovals: [...s.pendingApprovals, a], toastApproval: a },
        );
        break;
      }
      case "approval_resolved": {
        const r = payload as { id: string; status: string; session_id: string };
        set((s) => ({
          pendingApprovals: s.pendingApprovals.filter((x) => x.id !== r.id),
          toastApproval: s.toastApproval?.id === r.id ? null : s.toastApproval,
        }));
        useChatStore.getState().onApprovalResolved(r.id, r.status);
        break;
      }
      case "session_status": {
        const p = payload as { session_id: string; status: string };
        set((s) => ({ sessionStatus: { ...s.sessionStatus, [p.session_id]: p.status } }));
        break;
      }
      default:
        break; // unknown topics ignored
    }
  },

  seedSystemHistory: (points) =>
    set((s) => {
      if (s.historySeeded) return s;
      // Prepend seeded points that predate anything we already accumulated.
      const earliestLive = s.systemHistory.length ? toMs(s.systemHistory[0].ts) : Infinity;
      const older = points.filter((p) => toMs(p.ts) < earliestLive);
      return { historySeeded: true, systemHistory: trimHistory([...older, ...s.systemHistory]) };
    }),

  setHealth: (h) => set({ health: h }),
  removePendingApproval: (id) =>
    set((s) => ({
      pendingApprovals: s.pendingApprovals.filter((x) => x.id !== id),
      toastApproval: s.toastApproval?.id === id ? null : s.toastApproval,
    })),
  dismissToast: () => set({ toastApproval: null }),
}));
