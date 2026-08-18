export type StageId =
  | "ingest"
  | "redact"
  | "index"
  | "angle"
  | "run"
  | "poster";

export interface StageState {
  id: StageId;
  label: string;
  gate?: string; // gate name shown when the stage carries a blocking gate
  status: "todo" | "active" | "passed" | "blocked";
}

export function StageRail({
  stages,
  provider,
  onOpenSettings,
}: {
  stages: StageState[];
  provider?: string;
  onOpenSettings?: () => void;
}) {
  return (
    <nav
      aria-label="Workflow stages"
      className="w-52 shrink-0 border-r border-mist bg-card px-4 py-6 flex flex-col gap-1"
    >
      <div className="mb-5">
        <div className="font-display text-lg font-bold leading-tight">
          Policy Poster
        </div>
        <div className="font-mono text-[10px] tracking-widest text-ink-soft mt-1">
          GROUNDED · CITED · SEALED
        </div>
      </div>
      {stages.map((stage, i) => (
        <div key={stage.id} className="flex items-start gap-3 py-2">
          <div
            className={
              "font-mono text-[11px] w-5 text-right pt-0.5 " +
              (stage.status === "active" ? "text-vault font-semibold" : "text-ink-soft")
            }
          >
            {i + 1}
          </div>
          <div className="min-w-0">
            <div
              className={
                "text-sm " +
                (stage.status === "active"
                  ? "font-semibold text-ink"
                  : stage.status === "todo"
                    ? "text-ink-soft"
                    : "text-ink")
              }
            >
              {stage.label}
            </div>
            {stage.status === "passed" && (
              <span className="stamp text-vault mt-1">PASSED</span>
            )}
            {stage.status === "blocked" && (
              <span className="stamp text-stamp mt-1">BLOCKED</span>
            )}
            {stage.gate && stage.status !== "passed" && stage.status !== "blocked" && (
              <div className="font-mono text-[10px] text-ink-soft mt-0.5">
                gate: {stage.gate}
              </div>
            )}
          </div>
        </div>
      ))}
      <div className="mt-auto pt-6 border-t border-mist flex flex-col gap-2">
        {onOpenSettings && (
          <button
            className="text-left text-xs text-ink-soft hover:text-ink"
            onClick={onOpenSettings}
          >
            ⚙ AI provider{" "}
            {provider && (
              <span className="font-mono text-[10px] px-1 rounded bg-vault-soft text-vault">
                {provider}
              </span>
            )}
          </button>
        )}
        <a className="text-xs text-ink-soft hover:text-ink" href="/admin">
          ▤ Governance
        </a>
      </div>
    </nav>
  );
}
