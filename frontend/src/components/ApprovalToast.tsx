// Global toast shown when a new approval_pending arrives on the event feed.

import { useNavigate } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import { useLiveStore } from "../stores/liveStore";
import { TierBadge } from "./TierBadge";

export function ApprovalToast() {
  const approval = useLiveStore((s) => s.toastApproval);
  const dismiss = useLiveStore((s) => s.dismissToast);
  const navigate = useNavigate();

  if (!approval) return null;

  const review = () => {
    dismiss();
    void useChatStore.getState().select(approval.session_id);
    navigate("/");
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 rounded-md border border-warn/50 bg-panel shadow-lg shadow-black/50">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
        <span className="font-mono text-[10px] uppercase tracking-widest text-warn">
          approval pending
        </span>
        <TierBadge tier={approval.tier} />
        <button
          type="button"
          onClick={dismiss}
          className="ml-auto text-faint hover:text-fg"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>
      <div className="px-3 py-2.5">
        <div className="mb-1 font-mono text-xs text-fg">
          {approval.server}
          <span className="text-faint">.</span>
          {approval.tool}
        </div>
        <p className="mb-2 line-clamp-2 text-xs leading-relaxed text-dim">{approval.plan_summary}</p>
        <button
          type="button"
          onClick={review}
          className="rounded border border-accent/50 bg-accent/10 px-2.5 py-1 text-xs text-accent hover:bg-accent/20"
        >
          Review in chat
        </button>
      </div>
    </div>
  );
}
