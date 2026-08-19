import { useEffect, useState } from "react";

interface CampaignRun {
  run_id: string;
  status: string;
  angle: string;
}

interface Campaign {
  campaign_id: string;
  angle: string;
  runs: CampaignRun[];
  merged_coverage: Record<string, string>;
}

export function CampaignScreen({
  campaignId,
  onViewPoster,
}: {
  campaignId: string;
  onViewPoster: (runId: string) => void;
}) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      while (alive) {
        const data = await fetch(`/api/campaigns/${campaignId}`).then((r) => r.json());
        if (!alive) return;
        setCampaign(data);
        if (data.runs.every((r: CampaignRun) => r.status !== "running")) return;
        await new Promise((r) => setTimeout(r, 1200));
      }
    };
    void poll();
    return () => {
      alive = false;
    };
  }, [campaignId]);

  if (!campaign) return <div className="pt-24 text-center">Loading campaign…</div>;

  return (
    <div className="max-w-3xl mx-auto pt-10 px-8 pb-16">
      <h2 className="font-display text-2xl font-bold">Campaign</h2>
      <p className="text-sm text-ink-soft mt-1">
        “{campaign.angle}” — {campaign.runs.length} posters, partitioned so no
        obligation is dropped.
      </p>
      <ul className="mt-6 flex flex-col gap-3">
        {campaign.runs.map((run, i) => (
          <li key={run.run_id}
              className="bg-card border border-mist rounded-lg p-4 flex items-center gap-3">
            <span className="font-mono text-xs text-ink-soft">#{i + 1}</span>
            <span className="text-sm flex-1 truncate">{run.angle}</span>
            <span
              className={
                "stamp " +
                (run.status === "complete" ? "text-vault"
                  : run.status === "running" ? "text-brass" : "text-stamp")
              }
            >
              {run.status.toUpperCase()}
            </span>
            {run.status === "complete" && (
              <button className="text-xs bg-vault text-white rounded px-3 py-1.5"
                      onClick={() => onViewPoster(run.run_id)}>
                View poster →
              </button>
            )}
          </li>
        ))}
      </ul>
      <h3 className="font-semibold text-sm mt-8 mb-2">Merged coverage</h3>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(campaign.merged_coverage).map(([cid, state]) => (
          <span key={cid}
                className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-vault-soft text-vault">
            {cid}: {state}
          </span>
        ))}
        {Object.keys(campaign.merged_coverage).length === 0 && (
          <span className="text-xs text-ink-soft italic">
            Coverage appears as posters complete.
          </span>
        )}
      </div>
    </div>
  );
}
