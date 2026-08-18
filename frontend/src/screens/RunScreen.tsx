import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { RunEvent, RunStatus } from "../types";

const NODES: { id: string; label: string; desc: string }[] = [
  { id: "retrieve", label: "Agentic retrieval", desc: "hybrid search + sufficiency loop" },
  { id: "generate", label: "Content generation", desc: "schema-bound, cited copy" },
  { id: "qa_groundedness", label: "Groundedness verifier", desc: "every claim vs source spans" },
  { id: "qa_citation", label: "Citation verifier", desc: "clause really says what line claims" },
  { id: "qa_coverage", label: "Coverage agent", desc: "no obligation silently dropped" },
  { id: "qa_editorial", label: "Editorial gate", desc: "compliance blocking + tone advisory" },
  { id: "qa_layout", label: "Layout fit", desc: "both orientations, no overflow" },
  { id: "rehydrate", label: "Rehydration", desc: "placeholders → real values, validated" },
  { id: "export", label: "Export", desc: "PPTX both orientations" },
];

type NodeView = {
  status: "pending" | "active" | "pass" | "reject" | "error";
  attempts: number;
  findings: { detail?: string; slot?: string }[];
  latency_ms?: number;
  corrective?: string | null;
};

function deriveNodeViews(events: RunEvent[], running: boolean): Record<string, NodeView> {
  const views: Record<string, NodeView> = {};
  for (const n of NODES) {
    views[n.id] = { status: "pending", attempts: 0, findings: [] };
  }
  for (const ev of events) {
    const view = ev.node ? views[ev.node] : undefined;
    if (!view) continue;
    if (ev.type === "node_start") {
      view.status = "active";
      view.attempts = ev.attempt ?? view.attempts + 1;
      view.corrective = ev.corrective ?? null;
      view.findings = [];
    } else if (ev.type === "node_end") {
      view.status = ev.verdict === "reject" ? "reject" : "pass";
      view.findings = ev.findings ?? [];
      view.latency_ms = ev.latency_ms;
    } else if (ev.type === "node_error" || ev.type === "halt") {
      view.status = "error";
    }
  }
  if (!running) {
    for (const v of Object.values(views)) {
      if (v.status === "active") v.status = "error";
    }
  }
  return views;
}

