// Chat session state: session list, active transcript, SSE streaming, and the
// approve/reject flow. Streaming events mutate the transcript incrementally.

import { create } from "zustand";
import { api, errorMessage } from "../api/client";
import { streamSSE } from "../api/sse";
import type {
  ChatEvent,
  PendingApproval,
  SessionDetail,
  SessionSummary,
  ToolCallRecord,
  Usage,
} from "../api/types";

export type TranscriptItem =
  | { kind: "user"; id: string; content: string }
  | { kind: "assistant"; id: string; content: string; toolCalls: ToolCallRecord[] }
  | { kind: "approval"; id: string; approval: PendingApproval; resolved?: "approved" | "rejected" }
  | { kind: "error"; id: string; message: string };

export type ChatStatus = "idle" | "loading" | "running" | "paused";

interface ChatState {
  sessions: SessionSummary[];
  sessionsError: string | null;
  currentId: string | null;
  items: TranscriptItem[];
  status: ChatStatus;
  loadError: string | null;
  usage: Usage | null;
  draft: string;

  setDraft: (d: string) => void;
  loadSessions: () => Promise<void>;
  createSession: (title?: string) => Promise<string | null>;
  deleteSession: (id: string) => Promise<void>;
  select: (id: string) => Promise<void>;
  send: (content: string) => Promise<void>;
  cancel: () => Promise<void>;
  attachStream: () => Promise<void>;
  approve: (approval: { id: string; session_id: string }, confirmText?: string) => Promise<string | null>;
  reject: (approval: { id: string; session_id: string }, reason?: string) => Promise<string | null>;
  batchResolve: (
    approve: string[],
    reject: string[],
    confirmTexts?: Record<string, string>,
  ) => Promise<string | null>;
  onApprovalResolved: (approvalId: string, status: string) => void;
}

let seq = 0;
const uid = () => `local-${Date.now()}-${++seq}`;
let abortCtrl: AbortController | null = null;

