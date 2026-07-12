// WebSocket managers: one for the global /ws/events feed (JSON {topic,payload}
// frames, auto-reconnect with exponential backoff) and one for plain-text
// container log tails.

function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

export type EventFrameHandler = (topic: string, payload: unknown) => void;

/** Connect to /ws/events. Returns a cleanup function. Emits synthetic
 *  "__open" / "__close" topics so the store can track connectivity. */
export function connectEvents(onFrame: EventFrameHandler): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (closed) return;
    try {
      ws = new WebSocket(wsUrl("/ws/events"));
    } catch {
      scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      attempt = 0;
      onFrame("__open", null);
    };
    ws.onmessage = (e) => {
      try {
        const { topic, payload } = JSON.parse(e.data as string);
        if (topic && topic !== "ping") onFrame(topic, payload);
      } catch {
        // ignore malformed frame
      }
    };
    ws.onclose = () => {
      onFrame("__close", null);
      scheduleReconnect();
    };
    ws.onerror = () => {
      ws?.close();
    };
  };

  const scheduleReconnect = () => {
    if (closed) return;
    attempt += 1;
    const delay = Math.min(15000, 500 * 2 ** Math.min(attempt, 5));
    timer = setTimeout(open, delay);
  };

  open();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}

/** Live-tail a container's logs over /ws/logs/{id}. Frames are plain text,
 *  one log line each. Returns a cleanup function. */
export function connectLogTail(
  containerId: string,
  onLine: (line: string) => void,
  onStatus?: (status: "open" | "closed" | "error") => void,
): () => void {
  let closed = false;
  let ws: WebSocket | null = null;
  try {
    ws = new WebSocket(wsUrl(`/ws/logs/${encodeURIComponent(containerId)}`));
  } catch {
    onStatus?.("error");
    return () => {};
  }
  ws.onopen = () => onStatus?.("open");
  ws.onmessage = (e) => {
    if (typeof e.data === "string") onLine(e.data);
  };
  ws.onclose = () => {
    if (!closed) onStatus?.("closed");
  };
  ws.onerror = () => onStatus?.("error");
  return () => {
    closed = true;
    ws?.close();
  };
}
