import { useCallback, useState } from "react";
import { StageRail, type StageState } from "./components/StageRail";
import { AngleScreen } from "./screens/AngleScreen";
import { PosterScreen } from "./screens/PosterScreen";
import { RedactScreen } from "./screens/RedactScreen";
import { RunScreen } from "./screens/RunScreen";
import { UploadScreen } from "./screens/UploadScreen";

type Step = "ingest" | "redact" | "angle" | "run" | "poster";

export default function App() {
  const [step, setStep] = useState<Step>("ingest");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const stages: StageState[] = [
    { id: "ingest", label: "Ingest", status: stageStatus("ingest", step) },
    { id: "redact", label: "Redact", gate: "auditor", status: stageStatus("redact", step) },
    { id: "index", label: "Index + Angle", gate: "validation", status: stageStatus("angle", step, "index") },
    { id: "run", label: "Agent run", gate: "QA mesh", status: stageStatus("run", step) },
    { id: "poster", label: "Poster + Export", status: stageStatus("poster", step) },
  ];

  const onRunComplete = useCallback(() => setStep("poster"), []);

  return (
    <div className="flex min-h-screen">
      <StageRail stages={stages} />
      <main className="flex-1 min-w-0">
        {step === "ingest" && (
          <UploadScreen
            onUploaded={(pid) => {
              setProjectId(pid);
              setStep("redact");
            }}
          />
        )}
        {step === "redact" && projectId && (
          <RedactScreen projectId={projectId} onCleared={() => setStep("angle")} />
        )}
        {step === "angle" && projectId && (
          <AngleScreen
            projectId={projectId}
            onLaunch={(rid) => {
              setRunId(rid);
              setStep("run");
            }}
          />
        )}
        {step === "run" && runId && (
          <RunScreen runId={runId} onComplete={onRunComplete} />
        )}
        {step === "poster" && runId && <PosterScreen runId={runId} />}
      </main>
    </div>
  );
}

const ORDER: Step[] = ["ingest", "redact", "angle", "run", "poster"];

function stageStatus(
  stage: Step,
  current: Step,
  _alias?: string,
): StageState["status"] {
  const si = ORDER.indexOf(stage);
  const ci = ORDER.indexOf(current);
  if (si < ci) return "passed";
  if (si === ci) return "active";
  return "todo";
}
