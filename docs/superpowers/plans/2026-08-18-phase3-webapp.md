# Phase 3 — Web App (API + React UI + Render/Export) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the user-facing platform: FastAPI service over the Phase 1/2 pipeline, React (Vite+TS+Tailwind) frontend with the full workflow — upload → redaction review UI → angle → template → run → preview with citation click-through → diagnostic panel → exports — plus React poster templates (both orientations) and Playwright JPG render.

**Architecture:** FastAPI holds per-project state (document, ledger, index, runs) in a `ProjectStore` persisted to a work dir. LLM resolution: `AnthropicLLM` when `ANTHROPIC_API_KEY` is set, else a deterministic `OfflineLLM` (extractive copy from retrieved chunks — keeps the whole app demoable with zero egress). React templates are the render source of truth; JPG export drives Playwright against the frontend's `/render` route at 300 DPI.

**Tech Stack:** FastAPI + uvicorn, python-multipart; React 18 + Vite + TypeScript + Tailwind; Playwright (python) for JPG.

**Spec:** `policy-poster-agentic-build-prompt.md` §4 stages 1.4/3/4/7/9, §7 diagnostic panel, acceptance #2, #7, #11, #13.

## Tasks

1. **OfflineLLM** (`llm_offline.py`): recognises the pipeline's prompt types (sufficiency / strategy / generator / QA) and emits valid, grounded JSON extractively from the excerpts embedded in the prompt. Tests: full pipeline run with OfflineLLM completes.
2. **API** (`api/` package): FastAPI app.
   - `POST /projects` (upload docx/pdf) → project_id; parses to tree.
   - `GET/POST/DELETE /projects/{id}/terms` — Sensitive Terms panel CRUD (term, category) with live occurrence counts; `POST /terms/preview` for count-before-commit.
   - `GET /projects/{id}/suggestions` — NER suggestions with confidence; accept/dismiss.
   - `GET /projects/{id}/document` — sanitized doc + occurrence spans + per-leaf clause spans for the review UI; `unreviewed_suggestions` counter.
   - `GET /projects/{id}/audit` — auditor report; `POST /audit/acknowledge`.
   - `POST /projects/{id}/index` — chunk+embed+validate (gate blocks if audit fails).
   - `GET /projects/{id}/angles` — Strategy Agent proposals; `GET /templates` — gallery contracts.
   - `POST /projects/{id}/runs` {angle, template_family} → run pipeline (background thread); `GET /runs/{run_id}` status/state/diagnostic; `POST /runs/{run_id}/resume` {from_node, edits}.
   - `GET /runs/{run_id}/poster` — content + citation resolution (clause_id → section path, text, char_span); `GET /runs/{run_id}/exports/{orientation}.pptx`.
   - `GET /runs/{run_id}/trace`.
   - Tests via httpx TestClient covering the golden path + audit-block.
3. **React app** (`frontend/`): Vite+TS+Tailwind scaffold; typed API client; screens: Upload, Redact (highlighted doc, text-selection popover: mask term/category, show-all-occurrences count, mask-everywhere; suggestions sidebar; unreviewed counter; unmask), Angle (AI proposals + free text), Templates (gallery, both orientations preview), Run (progress + diagnostic panel with attempts side-by-side + resume-from-node), Poster (landscape/portrait toggle, click line → source clause highlight), Exports.
4. **Poster React templates**: `default` family, landscape + portrait components consuming identical content JSON; `/render/:runId/:orientation` route with print-safe deterministic geometry.
5. **JPG export** (`export_jpg.py`): Playwright chromium at 300 DPI (deviceScaleFactor), screenshots `/render` route; API endpoint `GET /runs/{id}/exports/{orientation}.jpg`. Test skips when browsers absent.

Each task: failing test (backend) / build+typecheck (frontend) → implement → verify → commit.
