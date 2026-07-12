// Poll GET /healthz every 15s and normalize into HealthView. The exact
// healthz shape is not part of the documented contract, so parsing is
// deliberately tolerant: booleans, {ok}, {available}, {status:"ok"},
// {configured} all count as healthy.

import { useEffect } from "react";
import type { HealthView } from "../api/types";
import { useLiveStore } from "../stores/liveStore";

function truthy(v: unknown): boolean {
  if (v === true) return true;
  if (typeof v === "string") return ["ok", "up", "healthy", "available", "configured"].includes(v.toLowerCase());
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return (
      o.ok === true ||
      o.available === true ||
      o.configured === true ||
      truthy(o.status)
    );
  }
  return false;
}

function parseHealth(j: unknown): HealthView {
  const o = (j ?? {}) as Record<string, unknown>;
  const provider = o.provider ?? o.llm ?? o.provider_configured;
  const providerConfigured =
    typeof provider === "object" && provider !== null
      ? (provider as Record<string, unknown>).configured === true || truthy(provider)
      : truthy(provider);
  return {
    reachable: true,
    docker: truthy(o.docker),
    k8s: truthy(o.k8s ?? o.kubernetes),
    providerConfigured,
  };
}

export function useHealthPoll(intervalMs = 15000) {
  const setHealth = useLiveStore((s) => s.setHealth);
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const res = await fetch("/healthz");
        if (!res.ok) throw new Error();
        const j = await res.json();
        if (!stopped) setHealth(parseHealth(j));
      } catch {
        if (!stopped) {
          setHealth({ reachable: false, docker: false, k8s: false, providerConfigured: false });
        }
      }
    };
    void poll();
    const t = setInterval(poll, intervalMs);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [intervalMs, setHealth]);
}
