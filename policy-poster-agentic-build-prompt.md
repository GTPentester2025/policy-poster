# BUILD PROMPT — Policy-to-Poster Agentic Platform

> Hand this document to a coding agent (Claude Code, or equivalent) as the authoritative build specification. Everything below is a requirement, not a suggestion. Where a requirement is marked **HARD**, failing it is a build failure.

---

## 1. Mission

Build a web platform that ingests an internal company policy document (DOCX or PDF) and produces **awareness posters and newsletter panels** that educate employees about that policy.

The output must be:
- **Strictly grounded** — every claim traceable to a specific span of the source policy.
- **Fully cited** — the user can click any line of poster copy and see the exact section/clause it came from.
- **Confidentiality-safe** — no PII, company name, or identifying data ever leaves the local boundary and reaches a model provider.
- **Complete** — no material clause of the policy silently dropped from the awareness campaign.

---

## 2. Non-Negotiable Constraints

| # | Constraint | Enforcement |
|---|---|---|
| C1 | **HARD** — No generated sentence may assert a fact, number, obligation, deadline, or consequence that is not present in the retrieved source spans. | Groundedness Verifier agent + citation binding |
| C2 | **HARD** — No raw sensitive token (company name, PII, internal system names, client names) is transmitted to any LLM API. Sanitisation happens *before* the first model call. | Redaction Auditor gate; hard block on egress |
| C3 | **HARD** — Every placeholder inserted during redaction must be resolved back to its original value at the final step. Zero unresolved, zero mis-mapped. | Rehydration Validator |
| C4 | Every poster line carries a citation pointer to `{section_id, clause_id, char_span}`. | Schema-enforced; unciteable line = rejected |
| C5 | No agent may invent a template, statistic, legal citation, or external reference. | Groundedness Verifier |
| C6 | The Supervisor may rewind the graph a **maximum of 2 times per node**. On the third failure it must halt and surface a diagnostic to the human. | Budget counter in orchestrator state |
| C7 | Content is generated **once** per poster and is **identical** across portrait and landscape. Only layout, positioning, and line-breaking differ. | Single canonical content object |

---

## 3. Tech Stack

- **Frontend / render layer:** React (primary, non-negotiable). Poster templates are React components; the rendered component tree is the source of truth for all exports.
- **Styling:** Tailwind or CSS-in-JS, your choice — but template geometry must be deterministic and print-safe.
- **Orchestration:** Graph-based agent orchestration (LangGraph-style or equivalent DAG-with-cycles engine). **Not** a linear chain. Nodes must support conditional edges, retry edges, and supervisor-initiated rewind edges.
- **Vector store:** Local/embedded (LanceDB, Chroma, or pgvector). Must store rich metadata alongside vectors.
- **Document parsing:** DOCX via structure-preserving parser (python-docx / mammoth); PDF via layout-aware extraction (pdfplumber / PyMuPDF) with heading detection.
- **Export:** PPTX via python-pptx (editable text boxes — **not** flattened images); JPG via headless browser render (Playwright) of the React component.

---

## 4. Pipeline — Stage by Stage

### Stage 0 — Ingest
- Accept `.docx` and `.pdf`.
- Parse into a **structural tree**, not a flat string: `Document → Section → Subsection → Clause → Paragraph`.
- Preserve: heading text, heading level, numbering (1.2.3, (a), (i)), tables, lists, and character offsets into the original.
- Assign every leaf node a stable `clause_id`. This ID is the anchor for all downstream citations.
- **Do not** proceed to any model call from this stage.

### Stage 1 — Redaction & Placeholder Lifecycle

This stage is entirely **local and deterministic-first**. No LLM egress permitted until the Redaction Auditor passes.

**1.1 — Pre-declared keyword input**
- Before or immediately after upload, present a dedicated **Sensitive Terms** panel.
- Pre-seeded fields (user fills in): Company Legal Name, Company Trading Name(s), Subsidiary Names, Client Names, Internal System/Application Names, Domain Names, Physical Addresses, Named Individuals, Employee IDs, Phone/Email patterns.
- User may add arbitrary custom terms. Each term gets a placeholder token: `⟦ORG_001⟧`, `⟦PERSON_003⟧`, `⟦SYSTEM_002⟧`, etc.

**1.2 — Deterministic replacement**
- Exact and case-insensitive matching, plus normalised variants: `XYZ Private Limited` / `XYZ Pvt. Ltd.` / `XYZ Pvt Ltd` / `XYZ` must all map to the **same** placeholder.
- Replacement is **global** across the entire document, every occurrence, including inside tables and headers.
- Maintain a `redaction_map: { placeholder → original_value }` held **server-side only, never serialised into any prompt**.

