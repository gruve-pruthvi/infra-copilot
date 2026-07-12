// Provider / model / API key configuration plus dry-run mode toggle.

import { useEffect, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { SettingsResponse, TestProviderResult } from "../api/types";

type ProviderKey = "anthropic" | "openai" | "google";
const KEY_FIELDS: Record<ProviderKey, "anthropic_api_key" | "openai_api_key" | "google_api_key"> = {
  anthropic: "anthropic_api_key",
  openai: "openai_api_key",
  google: "google_api_key",
};

interface TestState {
  busy: boolean;
  result: TestProviderResult | null;
}

export function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [typedKeys, setTypedKeys] = useState<Record<string, string>>({});
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await api.get<SettingsResponse>("/api/settings");
      setData(r);
      setLoadError(null);
      setProvider(r.settings.active_provider);
      setModel(r.settings.active_model);
      setDryRun(r.settings.dry_run_mode === "true");
      setTypedKeys({});
    } catch (e) {
      setLoadError(errorMessage(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loadError) {
    return (
      <div className="p-6">
        <div className="mx-auto max-w-xl rounded border border-danger/40 bg-danger/10 p-4 text-sm text-danger">
          Failed to load settings: {loadError}
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="p-6 text-center font-mono text-xs text-faint">Loading settings…</div>;
  }

  const providers = Object.keys(data.catalog) as ProviderKey[];

  const onProviderChange = (p: ProviderKey) => {
    setProvider(p);
    setModel(data.catalog[p]?.default ?? "");
  };

  const testProvider = async (p: ProviderKey) => {
    setTests((t) => ({ ...t, [p]: { busy: true, result: null } }));
    const testModel = p === provider ? model : (data.catalog[p]?.default ?? "");
    const typed = typedKeys[p];
    try {
      const r = await api.post<TestProviderResult>("/api/settings/test-provider", {
        provider: p,
        model: testModel,
        ...(typed ? { api_key: typed } : {}),
      });
      setTests((t) => ({ ...t, [p]: { busy: false, result: r } }));
    } catch (e) {
      setTests((t) => ({ ...t, [p]: { busy: false, result: { ok: false, error: errorMessage(e) } } }));
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveMsg(null);
    setSaveErr(null);
    const body: Record<string, string> = {
      active_provider: provider,
      active_model: model,
      dry_run_mode: dryRun ? "true" : "false",
    };
    for (const p of providers) {
      const typed = typedKeys[p];
      if (typed) body[KEY_FIELDS[p]] = typed;
    }
    try {
      await api.put("/api/settings", body);
      setSaveMsg("Settings saved.");
      await load(); // refresh masked key placeholders
    } catch (e) {
      setSaveErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl space-y-5 p-4">
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-faint">Settings</h1>

        <section className="rounded border border-edge bg-panel p-4">
          <h2 className="mb-3 text-sm font-medium text-fg">Provider</h2>
          <div className="space-y-4">
            {providers.map((p) => {
              const entry = data.catalog[p];
              const masked = data.settings[KEY_FIELDS[p]];
              const test = tests[p];
              return (
                <div key={p} className="rounded border border-edge bg-ink/50 p-3">
                  <label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="radio"
                      name="provider"
                      checked={provider === p}
                      onChange={() => onProviderChange(p)}
                      className="accent-[#22d3ee]"
                    />
                    <span className="text-sm text-fg">{entry.label}</span>
                    {provider === p && (
                      <span className="rounded border border-accent/40 bg-accent/10 px-1.5 font-mono text-[10px] text-accent">
                        active
                      </span>
                    )}
                  </label>
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <input
                      type="password"
                      value={typedKeys[p] ?? ""}
                      onChange={(e) => setTypedKeys((k) => ({ ...k, [p]: e.target.value }))}
                      placeholder={masked || "no key stored"}
                      autoComplete="off"
                      className="w-64 rounded border border-edge bg-ink px-2.5 py-1.5 font-mono text-xs text-fg outline-none placeholder:text-faint focus:border-accent/50"
                    />
                    <button
                      type="button"
                      onClick={() => void testProvider(p)}
                      disabled={test?.busy}
                      className="rounded border border-edge2 px-2.5 py-1.5 text-xs text-dim hover:border-accent/50 hover:text-accent disabled:opacity-50"
                    >
                      {test?.busy ? "Testing…" : "Test connection"}
                    </button>
                    {test?.result && (
                      <span
                        className={`font-mono text-[11px] ${test.result.ok ? "text-ok" : "text-danger"}`}
                      >
                        {test.result.ok
                          ? `ok${test.result.reply ? ` — ${test.result.reply}` : ""}`
                          : `failed — ${test.result.error ?? "unknown error"}`}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-4">
            <label className="mb-1 block text-xs text-dim">Model</label>
            <input
              list="model-suggestions"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full rounded border border-edge bg-ink px-2.5 py-1.5 font-mono text-xs text-fg outline-none focus:border-accent/50"
            />
            <datalist id="model-suggestions">
              {(data.catalog[provider as ProviderKey]?.models ?? []).map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </section>

        <section className="rounded border border-edge bg-panel p-4">
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="mt-0.5 accent-[#fbbf24]"
            />
            <span>
              <span className="block text-sm text-fg">Dry-run mode</span>
              <span className="block text-xs text-dim">
                All mutating actions run as previews — nothing is actually changed.
              </span>
            </span>
          </label>
        </section>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="rounded border border-accent/50 bg-accent/10 px-4 py-1.5 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saveMsg && <span className="font-mono text-xs text-ok">{saveMsg}</span>}
          {saveErr && <span className="font-mono text-xs text-danger">{saveErr}</span>}
        </div>
      </div>
    </div>
  );
}
