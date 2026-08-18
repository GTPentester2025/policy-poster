import { useRef, useState } from "react";
import { api } from "../api";

export function UploadScreen({
  onUploaded,
}: {
  onUploaded: (projectId: string, filename: string, clauses: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.upload(file);
      onUploaded(result.project_id, result.filename, result.clauses);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto pt-24 px-8">
      <h1 className="font-display text-4xl font-bold">
        Turn a policy into posters people actually read.
      </h1>
      <p className="mt-4 text-ink-soft leading-relaxed">
        Upload an internal policy (.docx or .pdf). It is parsed into clauses,
        masked before any model sees it, and every poster line stays traceable
        to the exact clause it came from.
      </p>
      <div
        className="mt-10 border-2 border-dashed border-mist rounded-lg bg-card p-12 text-center"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) void upload(file);
        }}
      >
        <div className="font-mono text-xs tracking-widest text-ink-soft">
          DOCX · PDF — PARSED LOCALLY, CLAUSE IDS ASSIGNED
        </div>
        <button
          className="mt-6 bg-vault text-white px-6 py-2.5 rounded font-medium disabled:opacity-50"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? "Parsing…" : "Choose policy file"}
        </button>
        <input
          ref={input}
          type="file"
          accept=".docx,.pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        {error && <div className="mt-4 text-stamp text-sm">{error}</div>}
      </div>
    </div>
  );
}