**1.3 — Suggested-entity pass (local NER)**
- Run a **local** NER model (spaCy or similar — must not be an external API) to propose additional candidates: ORG, PERSON, GPE, EMAIL, PHONE, MONEY, DATE-of-birth patterns.
- Present these as **suggestions with confidence**, never auto-applied. User accepts or dismisses each.

**1.4 — Interactive review UI (HARD requirement)**
- Render the full policy with placeholders visibly highlighted and colour-coded by category.
- The user can **select any text span with the cursor** in the rendered document.
- On selection, a popover appears offering:
  - `Mask this term` → choose or create a category
  - `Show all occurrences (n found)` → live count before committing
  - `Mask everywhere` → applies globally across the document, retroactively
- Newly masked terms are **automatically appended to the Sensitive Terms panel**, so the panel is always the complete ledger.
- The reverse must also work: user can **unmask** a placeholder if it was over-eager.
- Live counter: `Unreviewed suggestions: n` — the stage cannot be marked complete while high-confidence suggestions remain unactioned.

**1.5 — Redaction Auditor gate**
- Automated scan of the sanitised text for: any original value from `redaction_map` surviving in the text, residual email/phone/PAN/Aadhaar-shaped patterns, capitalised multi-word entities not in the ledger.
- **HARD:** if anything is found, the pipeline blocks and returns to 1.4. No egress.

### Stage 2 — Agentic RAG Layer

This must be **agentic RAG**, not naive top-k. Build it accordingly.

**2.1 — Structure-aware chunking (HARD)**
- Chunk boundaries **must** align to semantic units: a clause, a sub-clause, a complete list, a complete table row-group. 
- **Never** split mid-sentence, mid-clause, or mid-list-item. A chunk that ends in a dangling fragment is a defect.
- Target 200–500 tokens, but **boundary integrity beats size targets**. A 700-token clause stays whole. A 60-token clause stays whole.
- Small orphan clauses are merged with their parent heading context rather than left as fragments.

**2.2 — Chunk metadata (every chunk carries all of this)**
```json
{
  "chunk_id": "uuid",
  "clause_ids": ["3.2.1", "3.2.2"],
  "section_path": ["3. Data Handling", "3.2 Retention"],
  "heading_context": "3.2 Retention Periods",
  "char_span": [4821, 5340],
  "chunk_type": "clause | list | table | definition | preamble",
  "obligation_flag": true,
  "contains_placeholder": ["⟦ORG_001⟧"],
  "prev_chunk_id": "uuid",
  "next_chunk_id": "uuid"
}
```

**2.3 — Contextual enrichment before embedding**
- Prepend the section path to the chunk text at embedding time so `"Records must be destroyed after 90 days"` embeds as part of `"3. Data Handling > 3.2 Retention Periods: Records must be destroyed..."`.
- Store the raw text and the enriched text separately; retrieve raw, embed enriched.

**2.4 — Agentic retrieval behaviour (HARD)**
Retrieval is a **loop**, not a call:
1. Agent formulates a retrieval intent.
2. Hybrid search: dense vector + BM25 keyword, reciprocal-rank fused.
3. Agent evaluates sufficiency: *"Do these spans fully answer my intent?"*
4. If insufficient or ambiguous → **reformulate query and re-retrieve** (max 4 iterations).
5. Expand to neighbours: if a retrieved chunk references `see 4.1`, follow it via `prev/next/cross-ref` links.
6. Emit a `retrieval_report` listing every chunk consulted, retained, and discarded with reasons.

**2.5 — Index validation gate**
Before the graph proceeds: assert every clause in the structural tree appears in ≥1 chunk; assert no chunk is empty, truncated, or duplicated; assert embedding dimensionality and count match chunk count. Fail loudly.

### Stage 3 — Angle Definition & AI Recommendation
- Free-text prompt: *"What angle do you want for this campaign?"* (e.g. "urgency around phishing reporting", "reassuring tone for the new leave policy").
- **AI recommendation panel:** a Strategy Agent reads the indexed policy and proposes 3–5 candidate angles, each with: a one-line rationale, the clauses it would draw from, and a suggested tone. User picks one, edits it, or writes their own.
- Recommendations must cite which clauses motivated them. No ungrounded suggestions.

### Stage 4 — Template Selection
- Gallery of templates. **Each template exists in both landscape and portrait.** User selects template family; both orientations are produced.
- Templates declare their own **content contract**: slot names, character budgets per slot, image slot presence.
- **HARD:** character budget for a slot = the *tighter* of the two orientations. Content is written once to survive both.

### Stage 5 — Content Generation
Generation writes to a strict schema. Free prose is rejected.

