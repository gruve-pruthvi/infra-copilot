// Collapsible card for a tool invocation inside an assistant message.

import { useState } from "react";
import type { ToolCallRecord } from "../../api/types";
import { prettyJson } from "../../lib/format";
import { TierBadge } from "../TierBadge";

export function ToolCallCard({ call }: { call: ToolCallRecord }) {
  const [open, setOpen] = useState(false);
  const done = call.result_summary !== undefined && call.result_summary !== null;
  return (
    <div className="rounded border border-edge bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-raise"
      >
        <span className="font-mono text-[11px] text-faint">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-xs text-fg">
          {call.server}
          <span className="text-faint">.</span>
          {call.tool}
        </span>
        <TierBadge tier={call.tier} />
        <span className="ml-auto font-mono text-[11px] text-faint">
          {done ? "done" : "running…"}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-edge px-2.5 py-2">
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-faint">args</div>
            <pre className="overflow-x-auto rounded bg-ink p-2 font-mono text-[12px] leading-relaxed text-dim">
              {prettyJson(call.args)}
            </pre>
          </div>
          {done && (
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-faint">result</div>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-ink p-2 font-mono text-[12px] leading-relaxed text-fg">
                {call.result_summary}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
