# Production-Grade Roadmap — Content Quality, Agent Reliability, Observability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement phase-by-phase. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Take the platform from working-demo to production grade along the three axes the user named, in priority order: (1) content accuracy/quality, (2) UX + full visibility into what is happening, (3) hardened agents/infra.

**Diagnosis (from real run traces, 2026-08-19):** generation failures were dominated by
(a) character-budget overflows — LLMs cannot count characters, and whole-poster
regeneration burned all supervisor attempts; (b) citation drift — right copy,
wrong clause_id; (c) full-regen churn — retries discarded slots that had
already passed. **P0 fixes shipped:** budgets now repair (targeted shorten →
deterministic word-trim guarantee, never reject); QA rejects trigger
slot-targeted repair that freezes passing slots; in-node repair pass before any
supervisor rewind.

**Spec:** `policy-poster-agentic-build-prompt.md` remains authoritative — nothing below may weaken C1–C7.

---

## Phase A — Content accuracy & quality (the product)

### A1. Native structured outputs per provider ⭐ highest-impact remaining fix
Free-form JSON parsing is the #1 residual failure source for small/medium models.
- `OpenAICompatLLM.complete_json(schema)`: send `response_format: {type: "json_schema", json_schema: {...}}`; on 400 (endpoint doesn't support it) fall back to `{type: "json_object"}`, then to plain prompt + `extract_json` — capability cached per model like the token-param healing.
- `AnthropicLLM`: tool-use forced JSON (`output_config.format`); `GeminiLLM`: `responseSchema` in `generationConfig`.
- Generator, strategy, retrieval-judge, and all QA verdicts move to `complete_json`.
- Test: mock endpoints for each dialect; fallback ladder test.

### A2. Clause-first generation (kills citation drift structurally)
Two-stage generation instead of one shot:
1. **Plan call:** given retrieved chunks, pick per slot the clause_id + the exact quote fragment the slot will be built from → `{slot: {clause_id, source_quote}}`.
2. **Write call(s):** write each slot **from its chosen quote** ("rewrite this clause fragment as a headline ≤48 chars"). Citation = the clause chosen in step 1, bound by construction, not asserted after the fact.
- Keeps single-call mode as fallback for strong models (config flag `generation_mode: clause_first | single_shot`).
- Test: drift fixture that previously failed now passes without a citation-verifier rewind.

### A3. Retrieval quality
- **Default real embeddings:** bundle `fastembed` in core deps so the no-provider-embeddings path (OpenRouter, Anthropic) gets semantic vectors, not hashing. Hashing stays only as an emergency fallback.
- **Contextual enrichment at index time:** one batched LLM call writes a 1-line "where this sits in the policy" context per chunk, prepended before embedding (Anthropic contextual-retrieval pattern). Skipped in offline mode.
- **LLM rerank:** after RRF fusion, one listwise rerank call ordering the top-16 by relevance to the intent; keep top-k.
- **Query decomposition:** angle → 2–4 sub-intents (obligations, deadlines, consequences, who-it-applies-to); retrieve per sub-intent; union. Directly improves Coverage-gate pass rate on multi-obligation policies.
- Tests: needle-in-haystack fixture (50-clause synthetic policy, one target clause) must hit top-3; decomposition fixture.

### A4. QA calibration
- All verdicts via structured outputs (A1).
- Rubrics + 2 few-shot examples per verifier (one clear pass, one clear fail) — cuts false rejects like tone findings leaking into citation verdicts.
- Groundedness/citation verifiers get the *specific slot→quote map* from A2, so they verify against the chosen quote (cheap, precise) instead of re-reading everything.
- Merge Tone+Compliance into one call with two verdict sections (halves cost/latency; authorities unchanged).
- Confidence field on findings; only `high` blocks, `low` becomes advisory (surfaced in UI, stored in feedback).

### A5. Model routing per role (mirror of reference app's content/vision/image)
- Settings gain `roles: {generate: model, verify: model, utility: model}` — strong model writes, cheap model judges/shortens/enriches; blank = main model.
- UI: three optional dropdowns under ⚙ AI provider (same load-models catalog).

### A6. Evaluation harness (the thing that makes "best outputs" measurable)
- `evals/` package: golden corpus (3 synthetic policies: HR leave, infosec, AI governance) + planted defects (hallucination, omitted obligation, drifted citation, leak).
- Score per run: completion rate, groundedness pass-rate, citation accuracy, budget conformance, coverage %, tokens, wall-clock.
- `uv run python -m policy_poster.evals --provider offline|<real>` → scoreboard table; CI runs offline; before/after comparison is the gate for every prompt change.
- Acceptance: eval suite runs green offline; scoreboard has a baseline committed.

## Phase B — UX & full visibility

### B1. SSE live stream (replace polling)
- `GET /runs/{id}/stream` — Server-Sent Events from the existing on_event feed + heartbeat; frontend EventSource with reconnect + history backfill (dedupe by event index). Poll stays as fallback.

### B2. Deep observability per node ("what exactly is happening")
- Egress log (reference-app pattern): every LLM call recorded — masked system/user prompt, response, tokens (from provider `usage`), latency, status — keyed {run_id, node, attempt, call_n}. Never breaks the call path.
- Run detail drawer in UI: click any timeline node → its actual prompts/responses (sanitized), retrieval iterations (query → kept/discarded with reasons), token + cost per node (per-model rate table), attempt diffs (slot-level diff between attempt N and N+1).
- "Download full prompt chain" button per run (jsonl).

### B3. Content editing loop (human in the loop where it matters)
- Poster screen: inline slot editing with live char-budget meters; edited copy re-runs QA gates only (resume from qa_groundedness with edits — plumbing exists); accepted edits auto-recorded to the feedback store as `user_edit` for exemplar promotion.

### B4. Campaign mode (coverage-driven multi-poster)
- When Coverage finds more obligations than one poster carries, generate a poster *set*: partition obligations → one run per partition → campaign view listing posters with a combined coverage map (spec §5 Coverage "recommends additional posters").

### B5. UX polish backlog
- Toasts for saves/errors (aria-live), keyboard shortcuts, upload progress for large PDFs, empty states everywhere, mobile-usable review screen, dark-mode pass or explicit single-theme statement, focus management on step transitions.

## Phase C — Agent/infra hardening

### C1. Provider resilience
- Shared retry layer: 3 attempts, exponential backoff + jitter, honor `Retry-After`, retry only 429/5xx/network; per-run circuit breaker (N consecutive provider failures → fail fast with clear error instead of grinding through nodes).
- Per-call timeout budget; run-level wall-clock ceiling already exists (Supervisor) — wire a real value.

### C2. Durable state (top "amateur" gap)
- SQLite (or sqlite-backed docstore) for projects: document, ledger, acknowledged set, chunks, embedding source, runs/outcomes. Server restart currently loses all projects — after C2, refresh/restart resumes exactly where the user was. LanceDB dirs already persist; reconnect instead of rebuild.
- Run resume across restarts (checkpoints already on disk; rebuild Run objects lazily).

### C3. Ops
- Structured logging (json), request ids, `/healthz`; graceful shutdown draining run threads; run cancellation endpoint + UI button; job queue with max concurrent runs; pyproject `policy-poster serve` entrypoint; Dockerfile (backend + built frontend); version stamp in UI footer.

### C4. Security posture (still single-user local by design)
- Session-only API-key mode (reference-app pattern: key in browser sessionStorage, per-request header, never persisted) as an opt-in toggle for shared machines; CORS tightened to same-origin; upload size limits; path traversal audit on export filenames (rehydrated filenames contain company names — sanitize for filesystem).

## Ordering & effort

| Order | Item | Why first | Size |
|---|---|---|---|
| 1 | A1 structured outputs | kills remaining generation failures | M |
| 2 | A3 fastembed default + rerank | retrieval floor for every provider | S–M |
| 3 | B2 egress log + node drawer | can't tune what you can't see | M |
| 4 | A6 eval harness | every later change gets measured | M |
| 5 | A2 clause-first generation | structural fix for drift | M |
| 6 | C2 durable state | restart safety, real-app feel | M |
| 7 | A4 QA calibration + A5 role routing | quality/cost tuning on top of evals | M |
| 8 | B1 SSE + B3 edit loop | visibility + human loop | M |
| 9 | B4 campaign mode | coverage completeness at scale | M |
| 10 | C1/C3/C4 hardening | production ops | M |

Each item lands with failing test → implement → eval scoreboard unchanged-or-better → commit.