export function RunScreen({
  runId,
  onComplete,
  onBackToAngle,
}: {
  runId: string;
  onComplete: () => void;
  onBackToAngle?: () => void;
}) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      while (alive) {
        try {
          const s = await api.runStatus(runId);
          if (!alive) return;
          setStatus(s);
          if (s.status === "complete" && !doneRef.current) {
            doneRef.current = true;
            setTimeout(onComplete, 900); // let the last PASSED stamp land
            return;
          }
          if (s.status !== "running") return;
        } catch {
          /* transient poll failure — keep trying */
        }
        await new Promise((r) => setTimeout(r, 700));
      }
    };
    void poll();
    return () => {
      alive = false;
    };
  }, [runId, onComplete]);

  const events = status?.events ?? [];
  const running = status?.status === "running";
  const views = useMemo(() => deriveNodeViews(events, running || status?.status === "complete"), [events, running, status?.status]);
  const rewinds = events.filter((e) => e.type === "rewind");

  if (!status) {
    return (
      <div className="max-w-3xl mx-auto pt-16 px-8" aria-busy="true">
        <div className="h-6 w-64 bg-mist rounded animate-pulse" />
        <div className="mt-6 flex flex-col gap-3">
          {NODES.map((n) => (
            <div key={n.id} className="h-12 bg-card border border-mist rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto pt-10 px-8 pb-16">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-2xl font-bold">
          {running ? "Agents at work" : status.status === "complete" ? "Run complete" : "Run stopped"}
        </h2>
        <span className="font-mono text-[11px] text-ink-soft">
          {status.provider} · “{status.angle}”
        </span>
      </div>

      {/* provider hard error */}
      {status.status === "error" && (
        <div className="mt-4 bg-stamp-soft border border-stamp/30 rounded-lg p-4" role="alert">
          <span className="stamp text-stamp">PROVIDER ERROR</span>
          <p className="mt-2 text-sm break-words">{status.error}</p>
          <div className="mt-3 flex gap-2">
            {onBackToAngle && (
              <button className="text-sm bg-ink text-paper rounded px-3 py-1.5"
                      onClick={onBackToAngle}>
                ← Adjust and retry
              </button>
            )}
            <button
              className="text-sm border border-mist bg-card rounded px-3 py-1.5"
              onClick={async () => {
                doneRef.current = false;
                await api.resume(runId, "retrieve");
                setStatus({ ...status, status: "running", error: null, events: [] });
              }}
            >
              ↻ Retry run
            </button>
          </div>
          <p className="mt-2 text-[11px] text-ink-soft">
            Check ⚙ AI provider (rail, bottom-left) — wrong model name, missing
            key, or an endpoint that is down all surface here.
          </p>
        </div>
      )}

      {/* live agent timeline */}
      <ol className="mt-6 flex flex-col" aria-live="polite" aria-label="Pipeline progress">
        {NODES.map((n, i) => {
          const v = views[n.id];
          const isLast = i === NODES.length - 1;
          return (
            <li key={n.id} className="flex gap-4">
              {/* spine */}
              <div className="flex flex-col items-center">
                <div
                  className={
                    "w-7 h-7 rounded-full flex items-center justify-center font-mono text-[11px] shrink-0 border-2 transition-colors " +
                    (v.status === "pass"
                      ? "bg-vault border-vault text-white"
                      : v.status === "active"
                        ? "border-vault text-vault animate-pulse"
                        : v.status === "reject" || v.status === "error"
                          ? "bg-stamp-soft border-stamp text-stamp"
                          : "border-mist text-ink-soft")
                  }
                  aria-label={`${n.label}: ${v.status}`}
                >
                  {v.status === "pass" ? "✓" : v.status === "reject" || v.status === "error" ? "✗" : i + 1}
                </div>
                {!isLast && (
                  <div className={"w-0.5 flex-1 min-h-4 " + (v.status === "pass" ? "bg-vault/40" : "bg-mist")} />
                )}
              </div>
              {/* card */}
              <div className={"pb-4 flex-1 min-w-0 " + (v.status === "pending" ? "opacity-50" : "")}>
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-sm font-semibold">{n.label}</span>
                  <span className="text-[11px] text-ink-soft">{n.desc}</span>
                  {v.attempts > 1 && (
                    <span className="font-mono text-[10px] px-1 rounded bg-brass-soft text-brass">
                      attempt {v.attempts}
                    </span>
                  )}
                  {v.latency_ms !== undefined && v.status !== "active" && (
                    <span className="font-mono text-[10px] text-ink-soft">
                      {(v.latency_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                  {v.status === "active" && (
                    <span className="font-mono text-[10px] text-vault animate-pulse">working…</span>
                  )}
                </div>
                {v.corrective && (
                  <div className="mt-1 text-[11px] text-brass bg-brass-soft/50 rounded px-2 py-1">
                    supervisor: {v.corrective}
                  </div>
                )}
                {v.findings.length > 0 && (
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {v.findings.slice(0, 6).map((f, j) => (
                      <li key={j} className="text-[11px] text-stamp">
                        {f.slot && <span className="font-mono">{f.slot}: </span>}
                        {f.detail}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {/* rewind narrative */}
      {rewinds.length > 0 && (
        <div className="mt-2 bg-card border border-mist rounded-lg p-3">
          <div className="font-mono text-[10px] tracking-widest text-ink-soft mb-1">
            SUPERVISOR REWINDS
          </div>
          {rewinds.map((r, i) => (
            <div key={i} className="text-[11px] text-ink-soft">
              ↩ {r.from} → {r.to}
            </div>
          ))}
        </div>
      )}

      {/* halted → diagnostic (spec §7) */}
      {status.status === "halted" && status.diagnostic && (
        <div className="mt-6">
          <span className="stamp text-stamp">SUPERVISOR HALTED</span>
          <h3 className="font-display text-lg font-bold mt-2">
            “{status.diagnostic.node_id}” failed {status.diagnostic.attempts.length} times
          </h3>
          <p className="mt-1 text-sm text-ink-soft">{status.diagnostic.supervisor_diagnosis}</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4">
            {status.diagnostic.attempts.map((a) => (
              <div key={a.attempt} className="bg-card border border-mist rounded-lg p-3">
                <div className="flex justify-between items-baseline">
                  <span className="font-mono text-xs">attempt {a.attempt}</span>
                  <span className={"stamp " + (a.verdict === "pass" ? "text-vault" : "text-stamp")}>
                    {a.verdict.toUpperCase()}
                  </span>
                </div>
                <ul className="mt-2 flex flex-col gap-1">
                  {a.findings.map((f, j) => (
                    <li key={j} className="text-[11px] text-ink-soft">
                      {f.slot && <span className="font-mono text-brass">{f.slot}: </span>}
                      {f.detail}
                    </li>
                  ))}
                  {a.findings.length === 0 && (
                    <li className="text-[11px] text-ink-soft italic">no findings recorded</li>
                  )}
                </ul>
              </div>
            ))}
          </div>
          <h4 className="font-semibold mt-5 mb-1 text-sm">Resume from any step</h4>
          <p className="text-xs text-ink-soft mb-2">
            Upstream checkpoints are kept; downstream re-runs. No re-upload, no re-redaction.
          </p>
          <div className="flex flex-wrap gap-2">
            {NODES.map((n) => (
              <button
                key={n.id}
                className="font-mono text-xs border border-mist bg-card rounded px-3 py-1.5 hover:border-vault"
                onClick={async () => {
                  doneRef.current = false;
                  await api.resume(runId, n.id);
                  setStatus({ ...status, status: "running", diagnostic: undefined, events: [] });
                }}
              >
                ↻ {n.id}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
