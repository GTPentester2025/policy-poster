import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { VaultText } from "../components/VaultText";
import type { AuditReport, DocumentView, Suggestion, Term } from "../types";

const CATEGORIES = [
  "org", "person", "system", "client", "domain", "address",
  "employee_id", "email", "phone", "location", "custom",
];

interface Popover {
  text: string;
  x: number;
  y: number;
  category: string;
  count: number | null;
}

export function RedactScreen({
  projectId,
  onCleared,
}: {
  projectId: string;
  onCleared: () => void;
}) {
  const [doc, setDoc] = useState<DocumentView | null>(null);
  const [terms, setTerms] = useState<Term[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [unreviewed, setUnreviewed] = useState(0);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [popover, setPopover] = useState<Popover | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [d, t, s, a] = await Promise.all([
      api.document(projectId),
      api.terms(projectId),
      api.suggestions(projectId),
      api.audit(projectId),
    ]);
    setDoc(d);
    setTerms(t);
    setSuggestions(s.suggestions);
    setUnreviewed(s.unreviewed_high_confidence);
    setAudit(a);
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onMouseUp = async () => {
    const sel = window.getSelection();
    const text = sel?.toString().trim();
    if (!sel || !text || text.length < 2 || text.includes("⟦")) {
      setPopover(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const popup: Popover = {
      text, category: "custom",
      x: rect.left + rect.width / 2, y: rect.bottom + 8, count: null,
    };
    setPopover(popup);
    try {
      const preview = await api.previewTerm(projectId, text, "custom");
      setPopover((p) => (p && p.text === text ? { ...p, count: preview.occurrences } : p));
    } catch {
      /* preview is best-effort */
    }
  };

  const maskEverywhere = async () => {
    if (!popover) return;
    setBusy(true);
    try {
      await api.addTerm(projectId, popover.text, popover.category);
      setPopover(null);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const acceptSuggestion = async (s: Suggestion) => {
    await api.addTerm(projectId, s.text, s.category);
    await refresh();
  };

  const dismissSuggestion = async (s: Suggestion) => {
    await api.dismissSuggestion(projectId, [s.text]);
    await refresh();
  };

  const unmask = async (placeholder: string) => {
    await api.removeTerm(projectId, placeholder);
    await refresh();
  };

  const acknowledgeWarning = async (surface: string) => {
    await api.acknowledge(projectId, [surface]);
    await refresh();
  };

  const hardFindings = audit?.findings.filter((f) => f.severity === "hard") ?? [];
  const warnings = audit?.findings.filter(
    (f) => f.severity === "warning" && !f.acknowledged,
  ) ?? [];
  const clear = !!audit && !audit.blocking && unreviewed === 0;

  return (
    <div className="flex gap-6 px-8 py-6 items-start">
      {/* Document, redacted live */}
      <section className="flex-[2] min-w-0">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="font-display text-2xl font-bold">Redaction review</h2>
          <div className="font-mono text-xs text-ink-soft">
            Unreviewed suggestions: <b className={unreviewed ? "text-stamp" : "text-vault"}>{unreviewed}</b>
          </div>
        </div>
        <p className="text-sm text-ink-soft mb-4">
          Select any text to mask it. Masked values render as vault chips —
          only the sanitized version can ever leave this machine.
        </p>
        <div
          className="bg-card border border-mist rounded-lg p-6 whitespace-pre-wrap leading-7 text-[15px] select-text"
          onMouseUp={() => void onMouseUp()}
        >
          {doc ? (
            <VaultText text={doc.sanitized_text} occurrences={doc.occurrences} />
          ) : (
            "Loading document…"
          )}
        </div>
      </section>

      {/* Ledger + suggestions + audit */}
      <aside className="flex-1 min-w-[320px] flex flex-col gap-5 sticky top-6">
        <div className="bg-card border border-mist rounded-lg p-4">
          <h3 className="font-semibold text-sm mb-1">Sensitive terms</h3>
          <p className="text-xs text-ink-soft mb-3">
            The complete ledger. Every mask made in the document lands here.
          </p>
          {terms.length === 0 && (
            <div className="text-xs text-ink-soft italic">
              Nothing masked yet — add the company name first.
            </div>
          )}
          <ul className="flex flex-col gap-2">
            {terms.map((t) => (
              <li key={t.placeholder} className="flex items-center gap-2 text-sm">
                <span className="vault-chip" data-cat={t.category}>{t.placeholder}</span>
                <span className="truncate flex-1">{t.term}</span>
                <span className="font-mono text-[11px] text-ink-soft">×{t.occurrences}</span>
                <button
                  className="text-[11px] text-stamp hover:underline"
                  onClick={() => void unmask(t.placeholder)}
                >
                  unmask
                </button>
              </li>
            ))}
          </ul>
          <AddTermForm projectId={projectId} onAdded={refresh} />
        </div>

        <div className="bg-card border border-mist rounded-lg p-4">
          <h3 className="font-semibold text-sm mb-3">
            Suggested entities <span className="font-mono text-[11px] text-ink-soft">(local NER)</span>
          </h3>
          {suggestions.length === 0 && (
            <div className="text-xs text-ink-soft italic">No open suggestions.</div>
          )}
          <ul className="flex flex-col gap-2">
            {suggestions.map((s) => (
              <li key={s.text + s.label} className="text-sm flex items-center gap-2">
                <span className="font-mono text-[10px] px-1 rounded bg-brass-soft text-brass">
                  {s.label}
                </span>
                <span className="truncate flex-1">{s.text}</span>
                <span className="font-mono text-[11px] text-ink-soft">
                  {(s.confidence * 100).toFixed(0)}% ×{s.count}
                </span>
                <button className="text-[11px] text-vault hover:underline"
                        onClick={() => void acceptSuggestion(s)}>mask</button>
                <button className="text-[11px] text-ink-soft hover:underline"
                        onClick={() => void dismissSuggestion(s)}>dismiss</button>
              </li>
            ))}
          </ul>
        </div>

        <div className="bg-card border border-mist rounded-lg p-4">
          <h3 className="font-semibold text-sm mb-2">Redaction Auditor</h3>
          {hardFindings.length > 0 && (
            <ul className="flex flex-col gap-1 mb-2">
              {hardFindings.map((f, i) => (
                <li key={i} className="text-xs text-stamp">✗ {f.detail}</li>
              ))}
            </ul>
          )}
          {warnings.map((f, i) => {
            const surface = f.detail.match(/'([^']+)'/)?.[1] ?? f.detail;
            return (
              <div key={i} className="text-xs flex items-center gap-2 mb-1">
                <span className="text-brass">⚠ {f.detail}</span>
                <button className="text-vault hover:underline shrink-0"
                        onClick={() => void acknowledgeWarning(surface)}>
                  not sensitive
                </button>
              </div>
            );
          })}
          {clear ? (
            <div className="mt-2 flex items-center gap-3">
              <span className="stamp text-vault">CLEAR TO PROCEED</span>
              <button className="bg-vault text-white text-sm px-4 py-1.5 rounded"
                      onClick={onCleared}>
                Build index →
              </button>
            </div>
          ) : (
            <div className="mt-2">
              <span className="stamp text-stamp">EGRESS BLOCKED</span>
              <p className="text-[11px] text-ink-soft mt-1">
                Resolve hard findings, review warnings and high-confidence
                suggestions to unlock the next stage.
              </p>
            </div>
          )}
        </div>
      </aside>

      {/* selection popover */}
      {popover && (
        <div
          className="fixed z-50 bg-ink text-paper rounded-lg shadow-xl p-3 w-64 -translate-x-1/2"
          style={{ left: popover.x, top: popover.y }}
        >
          <div className="text-xs mb-2 truncate">
            Mask “<b>{popover.text}</b>”
          </div>
          <select
            className="w-full text-xs bg-white/10 rounded p-1.5 mb-2"
            value={popover.category}
            onChange={(e) => setPopover({ ...popover, category: e.target.value })}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c} className="text-ink">{c}</option>
            ))}
          </select>
          <div className="flex items-center justify-between">
            <span className="font-mono text-[11px] opacity-80">
              {popover.count === null ? "counting…" : `${popover.count} found`}
            </span>
            <div className="flex gap-2">
              <button className="text-xs opacity-70 hover:opacity-100"
                      onClick={() => setPopover(null)}>cancel</button>
              <button
                className="text-xs bg-vault px-2.5 py-1 rounded disabled:opacity-50"
                disabled={busy}
                onClick={() => void maskEverywhere()}
              >
                Mask everywhere
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AddTermForm({
  projectId,
  onAdded,
}: {
  projectId: string;
  onAdded: () => Promise<void>;
}) {
  const [term, setTerm] = useState("");
  const [category, setCategory] = useState("org");
  return (
    <form
      className="mt-3 flex gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!term.trim()) return;
        await api.addTerm(projectId, term.trim(), category);
        setTerm("");
        await onAdded();
      }}
    >
      <input
        className="flex-1 min-w-0 border border-mist rounded px-2 py-1 text-sm bg-paper"
        placeholder="Add term…"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
      />
      <select
        className="border border-mist rounded px-1 text-xs bg-paper"
        value={category}
        onChange={(e) => setCategory(e.target.value)}
      >
        {CATEGORIES.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
      <button className="text-sm bg-ink text-paper px-3 rounded" type="submit">
        Add
      </button>
    </form>
  );
}
