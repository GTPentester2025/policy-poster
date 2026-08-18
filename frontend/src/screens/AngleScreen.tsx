import { useEffect, useState } from "react";
import { api } from "../api";
import type { AngleProposal, TemplateInfo } from "../types";

export function AngleScreen({
  projectId,
  onLaunch,
}: {
  projectId: string;
  onLaunch: (runId: string, angle: string) => void;
}) {
  const [indexed, setIndexed] = useState<number | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<AngleProposal[] | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [angle, setAngle] = useState("");
  const [family, setFamily] = useState("default");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const result = await api.buildIndex(projectId);
        setIndexed(result.chunks);
        const [a, t] = await Promise.all([api.angles(projectId), api.templates()]);
        setProposals(a);
        setTemplates(t);
      } catch (e) {
        setIndexError(String(e instanceof Error ? e.message : e));
      }
    })();
  }, [projectId]);

  if (indexError) {
    return (
      <div className="max-w-2xl mx-auto pt-16 px-8">
        <span className="stamp text-stamp">INDEX BLOCKED</span>
        <p className="mt-3 text-sm text-ink-soft">{indexError}</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto pt-10 px-8 pb-16">
      <h2 className="font-display text-2xl font-bold">Campaign angle</h2>
      <p className="text-sm text-ink-soft mt-1">
        {indexed === null
          ? "Chunking and indexing the sanitized policy…"
          : `Index validated — ${indexed} boundary-clean chunks. Pick a recommended angle or write your own.`}
      </p>

      {proposals && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-6">
          {proposals.map((p) => (
            <button
              key={p.angle}
              className={
                "text-left border rounded-lg p-4 bg-card transition-colors " +
                (angle === p.angle
                  ? "border-vault ring-2 ring-vault/30"
                  : "border-mist hover:border-brass")
              }
              onClick={() => setAngle(p.angle)}
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
        className="mt-8 bg-vault text-white px-6 py-2.5 rounded font-medium disabled:opacity-40"
        disabled={!angle.trim() || indexed === null || busy}
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
        {busy ? "Launching…" : "Generate posters"}
      </button>
    </div>
  );
}