```json
{
  "poster_id": "uuid",
  "angle": "string",
  "template_family": "string",
  "content": {
    "eyebrow":     { "text": "≤ 24 chars",  "citations": ["clause_id"] },
    "headline":    { "text": "≤ 48 chars",  "citations": ["clause_id"] },
    "subhead":     { "text": "≤ 90 chars",  "citations": ["clause_id"] },
    "body_points": [
      { "text": "≤ 110 chars", "citations": ["clause_id"] }
    ],
    "callout":     { "text": "≤ 70 chars",  "citations": ["clause_id"] },
    "cta":         { "text": "≤ 40 chars",  "citations": ["clause_id"] }
  },
  "coverage_map": { "clause_id": "covered | partial | omitted | not_applicable" },
  "placeholders_present": ["⟦ORG_001⟧"]
}
```
- **HARD:** `citations` may never be empty for any slot containing a factual claim. A purely rhetorical CTA ("Stay alert") may cite the clause it supports.

### Stage 6 — QA Agent Mesh
All QA agents run against the **sanitised** text and the retrieval report. Each returns `{verdict: pass|revise|reject, findings: [...], severity}`.

### Stage 7 — Render
React components consume the content object. Portrait and landscape variants of the same template consume the **identical** content object and differ only in layout, positioning, and line-break behaviour.

### Stage 8 — Rehydration (HARD)
- Only after all QA gates pass, walk the final content object and replace every placeholder with its original value from `redaction_map`.
- Rehydration applies to poster copy, alt text, export metadata, and filenames.
- **Rehydration Validator** then asserts: zero `⟦...⟧` tokens remain anywhere in the output; every placeholder that entered has a resolved counterpart; no placeholder resolved to the wrong category.

### Stage 9 — Export
- **PPTX:** editable text boxes with real, selectable, editable text. Fonts embedded. One slide per poster. Slide dimensions match orientation (13.33×7.5in landscape, 7.5×13.33in portrait). **Not** a flattened image.
- **JPG:** high-DPI (300 DPI print-equivalent) headless-browser render of the React component.
- Both formats available in **both** orientations. Four artefacts minimum per poster.
- Optional: PDF (print-ready, CMYK-safe) and PNG with transparency.

---

## 5. Agent Roster

Each agent has an explicit contract: **objective, inputs, outputs, constraints, failure modes, verdict authority.**

| Agent | Objective | Verdict Authority |
|---|---|---|
| **Ingest Agent** | Parse to structural tree, preserve hierarchy and offsets | Advisory |
| **Redaction Auditor** | **HARD gate.** Verify zero sensitive-value leakage pre-egress | **Blocking** |
| **Index Agent** | Structure-aware chunking, metadata, embedding, index validation | **Blocking** |
| **Retrieval Agent** | Iterative agentic retrieval; emit retrieval report | Advisory |
| **Strategy Agent** | Propose candidate campaign angles, each clause-grounded | Advisory |
| **Content Generator** | Write schema-conformant copy within char budgets, every slot cited | Advisory |
| **Groundedness Verifier** | For every generated claim, assert support in retrieved spans. Reject anything unsupported. | **Blocking** |
| **Citation Verifier** | Assert every citation resolves to a real clause_id, and that the cited clause *actually says* what the line claims. Catches citation drift. | **Blocking** |
| **Coverage / Completeness Agent** | Map every material clause of the policy against the campaign. Flag omissions — ensure no key obligation is silently dropped. Recommends additional posters if one cannot carry the whole policy. | **Blocking** |
| **Tone & Clarity Editor** | Readability, employee-appropriate register, adherence to chosen angle, no jargon | Revise-only |
| **Compliance Gate** | Final check: no legal overreach, no softening of a mandatory obligation, no invented consequence | **Blocking** |
| **Layout Fit Agent** | Verify copy fits both orientations without overflow or awkward breaks | Revise-only |
| **Rehydration Validator** | **HARD gate.** Zero unresolved/mis-mapped placeholders | **Blocking** |
| **Supervisor** | Holds full trace. Detects drift. Rewinds graph to any upstream node. Budget-bounded. | **Rewind authority** |

---

## 6. Graph Topology & Supervisor Behaviour

- The orchestration graph is a **DAG with cycles**: forward edges for normal flow, retry edges within a node, and **supervisor rewind edges** that can jump back to *any* prior node — not just the immediately preceding one.
- Example: if the Coverage Agent finds a missing obligation, the Supervisor may rewind not to generation but all the way to **retrieval**, because the gap is likely a retrieval failure, not a writing failure. This diagnosis is the Supervisor's job.