export const useChatStore = create<ChatState>((set, get) => {
  // id of the assistant bubble currently receiving stream deltas
  let streamingItemId: string | null = null;

  const patchItems = (fn: (items: TranscriptItem[]) => TranscriptItem[]) =>
    set((s) => ({ items: fn(s.items) }));

  const ensureAssistantItem = (): string => {
    if (streamingItemId) return streamingItemId;
    const id = uid();
    streamingItemId = id;
    patchItems((items) => [...items, { kind: "assistant", id, content: "", toolCalls: [] }]);
    return id;
  };

  const markApproval = (approvalId: string, resolved: "approved" | "rejected") =>
    patchItems((items) =>
      items.map((it) =>
        it.kind === "approval" && it.approval.id === approvalId ? { ...it, resolved } : it,
      ),
    );

  const handleEvent = (sessionId: string, ev: ChatEvent) => {
    if (get().currentId !== sessionId) return; // stale stream after switching sessions
    switch (ev.type) {
      case "status":
        break;
      case "text_delta": {
        const id = ensureAssistantItem();
        patchItems((items) =>
          items.map((it) =>
            it.id === id && it.kind === "assistant" ? { ...it, content: it.content + ev.text } : it,
          ),
        );
        break;
      }
      case "tool_call_started": {
        const id = ensureAssistantItem();
        const call: ToolCallRecord = {
          tool: ev.tool,
          server: ev.server,
          tier: ev.tier,
          args: ev.args,
          tool_call_id: ev.tool_call_id,
        };
        patchItems((items) =>
          items.map((it) =>
            it.id === id && it.kind === "assistant"
              ? { ...it, toolCalls: [...it.toolCalls, call] }
              : it,
          ),
        );
        break;
      }
      case "tool_result":
        patchItems((items) =>
          items.map((it) =>
            it.kind === "assistant" && it.toolCalls.some((tc) => tc.tool_call_id === ev.tool_call_id)
              ? {
                  ...it,
                  toolCalls: it.toolCalls.map((tc) =>
                    tc.tool_call_id === ev.tool_call_id
                      ? { ...tc, result_summary: ev.result_summary }
                      : tc,
                  ),
                }
              : it,
          ),
        );
        break;
      case "approval_required":
        streamingItemId = null; // post-approval text goes to a fresh bubble
        patchItems((items) =>
          items.some((it) => it.kind === "approval" && it.approval.id === ev.approval.id)
            ? items
            : [...items, { kind: "approval", id: ev.approval.id, approval: ev.approval }],
        );
        break;
      case "turn_paused":
        set({ status: "paused" });
        break;
      case "done":
        set({ status: "idle", usage: ev.usage });
        break;
      case "error":
        patchItems((items) => [...items, { kind: "error", id: uid(), message: ev.message }]);
        set({ status: "idle" });
        break;
    }
  };

  const runStream = async (sessionId: string, path: string, init?: RequestInit) => {
    abortCtrl?.abort();
    const ctrl = new AbortController();
    abortCtrl = ctrl;
    streamingItemId = null;
    set({ status: "running" });
    await streamSSE(path, init, (ev) => handleEvent(sessionId, ev), ctrl.signal);
    // Stream closed without a terminal event (network drop, cancel, etc.)
    if (get().currentId === sessionId && get().status === "running" && abortCtrl === ctrl) {
      set({ status: "idle" });
    }
  };

  return {
    sessions: [],
    sessionsError: null,
    currentId: null,
    items: [],
    status: "idle",
    loadError: null,
    usage: null,
    draft: "",

    setDraft: (d) => set({ draft: d }),

    loadSessions: async () => {
      try {
        const sessions = await api.get<SessionSummary[]>("/api/sessions");
        set({ sessions, sessionsError: null });
      } catch (e) {
        set({ sessionsError: errorMessage(e) });
      }
    },

    createSession: async (title) => {
      try {
        const s = await api.post<SessionSummary>("/api/sessions", title ? { title } : {});
        abortCtrl?.abort();
        streamingItemId = null;
        set((st) => ({
          sessions: [s, ...st.sessions],
          currentId: s.id,
          items: [],
          status: "idle",
          usage: null,
          loadError: null,
        }));
        return s.id;
      } catch (e) {
        set({ sessionsError: errorMessage(e) });
        return null;
      }
    },

    deleteSession: async (id) => {
      try {
        await api.delete(`/api/sessions/${id}`);
      } catch {
        // remove locally regardless; a refresh re-syncs
      }
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== id),
        ...(s.currentId === id
          ? { currentId: null, items: [], status: "idle" as ChatStatus, usage: null }
          : {}),
      }));
    },

    select: async (id) => {
      if (get().currentId === id) return;
      abortCtrl?.abort();
      streamingItemId = null;
      set({ currentId: id, items: [], status: "loading", loadError: null, usage: null });
      try {
        const d = await api.get<SessionDetail>(`/api/sessions/${id}`);
        if (get().currentId !== id) return;
        const items: TranscriptItem[] = [];
        for (const m of d.messages ?? []) {
          if (m.role === "user") {
            items.push({ kind: "user", id: String(m.id), content: m.content });
          } else {
            items.push({
              kind: "assistant",
              id: String(m.id),
              content: m.content ?? "",
              toolCalls: m.tool_calls ?? [],
            });
          }
        }
        for (const a of d.pending_approvals ?? []) {
          items.push({
            kind: "approval",
            id: a.id,
            approval: { ...a, session_id: a.session_id ?? id },
          });
        }
        const hasPending = (d.pending_approvals ?? []).length > 0;
        set({ items, status: hasPending ? "paused" : "idle" });
        // If the session is mid-run server-side, attach to watch it.
        if (d.status === "running") void get().attachStream();
      } catch (e) {
        if (get().currentId === id) set({ status: "idle", loadError: errorMessage(e) });
      }
    },

    send: async (content) => {
      let id = get().currentId;
      if (!id) {
        id = await get().createSession(content.slice(0, 60));
        if (!id) return;
      }
      set((s) => ({ items: [...s.items, { kind: "user", id: uid(), content }], draft: "" }));
      await runStream(id, `/api/sessions/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
        headers: { "Content-Type": "application/json" },
      });
      void get().loadSessions(); // pick up title/updated_at changes
    },

    cancel: async () => {
      const id = get().currentId;
      if (!id) return;
      try {
        await api.post(`/api/sessions/${id}/cancel`);
      } catch {
        // stream teardown will settle status
      }
    },

    attachStream: async () => {
      const id = get().currentId;
      if (!id) return;
      await runStream(id, `/api/sessions/${id}/stream`);
    },

    approve: async (approval, confirmText) => {
      try {
        const res = await api.post<{ resumed: boolean; waiting_on?: number }>(
          `/api/approvals/${approval.id}/approve`,
          confirmText ? { confirm_text: confirmText } : {},
        );
        if (approval.session_id === get().currentId) markApproval(approval.id, "approved");
        if (res.resumed && approval.session_id === get().currentId) void get().attachStream();
        return null;
      } catch (e) {
        return errorMessage(e);
      }
    },

    reject: async (approval, reason) => {
      try {
        const res = await api.post<{ resumed: boolean }>(
          `/api/approvals/${approval.id}/reject`,
          reason ? { reason } : {},
        );
        if (approval.session_id === get().currentId) markApproval(approval.id, "rejected");
        if (res.resumed && approval.session_id === get().currentId) void get().attachStream();
        return null;
      } catch (e) {
        return errorMessage(e);
      }
    },

    batchResolve: async (approve, reject, confirmTexts = {}) => {
      const sessionId = get().currentId;
      try {
        const res = await api.post<{
          resumed: boolean;
          results: Record<string, { ok: boolean; error?: string }>;
        }>("/api/approvals/batch", { approve, reject, confirm_texts: confirmTexts });
        for (const id of approve) if (res.results[id]?.ok) get().onApprovalResolved(id, "approved");
        for (const id of reject) if (res.results[id]?.ok) get().onApprovalResolved(id, "rejected");
        if (res.resumed && sessionId === get().currentId) void get().attachStream();
        return null;
      } catch (e) {
        return errorMessage(e);
      }
    },

    onApprovalResolved: (approvalId, status) => {
      markApproval(approvalId, status === "approved" ? "approved" : "rejected");
    },
  };
});
