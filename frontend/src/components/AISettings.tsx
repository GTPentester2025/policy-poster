import { useEffect, useState } from "react";

interface LLMCurrent {
  provider: string;
  model: string;
  base_url: string;
  embed_model?: string;
  api_key_set: boolean;
}

interface LLMSettingsResponse {
  current: LLMCurrent;
  providers: string[];
  default_base_urls: Record<string, string>;
}

const KEYLESS = new Set(["offline", "ollama", "lmstudio", "vllm"]);

const MODEL_HINTS: Record<string, string> = {
  anthropic: "claude-opus-5",
  openai: "gpt-4o",
  gemini: "gemini-2.0-flash",
  groq: "llama-3.3-70b-versatile",
  mistral: "mistral-large-latest",
  deepseek: "deepseek-chat",
  openrouter: "anthropic/claude-sonnet-4-6",
  together: "meta-llama/Llama-3.3-70B-Instruct-Turbo",
  xai: "grok-3",
  ollama: "llama3.1",
  lmstudio: "local-model",
  vllm: "served-model-name",
  custom: "model-name",
};

export function AISettings({ onClose }: { onClose: () => void }) {
  const [meta, setMeta] = useState<LLMSettingsResponse | null>(null);
  const [provider, setProvider] = useState("offline");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [embedModel, setEmbedModel] = useState("");
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [models, setModels] = useState<string[]>([]);

  useEffect(() => {
    void fetch("/api/settings/llm")
      .then((r) => r.json())
      .then((data: LLMSettingsResponse) => {
        setMeta(data);
        setProvider(data.current.provider);
        setModel(data.current.model);
        setBaseUrl(data.current.base_url);
        setEmbedModel(data.current.embed_model ?? "");
        setRoles((data.current as { roles?: Record<string, string> }).roles ?? {});
      });
  }, []);

  const body = () => ({
    provider, model, base_url: baseUrl, api_key: apiKey,
    embed_model: embedModel, roles,
  });

  const loadModels = async () => {
    setBusy(true);
    setProbe(null);
    try {
      const result = await fetch("/api/settings/llm/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body()),
      }).then((r) => r.json());
      if (result.ok) {
        setModels(result.models);
        setProbe({ ok: true, text: `${result.models.length} models available` });
      } else {
        setProbe({ ok: false, text: result.error });
      }
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setBusy(true);
    setProbe(null);
    try {
      const result = await fetch("/api/settings/llm/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body()),
      }).then((r) => r.json());
      setProbe(
        result.ok
          ? { ok: true, text: `connected — model replied “${result.reply}”` }
          : { ok: false, text: result.error },
      );
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const resp = await fetch("/api/settings/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body()),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        setProbe({ ok: false, text: String(err.detail) });
        return;
      }
      setSaved(true);
      setTimeout(onClose, 700);
    } finally {
      setBusy(false);
    }
  };

  if (!meta) return null;
  const needsKey = !KEYLESS.has(provider);
  const defaultBase = meta.default_base_urls[provider] ?? "";

  return (
    <div
      className="fixed inset-0 z-50 bg-ink/40 flex items-center justify-center p-6"
      role="dialog"
      aria-label="AI provider settings"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-card rounded-lg shadow-2xl w-full max-w-md p-6">
        <div className="flex items-baseline justify-between">
          <h2 className="font-display text-xl font-bold">AI provider</h2>
          <button className="text-ink-soft text-sm hover:underline" onClick={onClose}>
            close
          </button>
        </div>
        <p className="text-xs text-ink-soft mt-1 mb-4">
          The pipeline is model-independent — point it at any provider. Only
          sanitized text is ever sent; “offline” runs with zero egress.
        </p>

        <label className="block text-xs font-medium mb-1">Provider</label>
        <select
          className="w-full border border-mist rounded px-2 py-1.5 text-sm bg-paper"
          value={provider}
          onChange={(e) => {
            const p = e.target.value;
            setProvider(p);
            setModel(MODEL_HINTS[p] ?? "");
            setBaseUrl("");
            setProbe(null);
          }}
        >
          {meta.providers.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        {provider !== "offline" && (
          <>
            <label className="block text-xs font-medium mt-3 mb-1">
              Model{" "}
              <button
                className="font-normal text-vault hover:underline"
                onClick={() => void loadModels()}
                disabled={busy}
                type="button"
              >
                load models ↺
              </button>
            </label>
            <input
              className="w-full border border-mist rounded px-2 py-1.5 text-sm bg-paper font-mono"
              value={model}
              list="ai-model-options"
              placeholder={MODEL_HINTS[provider] ?? "model name"}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id="ai-model-options">
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>

            {provider !== "anthropic" && provider !== "gemini" && (
              <>
                <label className="block text-xs font-medium mt-3 mb-1">
                  Base URL{" "}
                  <span className="font-normal text-ink-soft">
                    (OpenAI-compatible /v1)
                  </span>
                </label>
                <input
                  className="w-full border border-mist rounded px-2 py-1.5 text-sm bg-paper font-mono"
                  value={baseUrl}
                  placeholder={defaultBase || "https://host/v1"}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
                <label className="block text-xs font-medium mt-3 mb-1">
                  Embedding model{" "}
                  <span className="font-normal text-ink-soft">
                    (optional — powers retrieval; blank = local hashing)
                  </span>
                </label>
                <input
                  className="w-full border border-mist rounded px-2 py-1.5 text-sm bg-paper font-mono"
                  value={embedModel}
                  placeholder="e.g. text-embedding-3-small / nomic-embed-text"
                  onChange={(e) => setEmbedModel(e.target.value)}
                />
              </>
            )}

            {needsKey && (
              <>
                <label className="block text-xs font-medium mt-3 mb-1">
                  API key{" "}
                  {meta.current.api_key_set && (
                    <span className="font-normal text-vault">
                      (one is stored — leave blank to keep it)
                    </span>
                  )}
                </label>
                <input
                  type="password"
                  className="w-full border border-mist rounded px-2 py-1.5 text-sm bg-paper font-mono"
                  value={apiKey}
                  placeholder="••••••••"
                  autoComplete="off"
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </>
            )}
          </>
        )}

        {provider !== "offline" && (
          <details className="mt-3">
            <summary className="text-xs font-medium cursor-pointer text-ink-soft">
              Advanced — per-role models
            </summary>
            <p className="text-[11px] text-ink-soft mt-1">
              Route roles to different models on the same endpoint: a strong
              writer, a cheap verifier. Blank = main model.
            </p>
            {(["generate", "verify", "utility"] as const).map((role) => (
              <div key={role} className="mt-2">
                <label className="block text-[11px] font-medium mb-0.5">
                  {role === "generate" ? "Generate (poster copy)"
                    : role === "verify" ? "Verify (QA gates + retrieval judge)"
                    : "Utility (rerank, enrich, shorten)"}
                </label>
                <input
                  className="w-full border border-mist rounded px-2 py-1 text-xs bg-paper font-mono"
                  value={roles[role] ?? ""}
                  list="ai-model-options"
                  placeholder="(main model)"
                  onChange={(e) =>
                    setRoles({ ...roles, [role]: e.target.value })
                  }
                />
              </div>
            ))}
          </details>
        )}

        {probe && (
          <div
            className={
              "mt-3 text-xs rounded px-2.5 py-2 " +
              (probe.ok
                ? "bg-vault-soft text-vault"
                : "bg-stamp-soft text-stamp break-words")
            }
          >
            {probe.ok ? "✓ " : "✗ "}
            {probe.text}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button
            className="text-sm border border-mist rounded px-3 py-1.5 disabled:opacity-50"
            disabled={busy}
            onClick={() => void testConnection()}
          >
            {busy ? "…" : "Test connection"}
          </button>
          <button
            className="text-sm bg-vault text-white rounded px-4 py-1.5 disabled:opacity-50"
            disabled={busy}
            onClick={() => void save()}
          >
            {saved ? "Saved ✓" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
