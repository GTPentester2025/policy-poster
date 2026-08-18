import { useEffect, useState } from "react";
import { api } from "../api";
import { PosterTemplate } from "../components/PosterTemplate";
import { ChipifyString } from "../components/VaultText";
import type { CitationInfo, PosterResponse } from "../types";

export function PosterScreen({ runId }: { runId: string }) {
  const [poster, setPoster] = useState<PosterResponse | null>(null);
  const [orientation, setOrientation] = useState<"landscape" | "portrait">("landscape");
  const [source, setSource] = useState<{ slot: string; citations: CitationInfo[] } | null>(null);

  useEffect(() => {
    void api.poster(runId).then(setPoster);
  }, [runId]);

  if (!poster) return <div className="pt-24 text-center">Loading poster…</div>;

  const scale = orientation === "landscape" ? 0.55 : 0.42;
  const width = orientation === "landscape" ? 1280 : 720;
  const height = orientation === "landscape" ? 720 : 1280;

  const onSlotClick = (slot: string, citationIds: string[]) => {
    const infos = citationIds
      .map((cid) => poster.citations[cid])
      .filter((c): c is CitationInfo => !!c);
    setSource({ slot, citations: infos });
  };

  return (
    <div className="flex gap-6 px-8 py-6 items-start">
      <section className="flex-[2] min-w-0">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-display text-2xl font-bold">Poster</h2>
          <div className="flex items-center gap-3">
            <div className="flex rounded overflow-hidden border border-mist" role="tablist">
              {(["landscape", "portrait"] as const).map((o) => (
                <button
                  key={o}
                  role="tab"
                  aria-selected={orientation === o}
                  className={
                    "px-3 py-1.5 text-xs font-mono " +
                    (orientation === o ? "bg-ink text-paper" : "bg-card text-ink-soft")
                  }
                  onClick={() => setOrientation(o)}
                >
                  {o}
                </button>
              ))}
            </div>
            <a
              className="text-xs bg-vault text-white rounded px-3 py-1.5"
              href={api.exportUrl(runId, orientation)}
            >
              ⬇ PPTX ({orientation})
            </a>
          </div>
        </div>
        <p className="text-xs text-ink-soft mb-3">
          Identical content in both orientations — only layout differs. Click
          any line to see the exact clause it came from.
        </p>
        <div
          className="border border-mist rounded-lg overflow-hidden shadow-sm bg-white"
          style={{ width: width * scale, height: height * scale }}
        >
          <div style={{ transform: `scale(${scale})`, transformOrigin: "top left" }}>
            <PosterTemplate
              content={poster.content}
              orientation={orientation}
              onSlotClick={onSlotClick}
            />
          </div>
        </div>
      </section>

      <aside className="flex-1 min-w-[320px] sticky top-6">
        <div className="bg-card border border-mist rounded-lg p-4">
          <h3 className="font-semibold text-sm mb-1">Source of record</h3>
          {!source && (
            <p className="text-xs text-ink-soft">
              Click a poster line to reveal its citation.
            </p>
          )}
          {source && (
            <>
              <div className="font-mono text-[11px] text-ink-soft mb-3">
                slot: {source.slot}
              </div>
              {source.citations.length === 0 && (
                <p className="text-xs text-stamp">
                  No resolvable citation — this should never pass the gates.
                </p>
              )}
              {source.citations.map((c) => (
                <blockquote
                  key={c.clause_id}
                  className="border-l-4 border-brass bg-brass-soft/40 rounded-r px-3 py-2 mb-3"
                >
                  <div className="font-mono text-[11px] text-brass mb-1">
                    clause {c.clause_id} · chars {c.char_span[0]}–{c.char_span[1]}
                  </div>
                  <div className="text-[11px] text-ink-soft mb-1">
                    {c.section_path.join(" › ")}
                  </div>
                  <div className="text-sm leading-snug">
                    <ChipifyString text={c.text} />
                  </div>
                </blockquote>
              ))}
            </>
          )}
        </div>

        <div className="bg-card border border-mist rounded-lg p-4 mt-4">
          <h3 className="font-semibold text-sm mb-2">Coverage map</h3>
          <ul className="flex flex-col gap-1">
            {Object.entries(poster.coverage_map).map(([cid, state]) => (
              <li key={cid} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-ink-soft w-14">{cid}</span>
                <span
                  className={
                    "font-mono text-[10px] px-1.5 rounded " +
                    (state === "covered"
                      ? "bg-vault-soft text-vault"
                      : state === "omitted"
                        ? "bg-stamp-soft text-stamp"
                        : "bg-mist text-ink-soft")
                  }
                >
                  {state}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
