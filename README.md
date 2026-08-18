# Policy Poster — Policy-to-Poster Agentic Platform

Turns an internal company policy (DOCX/PDF) into awareness posters and
newsletter panels that are **strictly grounded**, **fully cited**,
**confidentiality-safe**, and **complete** — per
`policy-poster-agentic-build-prompt.md` (the authoritative spec).

## Architecture

```
frontend/  React 18 + Vite + TS + Tailwind — workflow UI, poster templates
           (render source of truth), /render route for JPG capture
backend/   Python 3.11 (uv) — the entire pipeline as a tested library + FastAPI
  src/policy_poster/
    models, tree, docx_parser, pdf_parser     Stage 0  structural ingest, clause IDs
    redaction, ner, auditor                   Stage 1  ledger, local NER, HARD egress gate
    chunker, embedder, index                  Stage 2  boundary-safe chunks, hybrid RRF index
    retrieval                                 Stage 2  agentic loop, retrieval report
    agents/ (strategy, generator, qa)         Stages 3,5,6  angle proposals, cited copy, QA mesh
    orchestrator, pipeline, trace             §6  graph + Supervisor rewind + checkpoints
    rehydration                               Stage 8  HARD placeholder resolution + validator
    export_pptx, export_jpg                   Stage 9  editable PPTX + 300 DPI Playwright JPG
    feedback, metrics                         §8  human-gated exemplars, governance metrics
    llm / llm_offline                         Claude API (opus 5, refusal fallbacks) / zero-egress demo
    api/                                      FastAPI: app (API), serve (API + SPA single origin)
```

## Confidentiality model (C2)

No text reaches any LLM until the **Redaction Auditor** passes: pre-declared
terms + local spaCy NER suggestions + cursor-selection masking build a ledger;
deterministic global replacement produces the sanitized text; the auditor
blocks indexing (HTTP 409) while any hard finding or unreviewed warning
remains. The `redaction_map` never enters a prompt; rehydration happens after
all QA gates, validated to zero residual placeholders. `MockLLM` call
recording asserts this end-to-end (`test_pipeline_integration.py`).

## Run it

```powershell
# backend deps (uv manages python 3.11)
cd backend
uv sync --dev
uv run python -m spacy download en_core_web_sm
uv run playwright install chromium      # only needed for JPG export

# frontend
cd ../frontend
npm install
npm run build                            # serve.py serves the built app

# start (single origin: UI + API)
cd ../backend
uv run uvicorn policy_poster.api.serve:app --port 8000
# open http://127.0.0.1:8000
```

- **Model-independent.** The pipeline talks to a provider-neutral
  `LLMClient` protocol; pick the AI in the UI (⚙ AI provider) or via env:
  - `anthropic` (Claude), `gemini` (Google), or any **OpenAI-compatible**
    endpoint: `openai`, `groq`, `mistral`, `deepseek`, `together`,
    `openrouter`, `xai`, and local servers `ollama` / `lmstudio` / `vllm` /
    `custom` (own base URL; keyless local servers supported).
  - Settings API: `GET/POST /api/settings/llm`, `POST /api/settings/llm/test`
    (live probe), `POST /api/settings/llm/models` (catalog). API keys are
    write-only — never echoed to the client.
  - Base URLs are normalized (bare host, API root, or pasted
    `/chat/completions` all work); reasoning-model token-param shape
    (`max_tokens` vs `max_completion_tokens`) self-heals with a cached retry.
  - Env: `POLICY_POSTER_LLM=<provider>`, `POLICY_POSTER_LLM_MODEL`,
    `POLICY_POSTER_LLM_BASE_URL`, `POLICY_POSTER_LLM_API_KEY`. Default:
    `anthropic` if `ANTHROPIC_API_KEY` is set, else `offline` — a
    deterministic extractive stand-in that runs the whole flow with zero
    egress.
- `POLICY_POSTER_EMBEDDER=fastembed` switches embeddings to a local ONNX
  model (`uv sync --extra embeddings`); default is a deterministic hashing
  embedder.
- Frontend dev loop: `npm run dev` (proxies `/api` to :8000).

## Tests

```powershell
cd backend
uv run pytest                 # 130 tests; JPG e2e auto-skips without chromium/dist
```

Planted-failure tests cover the spec's acceptance criteria: auditor blocks a
planted leak (#3), retrieval reformulates on a vague query (#5), Groundedness
rejects a planted hallucination (#9), Coverage flags an omitted obligation
(#8), Supervisor rewinds to a non-adjacent node (#10), halts on the 3rd
failure with a full diagnostic (#11), rehydration validates zero residue
(#12), PPTX text boxes are editable in both orientations with identical
content (#13/#14), traces are queryable per run (#15).

## Plans

Implementation plans live in `docs/superpowers/plans/` (phase 1 core
pipeline, phase 2 agents/orchestration, phase 3 web app).
