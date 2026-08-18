import { useCallback, useEffect, useState } from "react";

interface FeedbackEntry {
  entry_id: string;
  kind: string;
  policy_type: string;
  angle: string;
  before: string;
  after: string;
  note: string;
  promoted: boolean;
}

type Metrics = Record<string, number | null>;

export default function AdminScreen() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([]);

  const refresh = useCallback(async () => {
    const [m, f] = await Promise.all([
      fetch("/api/metrics").then((r) => r.json()),
      fetch("/api/feedback").then((r) => r.json()),
    ]);
    setMetrics(m);
    setFeedback(f);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (id: string, action: "promote" | "demote" | "remove") => {
    await fetch(
      `/api/feedback/${id}${action === "remove" ? "" : "/" + action}`,
      { method: action === "remove" ? "DELETE" : "POST" },
    );
    await refresh();
  };

  const promoted = feedback.filter((f) => f.promoted);

  const fmt = (v: number | null) =>
    v === null ? "—" : Number.isInteger(v) ? String(v) : (v as number).toFixed(2);

  const METRIC_LABELS: [string, string][] = [
    ["runs", "Runs"],
    ["completed_posters", "Completed posters"],
    ["groundedness_pass_rate", "Groundedness pass-rate"],
    ["citation_drift_rate", "Citation-drift rate"],
    ["mean_rewinds_per_run", "Mean rewinds / run"],
    ["coverage_completeness", "Coverage completeness"],
    ["redaction_leakage_incidents", "Redaction leakage incidents"],
    ["human_intervention_rate", "Human-intervention rate"],
    ["tokens_total", "Tokens total"],
    ["mean_tokens_per_poster", "Tokens / poster"],
  ];

  return (
    <div className="max-w-4xl mx-auto pt-10 px-8 pb-16">
      <h2 className="font-display text-2xl font-bold">Governance</h2>

      <h3 className="font-semibold text-sm mt-6 mb-2">Metrics</h3>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {metrics &&
          METRIC_LABELS.map(([key, label]) => (
            <div key={key} className="bg-card border border-mist rounded-lg p-3">
              <div className="font-mono text-lg">
                {fmt(metrics[key] as number | null)}
              </div>
              <div className="text-[11px] text-ink-soft mt-1">{label}</div>
            </div>
          ))}
      </div>

      <h3 className="font-semibold text-sm mt-8 mb-1">
        Corrections currently shaping generation
      </h3>
      <p className="text-xs text-ink-soft mb-3">
        {promoted.length} promoted correction{promoted.length === 1 ? "" : "s"}{" "}
        are injected as few-shot exemplars. Learning is retrieval-based and
        inspectable — remove any of them at any time.
      </p>
      <ul className="flex flex-col gap-2">
        {feedback.map((f) => (
          <li key={f.entry_id} className="bg-card border border-mist rounded-lg p-3 text-sm">
            <div className="flex items-center gap-2">
              <span
                className={
                  "stamp " + (f.promoted ? "text-vault" : "text-ink-soft")
                }
              >
                {f.promoted ? "ACTIVE" : "HELD"}
              </span>
              <span className="font-mono text-[11px] text-ink-soft">
                {f.kind} · {f.policy_type} · {f.angle}
              </span>
              <span className="flex-1" />
              {!f.promoted && (
                <button className="text-xs text-vault hover:underline"
                        onClick={() => void act(f.entry_id, "promote")}>
                  promote
                </button>
              )}
              {f.promoted && (
                <button className="text-xs text-ink-soft hover:underline"
                        onClick={() => void act(f.entry_id, "demote")}>
                  demote
                </button>
              )}
              <button className="text-xs text-stamp hover:underline"
                      onClick={() => void act(f.entry_id, "remove")}>
                remove
              </button>
            </div>
            <div className="mt-2 text-xs">
              <span className="line-through text-ink-soft">{f.before}</span>{" "}
              → <b>{f.after}</b>
            </div>
            {f.note && <div className="text-[11px] text-ink-soft mt-1">{f.note}</div>}
          </li>
        ))}
        {feedback.length === 0 && (
          <li className="text-xs text-ink-soft italic">
            No feedback captured yet.
          </li>
        )}
      </ul>
    </div>
  );
}