**Supervisor rules:**
1. Observes every node's inputs, outputs, verdict, and retrieval report.
2. On a blocking failure, diagnoses the **root cause node** and rewinds there with a corrective instruction injected into that node's context.
3. **Budget: 2 rewinds per node.** On the 3rd failure at the same node → halt and surface the diagnostic panel.
4. Also monitors global budget: total token spend, wall-clock, total rewinds across all nodes. Halts on ceiling breach.
5. Supervisor may **never** override a blocking gate. It can only re-run the work, not approve failing work.

**Checkpointing (HARD):**
Every node persists its full output state. Rewind and resume must be cheap — never a cold restart. State is keyed by `{run_id, node_id, attempt_n}`.

---

## 7. Human Intervention & Diagnostic Panel

When the Supervisor exhausts its budget, do **not** silently fail. Render a diagnostic panel showing:

- **Which node failed** and its position in the graph.
- **The input** that node received (sanitised view).
- **Every attempt** — full output of attempt 1, 2, 3 side by side.
- **The verdict and findings** for each attempt, from the agent that rejected it.
- **The Supervisor's diagnosis** of why it kept failing.
- **The retrieval spans** the node was working from.

The user may then:
1. **Accept** the best attempt as-is and continue.
2. **Edit** the output directly and continue from that node.
3. **Navigate to any upstream step**, adjust the prompt / angle / redaction / template, and **resume from that point** — with all downstream checkpoints invalidated and re-run, all upstream ones preserved.

Resumption must never require re-uploading the document or re-doing redaction.

---

## 8. Governance, Tracing & Self-Learning

**Trace record — emitted at every agent hop:**
```json
{
  "run_id": "uuid", "node_id": "string", "attempt": 1,
  "agent": "string", "timestamp": "iso8601",
  "input_hash": "sha256", "output": {},
  "retrieved_chunk_ids": ["..."],
  "verdict": "pass|revise|reject",
  "findings": [], "tokens_in": 0, "tokens_out": 0,
  "latency_ms": 0, "model": "string"
}
```
This is both the audit trail and the training signal.

**Self-learning (bounded, safe):**
- Maintain a **feedback store** capturing: user edits to generated copy, rejected outputs, accepted-after-N-attempts outputs, and QA findings that recur.
- These become **few-shot exemplars and dynamic rubric entries** injected into future runs — retrieved by similarity to the current policy type and angle.
- **HARD:** no weight updates, no fine-tuning loop, no automatic prompt mutation without a human-reviewed promotion step. Learning is retrieval-based and inspectable.
- Surface an admin view: "these 12 corrections are currently shaping generation" — with the ability to remove any of them.

**Metrics dashboard:** groundedness pass-rate, citation-drift rate, mean rewinds per run, coverage completeness %, redaction leakage incidents (target: 0), human-intervention rate, cost per poster.

---

## 9. Acceptance Criteria

The build is complete when all of the following hold:

1. A DOCX and a PDF policy both ingest with hierarchy preserved and clause IDs assigned.
2. Sensitive Terms panel supports pre-declared, auto-suggested, and cursor-selected masking, with global propagation and live occurrence counts.
3. Redaction Auditor demonstrably blocks egress on a planted leak.
4. Every chunk in the index passes boundary-integrity inspection — no mid-clause splits, no fragments, full metadata.
5. Retrieval demonstrably iterates and reformulates on a deliberately vague query.
6. Generated copy conforms to schema, respects character budgets, and every factual slot carries a resolving citation.
7. Clicking any poster line in the UI reveals the exact source clause and highlights it in the policy view.
8. Coverage Agent correctly flags a deliberately omitted obligation.
9. Groundedness Verifier correctly rejects a planted hallucination.
10. Supervisor demonstrably rewinds to a *non-adjacent* upstream node on an induced failure.
11. After 3 failures, the diagnostic panel renders with all attempts and permits resume-from-any-step.
12. Rehydration Validator confirms zero unresolved placeholders; final artefacts contain real company values.
13. PPTX exports contain **editable** text boxes in both orientations; JPG exports render at print DPI in both orientations.
14. Portrait and landscape posters contain byte-identical content, differing only in layout.
15. Full trace is queryable for any run, end to end.

---

## 10. Build Order

1. Ingest + structural tree + clause IDs
2. Redaction lifecycle + review UI + auditor gate *(build this before any model call exists)*
3. Chunking + metadata + index validation
4. Agentic retrieval loop + retrieval report
5. Content schema + generator
6. QA mesh, one agent at a time, each with a failing test case
7. Graph orchestration + checkpointing + Supervisor
8. Diagnostic panel + resume
9. React templates, both orientations
10. Rehydration + validator
11. Exports
12. Trace store, feedback store, metrics

Do not proceed to a stage until the previous stage's acceptance criteria pass.
