// Global toast queue shown when approval_pending events arrive on the event
// feed. Actionable from any page: embeds PlanApprovalCard so the user can
// approve/reject without navigating into the owning chat session.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import { useLiveStore } from "../stores/liveStore";
import { PlanApprovalCard } from "./chat/PlanApprovalCard";

export function ApprovalToast() {
  const queue = useLiveStore((s) => s.toastQueue);
  const dismiss = useLiveStore((s) => s.dismissToast);
  const navigate = useNavigate();
  // Optimistic local resolution so the embedded card reflects the outcome
  // immediately, ahead of the WS approval_resolved frame that actually
  // removes the item from toastQueue.
  const [resolvedIds, setResolvedIds] = useState<Record<string, "approved" | "rejected">>({});

  if (queue.length === 0) return null;
  const approval = queue[0];
  const extra = queue.length - 1;

  const review = () => {
    dismiss(approval.id);
    void useChatStore.getState().select(approval.session_id);
    navigate("/");
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 space-y-1">
      {extra > 0 && (
        <div className="text-right font-mono text-[10px] text-faint">+{extra} more pending</div>
      )}
      <div className="rounded-md border border-warn/50 bg-panel shadow-lg shadow-black/50">
        <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
          <button
            type="button"
            onClick={review}
            className="font-mono text-[10px] uppercase tracking-widest text-accent hover:underline"
          >
            Review in chat
          </button>
          <button
            type="button"
            onClick={() => dismiss(approval.id)}
            className="ml-auto text-faint hover:text-fg"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto p-2">
          <PlanApprovalCard
            approval={approval}
            resolved={resolvedIds[approval.id]}
            onApprove={async (confirmText) => {
              const err = await useChatStore.getState().approve(approval.id, confirmText);
              if (!err) setResolvedIds((m) => ({ ...m, [approval.id]: "approved" }));
              return err;
            }}
            onReject={async () => {
              const err = await useChatStore.getState().reject(approval.id);
              if (!err) setResolvedIds((m) => ({ ...m, [approval.id]: "rejected" }));
              return err;
            }}
          />
        </div>
      </div>
    </div>
  );
}
