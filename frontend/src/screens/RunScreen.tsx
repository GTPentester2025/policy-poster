import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunStatus } from "../types";

const NODES = [
  "retrieve", "generate", "qa_groundedness", "qa_citation", "qa_coverage",
  "qa_compliance", "qa_tone", "qa_layout", "rehydrate", "export",
];

export function RunScreen({
  runId,
  onComplete,
}: {
  runId: string;
  onComplete: () => void;
}) {
  const [status, setStatus] = useState<RunStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      while (alive) {
        const s = await api.runStatus(runId);
        if (!alive) return;
        setStatus(s);
        if (s.status === "complete") {
          onComplete();
          return;
        }
        if (s.status !== "running") return;
        await new Promise((r) => setTimeout(r, 800));
      }
    };
    void poll();
    return () => {
      alive = false;
    };
  }, [runId, onComplete]);

  if (!status) return <Centered>Contacting supervisor…</Centered>;

  if (status.status === "running") {
    return (
      <Centered>
        <div className="font-display text-xl font-bold">Agents at work</div>
        <ul className="mt-6 flex flex-col gap-1.5 text-sm font-mono">
          {NODES.map((n) => (
            <li key={n} className="text-ink-soft">· {n}</li>
          ))}
        </ul>
        <div className="mt-6 text-xs text-ink-soft animate-pulse">
          retrieval → generation → QA mesh → rehydration → export
        </div>
      </Centered>
    );
  }

  if (status.status === "error") {
    return (
      <Centered>
        <span className="stamp text-stamp">RUN ERROR</span>
        <p className="mt-3 text-sm text-ink-soft">{status.error}</p>
      </Centered>
    );
  }

  // halted → diagnostic panel (spec §7)
  const d = status.diagnostic;
  if (!d) return <Centered>Halted without diagnostic.</Centered>;
  return (
    <div className="max-w-5xl mx-auto pt-10 px-8 pb-16">
      <span className="stamp text-stamp">SUPERVISOR HALTED</span>
      <h2 className="font-display text-2xl font-bold mt-3">
        Node “{d.node_id}” failed {d.attempts.length} times
      </h2>
      <p className="mt-2 text-sm text-ink-soft max-w-2xl">{d.supervisor_diagnosis}</p>

      <div className="mt-4 font-mono text-xs text-ink-soft">
        graph position: {NODES.map((n, i) => (
          <span key={n} className={n === d.node_id ? "text-stamp font-bold" : ""}>
            {i > 0 && " → "}{n}
          </span>
        ))}
      </div>

      <h3 className="font-semibold mt-8 mb-2 text-sm">
        All attempts, side by side
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {d.attempts.map((a) => (
          <div key={a.attempt} className="bg-card border border-mist rounded-lg p-4">
            <div className="flex justify-between items-baseline">
              <span className="font-mono text-xs">attempt {a.attempt}</span>
              <span className={"stamp " + (a.verdict === "pass" ? "text-vault" : "text-stamp")}>
                {a.verdict.toUpperCase()}
              </span>
            </div>
            <ul className="mt-3 flex flex-col gap-1">
              {a.findings.map((f, i) => (
                <li key={i} className="text-xs text-ink-soft">
                  {f.slot && <span className="font-mono text-brass">{f.slot}: </span>}
                  {f.detail}
                </li>
              ))}
              {a.findings.length === 0 && (
                <li className="text-xs text-ink-soft italic">no findings recorded</li>
              )}
            </ul>
          </div>
        ))}
      </div>

      <h3 className="font-semibold mt-8 mb-2 text-sm">Resume from any step</h3>
      <p className="text-xs text-ink-soft mb-3">
        Upstream checkpoints are preserved; everything downstream re-runs.
        No re-upload, no re-redaction.
      </p>
      <div className="flex flex-wrap gap-2">
        {NODES.map((n) => (
          <button
            key={n}
            className="font-mono text-xs border border-mist bg-card rounded px-3 py-1.5 hover:border-vault"
            onClick={async () => {
              await api.resume(runId, n);
              setStatus({ ...status, status: "running", diagnostic: undefined });
            }}
          >
            ↻ {n}
          </button>
        ))}
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="pt-28 text-center flex flex-col items-center">{children}</div>
  );
}
