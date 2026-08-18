import { Fragment } from "react";
import type { Occurrence } from "../types";

/** Renders sanitized text with placeholder occurrences as vault chips. */
export function VaultText({
  text,
  occurrences,
}: {
  text: string;
  occurrences: Occurrence[];
}) {
  const sorted = [...occurrences].sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  sorted.forEach((occ, i) => {
    if (occ.start > cursor) {
      parts.push(<Fragment key={`t${i}`}>{text.slice(cursor, occ.start)}</Fragment>);
    }
    parts.push(
      <span key={`c${i}`} className="vault-chip" data-cat={occ.category}>
        {text.slice(occ.start, occ.end)}
      </span>,
    );
    cursor = occ.end;
  });
  parts.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);
  return <>{parts}</>;
}

/** Inline variant for arbitrary strings that may contain ⟦…⟧ tokens. */
export function ChipifyString({ text }: { text: string }) {
  const parts = text.split(/(⟦[A-Z]+_\d{3}⟧)/g);
  return (
    <>
      {parts.map((part, i) =>
        /^⟦[A-Z]+_\d{3}⟧$/.test(part) ? (
          <span key={i} className="vault-chip">
            {part}
          </span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
