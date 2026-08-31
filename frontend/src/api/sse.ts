// SSE parsing over fetch. EventSource cannot POST, so we read the body stream
// manually: frames are separated by a blank line, payload lines start with
// "data:"; comment lines (": keepalive") are ignored.

import { authHeaders } from "./client";
import type { ChatEvent } from "./types";

function emitFrame(frame: string, onEvent: (ev: ChatEvent) => void): void {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue; // skips ": keepalive" comments
    const data = line.slice(5).trim();
    if (!data) continue;
    try {
      onEvent(JSON.parse(data) as ChatEvent);
    } catch {
      // malformed frame — ignore
    }
  }
}

/**
 * Open an SSE stream (GET or POST) and dispatch each parsed event.
 * Resolves when the stream ends. Emits a synthetic {type:"error"} event on
 * HTTP or network failure so callers have a single event path.
 */
export async function streamSSE(
  path: string,
  init: RequestInit | undefined,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      signal,
      credentials: "same-origin",
      headers: {
        Accept: "text/event-stream",
        ...authHeaders(),
        ...(init?.headers as Record<string, string> | undefined),
      },
    });
  } catch (e) {
    if ((e as Error)?.name !== "AbortError") {
      onEvent({ type: "error", message: "Backend unreachable" });
    }
    return;
  }

  if (!res.ok) {
    let detail = `Request failed (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // keep default
    }
    onEvent({ type: "error", message: detail });
    return;
  }
  if (!res.body) {
    onEvent({ type: "error", message: "Empty stream body" });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // JSON payloads never contain raw \r, so normalizing CRLF here is safe.
      buf += decoder.decode(value, { stream: true }).replace(/\r/g, "");
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        emitFrame(frame, onEvent);
      }
    }
    if (buf.trim()) emitFrame(buf, onEvent);
  } catch (e) {
    if ((e as Error)?.name !== "AbortError") {
      onEvent({ type: "error", message: "Stream interrupted" });
    }
  }
}
