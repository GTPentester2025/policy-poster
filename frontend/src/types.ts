export interface Term {
  term: string;
  category: string;
  placeholder: string;
  variants: string[];
  occurrences: number;
}

export interface Suggestion {
  text: string;
  label: string;
  category: string;
  count: number;
  confidence: number;
  spans: [number, number][];
}

export interface Occurrence {
  placeholder: string;
  category: string;
  start: number;
  end: number;
}

export interface ClauseView {
  clause_id: string;
  kind: string;
  char_span: [number, number];
  text: string;
  section_path: string[];
}

export interface DocumentView {
  sanitized_text: string;
  occurrences: Occurrence[];
  clauses: ClauseView[];
}

export interface AuditFinding {
  kind: string;
  severity: "hard" | "warning";
  detail: string;
  span: [number, number];
  acknowledged: boolean;
}

export interface AuditReport {
  passed: boolean;
  blocking: boolean;
  findings: AuditFinding[];
}

export interface AngleProposal {
  angle: string;
  rationale: string;
  clause_ids: string[];
  tone: string;
}

export interface TemplateInfo {
  family: string;
  budgets_landscape: Record<string, number>;
  budgets_portrait: Record<string, number>;
  max_body_points: number;
}

export interface SlotData {
  text: string;
  citations: string[];
}

export interface PosterContentData {
  poster_id: string;
  angle: string;
  template_family: string;
  content: {
    eyebrow: SlotData;
    headline: SlotData;
    subhead: SlotData;
    body_points: SlotData[];
    callout: SlotData;
    cta: SlotData;
  };
  coverage_map: Record<string, string>;
  placeholders_present: string[];
}

export interface CitationInfo {
  clause_id: string;
  text: string;
  char_span: [number, number];
  section_path: string[];
}

export interface PosterResponse {
  content: PosterContentData;
  sanitized_content: PosterContentData;
  citations: Record<string, CitationInfo>;
  coverage_map: Record<string, string>;
}

export interface AttemptRecord {
  attempt: number;
  output: Record<string, unknown>;
  verdict: string;
  findings: { detail?: string; slot?: string }[];
}

export interface Diagnostic {
  node_id: string;
  node_index: number;
  input_state_keys: string[];
  attempts: AttemptRecord[];
  supervisor_diagnosis: string;
  retrieval_spans: unknown;
}

export interface RunStatus {
  run_id: string;
  status: "running" | "complete" | "halted" | "error";
  angle: string;
  template_family: string;
  error: string | null;
  state_keys?: string[];
  diagnostic?: Diagnostic;
}
