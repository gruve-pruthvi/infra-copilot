// Live container log tail over WS /ws/logs/{id} (the server replays a
// ~200-line backlog on connect). Scrollback capped at 5000 lines.

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { connectLogTail } from "../api/ws";
import { StatusDot } from "../components/StatusDot";
import { useLiveStore } from "../stores/liveStore";

const MAX_LINES = 5000;

// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const cleanLine = (s: string) => s.replace(ANSI_RE, "").replace(/\r/g, "");

/**
 * Reassembles log lines from WS frames. Non-TTY containers arrive one full
 * line per frame; TTY containers arrive byte-by-byte (docker-py streams raw
 * TTY output with chunk_size=1) with the newline frame rstripped to "".
 * Rule: empty frame or embedded \n flushes; single-char frames accumulate;
 * multi-char frames are complete lines.
 */
function makeLineAssembler(push: (line: string) => void) {
  let partial = "";
  const flush = () => {
    const line = cleanLine(partial);
    partial = "";
    if (line.trim().length > 0) push(line);
  };
  return (frame: string) => {
    if (frame === "" || frame === "\n") {
      flush();
    } else if (frame.length === 1) {
      partial += frame;
    } else {
      const segments = (partial + frame).split("\n");
      partial = "";
      for (const seg of segments) {
        const line = cleanLine(seg);
        if (line.trim().length > 0) push(line);
      }
    }
  };
}

export function LogsPage() {
  const containersSnapshot = useLiveStore((s) => s.containers);
  const [params, setParams] = useSearchParams();
  const selected = params.get("container") ?? "";

  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const [streamStatus, setStreamStatus] = useState<"idle" | "open" | "closed" | "error">("idle");

  const scrollRef = useRef<HTMLDivElement>(null);
  const bufferRef = useRef<string[]>([]);
  const followRef = useRef(follow);
  followRef.current = follow;

  const containers = containersSnapshot?.containers ?? [];

  // Tail lifecycle: REST seed then WS append, batched via a 200ms flush timer
  // so a chatty container does not re-render per line.
  useEffect(() => {
    setLines([]);
    bufferRef.current = [];
    setStreamStatus("idle");
    if (!selected) return;

    let cancelled = false;

    // No REST seed needed: the WS tail already replays a ~200-line backlog.
    const assemble = makeLineAssembler((line) => bufferRef.current.push(line));
    const closeWs = connectLogTail(selected, assemble, (st) => {
      if (!cancelled) setStreamStatus(st);
    });

    const flush = setInterval(() => {
      if (bufferRef.current.length === 0) return;
      const batch = bufferRef.current;
      bufferRef.current = [];
      setLines((prev) => [...prev, ...batch].slice(-MAX_LINES));
    }, 200);

    return () => {
      cancelled = true;
      clearInterval(flush);
      closeWs();
    };
  }, [selected]);

  useEffect(() => {
    if (followRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  const shown = useMemo(() => {
    if (!filter) return lines;
    const f = filter.toLowerCase();
    return lines.filter((l) => l.toLowerCase().includes(f));
  }, [lines, filter]);

  const selectedContainer = containers.find((c) => c.id === selected || c.name === selected);

  return (
    <div className="flex h-full flex-col p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <select
          value={selected}
          onChange={(e) => setParams(e.target.value ? { container: e.target.value } : {})}
          className="rounded border border-edge bg-panel px-2.5 py-1.5 font-mono text-xs text-fg outline-none focus:border-accent/50"
        >
          <option value="">— select container —</option>
          {containers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.status})
            </option>
          ))}
        </select>
        {selectedContainer && <StatusDot status={selectedContainer.status} />}
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter…"
          className="w-56 rounded border border-edge bg-panel px-2.5 py-1.5 font-mono text-xs text-fg outline-none placeholder:text-faint focus:border-accent/50"
        />
        <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-dim">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
            className="accent-[#22d3ee]"
          />
          Follow
        </label>
        <span className="ml-auto font-mono text-[10px] text-faint">
          {shown.length}/{lines.length} lines
          {streamStatus === "open" && <span className="ml-2 text-ok">● live</span>}
          {streamStatus === "closed" && <span className="ml-2 text-faint">stream closed</span>}
          {streamStatus === "error" && <span className="ml-2 text-danger">stream error</span>}
        </span>
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-auto rounded border border-edge bg-ink p-2"
      >
        {!selected ? (
          <div className="p-4 text-xs text-faint">
            {containersSnapshot === null
              ? "Waiting for container list…"
              : containersSnapshot.available === false
                ? `Docker unavailable${containersSnapshot.error ? `: ${containersSnapshot.error}` : "."}`
                : "Select a container to tail its logs."}
          </div>
        ) : shown.length === 0 ? (
          <div className="p-4 text-xs text-faint">
            {lines.length === 0 ? "No log output yet." : "No lines match the filter."}
          </div>
        ) : (
          <pre className="whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed text-fg">
            {shown.join("\n")}
          </pre>
        )}
      </div>
    </div>
  );
}
