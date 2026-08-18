import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { PosterTemplate } from "../components/PosterTemplate";
import type { PosterContentData } from "../types";

/** Bare render route for headless JPG capture (Playwright). */
export default function RenderPage() {
  const { runId, orientation } = useParams();
  const [content, setContent] = useState<PosterContentData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    api
      .poster(runId)
      .then((p) => setContent(p.content))
      .catch((e) => setError(String(e)));
  }, [runId]);

  if (error) return <div data-render-error>{error}</div>;
  if (!content) return <div data-render-loading>Loading…</div>;
  return (
    <div data-render-ready style={{ margin: 0, padding: 0 }}>
      <PosterTemplate
        content={content}
        orientation={orientation === "portrait" ? "portrait" : "landscape"}
      />
    </div>
  );
}
