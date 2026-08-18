import type { PosterContentData } from "../types";

/**
 * The render source of truth (spec §3): PPTX/JPG must match this tree.
 * Deterministic, print-safe geometry: fixed-pixel canvas at 96 DPI
 * (landscape 1280×720, portrait 720×1280); JPG export screenshots this
 * component at a higher deviceScaleFactor for 300 DPI.
 */
export function PosterTemplate({
  content,
  orientation,
  onSlotClick,
}: {
  content: PosterContentData;
  orientation: "landscape" | "portrait";
  onSlotClick?: (slotName: string, citations: string[]) => void;
}) {
  const landscape = orientation === "landscape";
  const c = content.content;

  const slot = (
    name: string,
    data: { text: string; citations: string[] },
    className: string,
  ) => (
    <div
      className={
        className +
        (onSlotClick
          ? " cursor-pointer rounded-sm hover:outline hover:outline-2 hover:outline-brass/60"
          : "")
      }
      role={onSlotClick ? "button" : undefined}
      tabIndex={onSlotClick ? 0 : undefined}
      onClick={() => onSlotClick?.(name, data.citations)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onSlotClick?.(name, data.citations);
      }}
    >
      {data.text}
    </div>
  );

  return (
    <div
      data-poster-canvas
      style={{
        width: landscape ? 1280 : 720,
        height: landscape ? 720 : 1280,
        background: "#FAFAF7",
        color: "#1A2332",
        fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
        display: "flex",
        flexDirection: "column",
        padding: landscape ? "56px 72px" : "64px 56px",
        boxSizing: "border-box",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* brass spine — the citation-of-record mark */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 10,
          background: "#A87B2D",
        }}
      />
      {slot(
        "eyebrow",
        c.eyebrow,
        "font-mono tracking-[0.18em] text-[#0E4F45] font-medium " +
          (landscape ? "text-[22px]" : "text-[20px]"),
      )}
      {slot(
        "headline",
        c.headline,
        "font-display font-bold leading-[1.05] mt-4 " +
          (landscape ? "text-[76px]" : "text-[58px]"),
      )}
      {slot(
        "subhead",
        c.subhead,
        "mt-5 text-[#4A5568] leading-snug " +
          (landscape ? "text-[30px] max-w-[900px]" : "text-[26px]"),
      )}
      <div
        style={{
          marginTop: "auto",
          display: "flex",
          flexDirection: landscape ? "row" : "column",
          gap: landscape ? 48 : 28,
          alignItems: landscape ? "flex-end" : "stretch",
        }}
      >
        <ul
          style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14 }}
        >
          {c.body_points.map((p, i) => (
            <li key={i} className="flex gap-3 items-baseline">
              <span
                className="font-mono text-[#A87B2D]"
                style={{ fontSize: landscape ? 20 : 18 }}
              >
                §
              </span>
              {slot(
                `body_points[${i}]`,
                p,
                landscape ? "text-[24px]" : "text-[22px]",
              )}
            </li>
          ))}
        </ul>
        <div style={{ width: landscape ? 380 : "auto" }}>
          {slot(
            "callout",
            c.callout,
            "font-display font-bold text-[#0E4F45] leading-tight " +
              (landscape ? "text-[34px]" : "text-[30px]"),
          )}
          {slot(
            "cta",
            c.cta,
            "mt-4 inline-block bg-[#1A2332] text-[#FAFAF7] font-semibold rounded-sm " +
              (landscape
                ? "text-[22px] px-6 py-3"
                : "text-[20px] px-5 py-3"),
          )}
        </div>
      </div>
    </div>
  );
}
