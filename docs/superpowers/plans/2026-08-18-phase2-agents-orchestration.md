# Phase 2 — Agents, Orchestration, Rehydration, Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build spec build-order stages 5–8, 10–11: content schema + generator, QA agent mesh, graph orchestrator with Supervisor rewind + checkpointing, rehydration + validator, PPTX/JPG export.

**Architecture:** All agents are pure functions `(state, llm) → verdict/output` wired into a hand-rolled DAG-with-cycles engine (spec permits "LangGraph-style or equivalent"). Checkpoints persist per `{run_id, node_id, attempt}` to a JSON store. Every LLM agent works only on sanitised text; rehydration is the final node behind all blocking gates.

**Tech Stack:** Python 3.11 (existing backend package), python-pptx, Playwright (JPG render deferred to Phase 3 when React templates exist — export module ships PPTX + render-callback seam now).

**Spec:** `policy-poster-agentic-build-prompt.md` §4 stages 3–9, §5, §6, §8.

## Global Constraints

- C1: Groundedness Verifier blocks any claim unsupported by retrieved spans.
- C4: every slot with factual claim carries ≥1 citation resolving to a real clause_id.
- C5: no invented statistics/legal citations/external references.
- C6: Supervisor max 2 rewinds per node; 3rd failure → halt with diagnostic. Supervisor may never override a blocking gate.
- C7: content generated once; portrait/landscape identical content object; slot budget = tighter of two orientations.
- Checkpoint every node output keyed `{run_id, node_id, attempt_n}`; rewind/resume never cold-restarts.
- Trace record emitted at every agent hop (spec §8 schema).
- Rehydration only after all QA gates pass; validator asserts zero `⟦...⟧` residue and no mis-mapped categories.

## File Structure

```
backend/src/policy_poster/
  content.py        # PosterContent schema, char budgets, template contracts
  agents/
    __init__.py
    strategy.py     # Strategy Agent: 3-5 clause-grounded angle proposals
    generator.py    # Content Generator: schema-conformant, cited copy
    qa.py           # Groundedness, Citation, Coverage, Tone, Compliance, LayoutFit
  orchestrator.py   # Graph engine: nodes, edges, Supervisor, budgets, checkpoints
  trace.py          # TraceRecord + TraceStore (jsonl)
  rehydration.py    # rehydrate() + RehydrationValidator
  export_pptx.py    # editable-text PPTX both orientations
backend/tests/
  test_content.py test_strategy.py test_generator.py test_qa.py
  test_orchestrator.py test_rehydration.py test_export_pptx.py
```

## Tasks (condensed — full spec text governs)

1. **content.py** — `TemplateContract` (slot budgets per orientation, effective budget = min), `Slot {text, citations}`, `PosterContent` with `validate(contract, known_clause_ids)` returning schema violations (over budget, empty citation on factual slot, unknown clause_id). Tests: budget enforcement, citation requirement, coverage_map shape.
2. **agents/strategy.py** — `propose_angles(index, llm, n)` → list of `{angle, rationale, clause_ids, tone}`; every proposal must cite clauses that exist; unparseable/uncited proposals dropped. Tests with MockLLM incl. dropped ungrounded proposal.
3. **agents/generator.py** — `generate_content(angle, template, retrieved_chunks, llm)` → PosterContent; prompt embeds slot budgets + chunk texts with clause_ids; output parsed via extract_json; schema violations → returns (None, violations) for retry edge. Tests: happy path, over-budget rejection, citation of unretrieved clause rejection.
4. **agents/qa.py** — each agent returns `Verdict {verdict: pass|revise|reject, findings: [{severity, detail, slot}], agent}`.
   - Groundedness: LLM judge per slot against retrieved span texts; planted hallucination test must reject (acceptance #9).
   - Citation: deterministic — citation resolves to real clause AND cited chunk text overlap check via LLM claim-support judgment; drift test.
   - Coverage: obligation-flagged chunks vs coverage_map; deliberately omitted obligation flagged (acceptance #8); recommends additional posters when omissions exceed capacity.
   - Tone: revise-only, LLM.
   - Compliance: blocking, LLM — softened obligation / invented consequence test.
   - LayoutFit: deterministic re-check of char budgets + widow/orphan heuristic (line-break simulation), revise-only.
5. **trace.py** — spec §8 record; `TraceStore.append/query(run_id)` jsonl under run dir. Test roundtrip.
6. **orchestrator.py** — `Node {name, fn, verdict_authority}`, `Graph {nodes, edges, conditional edges}`, `Supervisor` observing verdicts: on blocking failure → diagnose root-cause node (mapping table e.g. coverage failure → retrieval) → rewind with corrective instruction injected into node context; per-node rewind budget 2; global budgets (wall-clock, total rewinds); halt → `DiagnosticSnapshot {node, attempts[], verdicts, supervisor_diagnosis, retrieval_spans}`. Checkpoint store: JSON per {run_id,node_id,attempt}; resume from any node invalidates downstream only. Tests: rewind to non-adjacent node (acceptance #10), halt after 3rd failure with full diagnostic (acceptance #11), resume preserves upstream checkpoints, supervisor cannot override blocking gate.
7. **rehydration.py** — walk content object + metadata + filename; replace placeholders from ledger.redaction_map; validator: zero `⟦...⟧` residue anywhere, every entering placeholder resolved, category consistency. Tests: full resolution, planted unresolved placeholder caught, mis-mapped category caught (acceptance #12).
8. **export_pptx.py** — python-pptx; slide 13.33×7.5in landscape / 7.5×13.33in portrait; real text boxes per slot (eyebrow/headline/subhead/body/callout/cta); reopen file and assert text frames present + editable (acceptance #13 backend half). JPG render lands in Phase 3 with React templates.

Each task: failing test → implement → pass → commit.
