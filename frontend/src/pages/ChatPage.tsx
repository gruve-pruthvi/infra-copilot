import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Markdownish } from "../components/Markdownish";
import { PlanApprovalCard } from "../components/chat/PlanApprovalCard";
import { ToolCallCard } from "../components/chat/ToolCallCard";
import { useChatStore, type TranscriptItem } from "../stores/chatStore";
import { useLiveStore } from "../stores/liveStore";
import { fmtDateTime } from "../lib/format";

function SessionList() {
  const sessions = useChatStore((s) => s.sessions);
  const sessionsError = useChatStore((s) => s.sessionsError);
  const currentId = useChatStore((s) => s.currentId);
  const select = useChatStore((s) => s.select);
  const createSession = useChatStore((s) => s.createSession);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const sessionStatus = useLiveStore((s) => s.sessionStatus);

  return (
    <div className="flex w-60 shrink-0 flex-col border-r border-edge bg-panel/50">
      <div className="border-b border-edge p-2">
        <button
          type="button"
          onClick={() => void createSession()}
          className="w-full rounded border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
        >
          + New session
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-1.5">
        {sessionsError && (
          <div className="m-1 rounded border border-danger/40 bg-danger/10 p-2 text-xs text-danger">
            {sessionsError}
          </div>
        )}
        {sessions.length === 0 && !sessionsError && (
          <div className="p-3 text-center text-xs text-faint">No sessions yet</div>
        )}
        {sessions.map((s) => {
          const running = sessionStatus[s.id] === "running" || s.status === "running";
          return (
            <div
              key={s.id}
              className={`group flex cursor-pointer items-center gap-2 rounded px-2.5 py-2 ${
                s.id === currentId ? "bg-raise" : "hover:bg-raise/60"
              }`}
              onClick={() => void select(s.id)}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs text-fg">{s.title || "Untitled session"}</div>
                <div className="truncate font-mono text-[10px] text-faint">
                  {fmtDateTime(s.updated_at)}
                </div>
              </div>
              {running && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent" />}
              <button
                type="button"
                aria-label="Delete session"
                onClick={(e) => {
                  e.stopPropagation();
                  void deleteSession(s.id);
                }}
                className="hidden shrink-0 rounded px-1 text-faint hover:text-danger group-hover:block"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TranscriptItemView({ item }: { item: TranscriptItem }) {
  const approve = useChatStore((s) => s.approve);
  const reject = useChatStore((s) => s.reject);
  const removePending = useLiveStore((s) => s.removePendingApproval);

  switch (item.kind) {
    case "user":
      return (
        <div className="ml-auto max-w-[75%] rounded-md rounded-br-sm border border-edge2 bg-raise px-3 py-2">
          <p className="whitespace-pre-wrap break-words leading-relaxed text-fg">{item.content}</p>
        </div>
      );
    case "assistant":
      return (
        <div className="max-w-[85%] space-y-2">
          {item.toolCalls.map((tc) => (
            <ToolCallCard key={tc.tool_call_id} call={tc} />
          ))}
          {item.content && (
            <div className="rounded-md rounded-bl-sm border border-edge bg-panel px-3 py-2">
              <Markdownish text={item.content} />
            </div>
          )}
        </div>
      );
    case "approval":
      return (
        <div className="max-w-[85%]">
          <PlanApprovalCard
            approval={item.approval}
            resolved={item.resolved}
            onApprove={async (confirmText) => {
              const err = await approve(item.approval, confirmText);
              if (!err) removePending(item.approval.id);
              return err;
            }}
            onReject={async () => {
              const err = await reject(item.approval);
              if (!err) removePending(item.approval.id);
              return err;
            }}
          />
        </div>
      );
    case "error":
      return (
        <div className="max-w-[85%] rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {item.message}
        </div>
      );
  }
}

function BatchApprovalBar() {
  const items = useChatStore((s) => s.items);
  const batchResolve = useChatStore((s) => s.batchResolve);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pending = items.filter(
    (it): it is Extract<TranscriptItem, { kind: "approval" }> =>
      it.kind === "approval" && !it.resolved,
  );
  if (pending.length < 2) return null;

  const nonDestructiveIds = pending
    .filter((it) => it.approval.tier !== "destructive")
    .map((it) => it.approval.id);
  const allIds = pending.map((it) => it.approval.id);

  const run = async (approve: string[], reject: string[]) => {
    setBusy(true);
    setError(null);
    const err = await batchResolve(approve, reject);
    if (err) setError(err);
    setBusy(false);
  };

  return (
    <div className="mx-3 mb-2 flex flex-wrap items-center gap-2 rounded border border-edge bg-panel px-3 py-2 text-xs">
      <span className="text-dim">
        {pending.length} approvals pending
        {nonDestructiveIds.length < pending.length && (
          <span className="text-faint"> — destructive ones need individual confirmation</span>
        )}
      </span>
      {error && <span className="text-danger">{error}</span>}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {nonDestructiveIds.length > 0 && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(nonDestructiveIds, [])}
            className="rounded border border-ok/50 bg-ok/10 px-2 py-1 text-xs text-ok hover:bg-ok/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Approve all non-destructive ({nonDestructiveIds.length})
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => void run([], allIds)}
          className="rounded border border-danger/50 bg-danger/10 px-2 py-1 text-xs text-danger hover:bg-danger/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Reject all ({allIds.length})
        </button>
      </div>
    </div>
  );
}

function Composer() {
  const draft = useChatStore((s) => s.draft);
  const setDraft = useChatStore((s) => s.setDraft);
  const status = useChatStore((s) => s.status);
  const send = useChatStore((s) => s.send);
  const cancel = useChatStore((s) => s.cancel);

  const busy = status === "running" || status === "loading";
  const canSend = !busy && draft.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    void send(draft.trim());
  };

  return (
    <div className="border-t border-edge bg-panel p-3">
      {status === "paused" && (
        <div className="mb-2 rounded border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs text-warn">
          Run paused — waiting on approval above.
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={busy}
          rows={2}
          placeholder={busy ? "Running…" : "Ask about your infrastructure… (Enter to send)"}
          className="min-h-[42px] flex-1 resize-y rounded border border-edge bg-ink px-3 py-2 text-sm text-fg outline-none placeholder:text-faint focus:border-accent/50 disabled:opacity-50"
        />
        {status === "running" ? (
          <button
            type="button"
            onClick={() => void cancel()}
            className="rounded border border-danger/50 bg-danger/10 px-4 py-2 text-sm text-danger hover:bg-danger/20"
          >
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            className="rounded border border-accent/50 bg-accent/10 px-4 py-2 text-sm font-medium text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}

export function ChatPage() {
  const items = useChatStore((s) => s.items);
  const status = useChatStore((s) => s.status);
  const currentId = useChatStore((s) => s.currentId);
  const loadError = useChatStore((s) => s.loadError);
  const usage = useChatStore((s) => s.usage);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const setDraft = useChatStore((s) => s.setDraft);

  const location = useLocation();
  const navigate = useNavigate();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // Draft handoff from Dashboard's "Ask copilot"
  useEffect(() => {
    const draft = (location.state as { draft?: string } | null)?.draft;
    if (draft) {
      setDraft(draft);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate, setDraft]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items, status]);

  return (
    <div className="flex h-full min-h-0">
      <SessionList />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-3 p-4">
            {loadError && (
              <div className="rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
                Failed to load session: {loadError}
              </div>
            )}
            {!currentId && !loadError && (
              <div className="mt-16 text-center">
                <div className="font-mono text-sm text-dim">No session selected.</div>
                <div className="mt-1 text-xs text-faint">
                  Create a session or just start typing below — one will be created for you.
                </div>
              </div>
            )}
            {status === "loading" && (
              <div className="text-center font-mono text-xs text-faint">loading transcript…</div>
            )}
            {items.map((item) => (
              <TranscriptItemView key={item.id} item={item} />
            ))}
            {status === "running" && (
              <div className="font-mono text-xs text-accent">
                <span className="animate-pulse">▍</span> thinking…
              </div>
            )}
            {usage && status === "idle" && (
              <div className="text-right font-mono text-[10px] text-faint">
                {usage.input_tokens} in / {usage.output_tokens} out · {usage.requests} req
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>
        <BatchApprovalBar />
        <Composer />
      </div>
    </div>
  );
}
