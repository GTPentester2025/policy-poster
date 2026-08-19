import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AnglesResponse, TemplateInfo } from "../types";

export function AngleScreen({
  projectId,
  onLaunch,
  onLaunchCampaign,
}: {
  projectId: string;
  onLaunch: (runId: string, angle: string) => void;
  onLaunchCampaign?: (campaignId: string) => void;
}) {
  const [phase, setPhase] = useState<"indexing" | "angles" | "ready" | "index_error">("indexing");
  const [chunks, setChunks] = useState<number | null>(null);
  const [embeddingSource, setEmbeddingSource] = useState("");
  const [indexError, setIndexError] = useState<string | null>(null);
  const [anglesResp, setAnglesResp] = useState<AnglesResponse | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [angle, setAngle] = useState("");
  const [family, setFamily] = useState("default");
  const [busy, setBusy] = useState(false);

  const boot = useCallback(async () => {
    setPhase("indexing");
    setIndexError(null);
    try {
      const result = await api.buildIndex(projectId);
      setChunks(result.chunks);
      setEmbeddingSource(result.embedding_source);
      setPhase("angles");
      const [a, t] = await Promise.all([api.angles(projectId), api.templates()]);
      setAnglesResp(a);
      setTemplates(t);
      setPhase("ready");
    } catch (e) {
      setIndexError(String(e instanceof Error ? e.message : e));
      setPhase("index_error");
    }
  }, [projectId]);

  useEffect(() => {
    void boot();
  }, [boot]);

  if (phase === "index_error") {
    return (
      <div className="max-w-2xl mx-auto pt-16 px-8">
        <span className="stamp text-stamp">INDEX BLOCKED</span>
        <p className="mt-3 text-sm break-words">{indexError}</p>
        <button className="mt-4 text-sm bg-ink text-paper rounded px-4 py-2"
                onClick={() => void boot()}>
          ↻ Retry indexing
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto pt-10 px-8 pb-16">
      <h2 className="font-display text-2xl font-bold">Campaign angle</h2>

      {/* index status strip */}
      <div className="mt-2 flex items-center gap-3 flex-wrap text-xs">
        {phase === "indexing" ? (
          <span className="text-ink-soft animate-pulse">
            Chunking and embedding the sanitized policy…
          </span>
        ) : (
          <>
            <span className="stamp text-vault">INDEX VALIDATED</span>
            <span className="font-mono text-ink-soft">{chunks} chunks</span>
            <span
              className={
                "font-mono px-1.5 py-0.5 rounded " +
                (embeddingSource.startsWith("local")
                  ? "bg-mist text-ink-soft"
                  : "bg-vault-soft text-vault")
              }
              title="What produced the dense vectors for retrieval"
            >
              embeddings: {embeddingSource}
            </span>
          </>
        )}
      </div>

      <h3 className="font-semibold text-sm mt-6 mb-2">
        AI-recommended angles{" "}
        {anglesResp && (
          <span className="font-mono text-[11px] text-ink-soft">
            via {anglesResp.provider}
          </span>
        )}
      </h3>

      {phase !== "ready" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 bg-card border border-mist rounded-lg animate-pulse" />
          ))}
        </div>
      )}

      {phase === "ready" && anglesResp?.error && (
        <div className="bg-stamp-soft border border-stamp/30 rounded-lg p-3 text-sm mb-3" role="alert">
          <b>Angle recommendations failed:</b> {anglesResp.error}
          <div className="mt-2 flex gap-2">
            <button className="text-xs border border-mist bg-card rounded px-2.5 py-1"
                    onClick={() => void boot()}>↻ Retry</button>
          </div>
          <p className="text-[11px] text-ink-soft mt-1">
            You can still write your own angle below.
          </p>
        </div>
      )}

      {phase === "ready" && anglesResp && anglesResp.proposals.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {anglesResp.proposals.map((p) => (
            <button
              key={p.angle}
              className={
                "text-left border rounded-lg p-4 bg-card transition-colors " +
                (angle === p.angle
                  ? "border-vault ring-2 ring-vault/30"
                  : "border-mist hover:border-brass")
              }
              onClick={() => setAngle(p.angle)}
              aria-pressed={angle === p.angle}
            >
              <div className="font-semibold text-sm">{p.angle}</div>
              <div className="text-xs text-ink-soft mt-1">{p.rationale}</div>
              <div className="font-mono text-[10px] text-brass mt-2">
                grounded in {p.clause_ids.join(", ")} · tone: {p.tone}
              </div>
            </button>
          ))}
        </div>
      )}

      <label className="block mt-6 text-sm font-medium" htmlFor="angle-input">
        Angle for this campaign
      </label>
      <input
        id="angle-input"
        className="mt-1 w-full border border-mist rounded-lg bg-card px-3 py-2 text-sm"
        placeholder='e.g. "urgency around phishing reporting"'
        value={angle}
        onChange={(e) => setAngle(e.target.value)}
      />

      <h3 className="font-display text-lg font-bold mt-8">Template family</h3>
      <p className="text-xs text-ink-soft">
        Both orientations are always produced; copy budgets use the tighter of the two.
      </p>
      <div className="flex gap-3 mt-3">
        {templates.map((t) => (
          <button
            key={t.family}
            className={
              "border rounded-lg p-3 bg-card text-left " +
              (family === t.family
                ? "border-vault ring-2 ring-vault/30"
                : "border-mist hover:border-brass")
            }
            onClick={() => setFamily(t.family)}
            aria-pressed={family === t.family}
          >
            <div className="flex gap-2 items-end mb-2" aria-hidden>
              <div className="w-16 h-9 bg-paper border border-mist rounded-sm border-l-4 border-l-brass" />
              <div className="w-9 h-16 bg-paper border border-mist rounded-sm border-l-4 border-l-brass" />
            </div>
            <div className="font-mono text-xs">{t.family}</div>
            <div className="text-[10px] text-ink-soft">
              headline ≤ {Math.min(t.budgets_landscape.headline ?? 99, t.budgets_portrait.headline ?? 99)} chars
            </div>
          </button>
        ))}
      </div>

      <button
        className="mt-8 bg-vault text-white px-6 py-2.5 rounded font-medium disabled:opacity-40 disabled:cursor-not-allowed"
        disabled={!angle.trim() || phase !== "ready" || busy}
        onClick={async () => {
          setBusy(true);
          try {
            const { run_id } = await api.startRun(projectId, angle.trim(), family);
            onLaunch(run_id, angle.trim());
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Launching…" : "Generate posters →"}
      </button>
      {onLaunchCampaign && (
        <button
          className="mt-8 ml-3 border border-vault text-vault px-5 py-2.5 rounded font-medium disabled:opacity-40"
          disabled={!angle.trim() || phase !== "ready" || busy}
          title="One poster per group of obligations — nothing gets dropped"
          onClick={async () => {
            setBusy(true);
            try {
              const resp = await fetch(`/api/projects/${projectId}/campaigns`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ angle: angle.trim(), template_family: family }),
              });
              const data = await resp.json();
              if (resp.ok) onLaunchCampaign(data.campaign_id);
            } finally {
              setBusy(false);
            }
          }}
        >
          Generate as campaign (multi-poster)
        </button>
      )}
    </div>
  );
}
