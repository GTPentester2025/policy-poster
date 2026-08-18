# Phase 1 — Core Pipeline (Ingest → Redaction → Index → Agentic Retrieval) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend library implementing build-order stages 1–4 of the spec: structural ingest with clause IDs, full redaction lifecycle with auditor gate, structure-aware chunked hybrid index with validation gate, and iterative agentic retrieval with retrieval reports.

**Architecture:** Pure-Python package `policy_poster` (no web layer yet — that is Phase 2). Every stage is a deterministic, testable module; LLM access goes through a `LLMClient` protocol so all tests run against a scripted `MockLLM` with zero egress. Redaction is recomputed from the original canonical text + a terms ledger, making global/retroactive masking and unmasking trivially deterministic.

**Tech Stack:** Python 3.11 (uv-managed venv), python-docx, PyMuPDF, spaCy (`en_core_web_sm`, local), LanceDB, rank-bm25, fastembed (prod embeddings; deterministic HashingEmbedder in tests), anthropic SDK, pytest.

**Spec:** `policy-poster-agentic-build-prompt.md` (repo root). This plan implements spec §4 Stage 0–2, §2 C2/C6-relevant plumbing, part of §5 roster (Ingest, Redaction Auditor, Index, Retrieval agents).

## Global Constraints

- C2 HARD: no raw sensitive token to any LLM API; sanitisation before first model call. Phase 1 tests use `MockLLM` only; `AnthropicLLM` exists but is never invoked in tests.
- Redaction map `{placeholder → original}` never serialised into any prompt or LLM-visible structure.
- Chunk boundaries align to semantic units; never split mid-sentence/clause/list-item. Target 200–500 est. tokens; boundary integrity beats size.
- Every chunk carries the full metadata object of spec §4 2.2.
- Retrieval is a loop, max 4 iterations, emits `retrieval_report`.
- Placeholders use `⟦CAT_NNN⟧` form, e.g. `⟦ORG_001⟧`.
- Python 3.11 via uv; all deps pinned in `pyproject.toml`.

## Later Phases (separate plans, not here)

- Phase 2: FastAPI app + React frontend (Sensitive Terms panel, interactive review UI, angle selection, template gallery, citation click-through).
- Phase 3: Strategy/Content/QA agent mesh, graph orchestrator, Supervisor rewind, checkpointing, diagnostic panel.
- Phase 4: React templates both orientations, rehydration + validator, PPTX/JPG export, trace/feedback/metrics.

## File Structure

```
backend/
  pyproject.toml
  src/policy_poster/
    __init__.py
    models.py        # ClauseNode, PolicyDocument, Block
    tree.py          # build_tree(blocks) → PolicyDocument, clause IDs, char spans
    docx_parser.py   # parse_docx(path) → PolicyDocument
    pdf_parser.py    # parse_pdf(path) → PolicyDocument
    redaction.py     # SensitiveTerm, RedactionLedger, apply_redaction, variants
    ner.py           # spaCy + regex suggesters → Suggestion list
    auditor.py       # audit(sanitized, ledger) → AuditReport (blocking gate)
    chunker.py       # Chunk dataclass + chunk_document()
    embedder.py      # Embedder protocol, HashingEmbedder, FastEmbedEmbedder
    index.py         # PolicyIndex (LanceDB + BM25 + RRF), validate_index gate
    llm.py           # LLMClient protocol, MockLLM, AnthropicLLM
    retrieval.py     # AgenticRetriever loop + RetrievalReport
  tests/
    conftest.py      # sample policy fixture builders (DOCX + PDF generated in-test)
    test_tree.py test_docx_parser.py test_pdf_parser.py
    test_redaction.py test_ner.py test_auditor.py
    test_chunker.py test_index.py test_retrieval.py
```

---

### Task 0: Scaffold

**Files:** Create `backend/pyproject.toml`, package skeleton, `.gitignore`.

- [ ] `git init` at repo root; `.gitignore` (venvs, `__pycache__`, `.lancedb`, `node_modules`, `dist`).
- [ ] `uv init` backend with `requires-python = ">=3.11,<3.12"`; deps: `python-docx`, `pymupdf`, `spacy`, `lancedb`, `rank-bm25`, `numpy`, `anthropic`; dev: `pytest`; extra `embeddings`: `fastembed`.
- [ ] `uv sync`; `uv run python -m spacy download en_core_web_sm` (or add model wheel URL as dep).
- [ ] Empty test run passes: `uv run pytest` → "no tests ran".
- [ ] Commit `chore: scaffold backend package`.

### Task 1: Structural models + tree builder

**Files:** Create `models.py`, `tree.py`, `tests/test_tree.py`.

**Interfaces (produced):**
```python
@dataclass
class Block:
    text: str
    kind: str                 # "heading" | "paragraph" | "list_item" | "table"
    level: int = 0            # 1..6 for headings, 0 otherwise
    number: str | None = None # printed numbering if detected: "3.2", "(a)"

@dataclass
class ClauseNode:
    clause_id: str            # hierarchical: "3", "3.2", "3.2.1"; preamble under "0"
    kind: str                 # "root"|"section"|"paragraph"|"list_item"|"table"
    heading: str | None
    number: str | None
    text: str                 # leaf body text; "" for containers
    char_span: tuple[int, int]  # into PolicyDocument.canonical_text
    children: list["ClauseNode"]
    def is_leaf(self) -> bool

@dataclass
class PolicyDocument:
    doc_id: str
    source_filename: str
    canonical_text: str
    root: ClauseNode
    def leaves(self) -> list[ClauseNode]
    def find(self, clause_id: str) -> ClauseNode | None
    def section_path(self, clause_id: str) -> list[str]   # heading texts, root→node

def build_tree(blocks: list[Block], doc_id: str, filename: str) -> PolicyDocument
```

Builder rules: heading stack by `level`; body blocks are leaves under current heading; leading body blocks before any heading go under synthetic section `0` ("Preamble"); clause IDs are positional per parent (`section 3`, its 2nd child → `3.2`); canonical_text is blocks joined by `\n` with spans recorded during concatenation; heading nodes' char_span covers heading line through last descendant.

- [ ] Failing tests: nested headings produce correct clause IDs; `leaves()` order = document order; `char_span` slices of `canonical_text` equal each block text; preamble handling; `section_path` returns heading texts.
- [ ] Implement; tests pass; commit `feat: structural tree with clause IDs and char spans`.

### Task 2: DOCX parser

**Files:** Create `docx_parser.py`, `tests/test_docx_parser.py` (fixture built with python-docx in `conftest.py`).

`parse_docx(path) -> PolicyDocument`: iterate body elements in order (paragraphs and tables interleaved via `document.element.body`); style "Heading N" → `Block(kind="heading", level=N)`; list styles / numPr → `list_item`; each table → single `Block(kind="table")` with rows serialised `cell | cell` per line; numbering regex `^\s*(\d+(?:\.\d+)*|\([a-z]\)|\([ivx]+\))[.)]?\s+` captured into `Block.number`.

- [ ] Fixture: generated DOCX with H1/H2, paragraphs, a bullet list, a 2×2 table. Tests assert hierarchy, table serialisation, numbering detection, non-empty spans.
- [ ] Implement; pass; commit `feat: DOCX structural parser`.

### Task 3: PDF parser

**Files:** Create `pdf_parser.py`, `tests/test_pdf_parser.py` (fixture PDF generated with PyMuPDF: body 11pt, H1 18pt bold, H2 14pt bold).

`parse_pdf(path) -> PolicyDocument`: extract span dicts via `page.get_text("dict")`; body size = modal font size; heading if `size ≥ body*1.25` (level by size tier, larger→1); consecutive same-style lines merged into one block; bullets (`•`, `-`, numbering regex) → `list_item`.

- [ ] Tests: two-level hierarchy detected; paragraph merge; leaves ordered.
- [ ] Implement; pass; commit `feat: layout-aware PDF parser with heading detection`.

### Task 4: Redaction ledger + deterministic replacement

**Files:** Create `redaction.py`, `tests/test_redaction.py`.

**Interfaces (produced):**
```python
PLACEHOLDER_RE = re.compile(r"⟦[A-Z]+_\d{3}⟧")
CATEGORY_PREFIX = {"org": "ORG", "person": "PERSON", "system": "SYSTEM", "client": "CLIENT",
                   "domain": "DOMAIN", "address": "ADDR", "employee_id": "ID", "email": "EMAIL",
                   "phone": "PHONE", "location": "LOC", "custom": "TERM"}

@dataclass
class SensitiveTerm:
    term: str; category: str; placeholder: str; variants: list[str]

class RedactionLedger:
    def add(self, term: str, category: str) -> SensitiveTerm   # same normalised base → same placeholder
    def remove(self, placeholder: str) -> None                 # unmask
    @property
    def redaction_map(self) -> dict[str, str]                  # placeholder → original (server-side only)

@dataclass
class Occurrence: placeholder: str; original: str; start: int; end: int  # span in sanitized text

@dataclass
class RedactionResult: sanitized_text: str; occurrences: list[Occurrence]

def apply_redaction(text: str, ledger: RedactionLedger) -> RedactionResult
def variants_for(term: str) -> list[str]
```

`variants_for`: legal-suffix family — strip/permute `private limited, pvt. ltd., pvt ltd, limited, ltd., ltd, inc., inc, llc, llp, plc, corp., corp, co.`; include bare base name; punctuation-tolerant. `apply_redaction`: longest-match-first, case-insensitive, word-boundary, global (tables/headers included since it runs over any text), single pass with combined regex; occurrences carry sanitized-text spans for UI highlight. Redaction always recomputed from original text → retroactive masking and unmasking are ledger edits + reapply.

- [ ] Failing tests: `"XYZ Private Limited"`, `"XYZ Pvt. Ltd."`, `"XYZ Pvt Ltd"`, `"XYZ"` all → same `⟦ORG_001⟧`; global replacement incl. repeated hits; distinct terms get distinct counters per category; remove+reapply restores text; occurrence spans index correctly into sanitized text; longest-first (no `⟦ORG_001⟧ Private Limited` residue).
- [ ] Implement; pass; commit `feat: deterministic redaction with variant normalisation`.

### Task 5: Local NER + regex suggesters

**Files:** Create `ner.py`, `tests/test_ner.py`.

```python
@dataclass
class Suggestion: text: str; label: str; category: str; count: int; confidence: float; spans: list[tuple[int,int]]
def suggest_entities(text: str, ledger: RedactionLedger, nlp=None) -> list[Suggestion]
```
spaCy `en_core_web_sm` (lazy-loaded module singleton, injectable for tests) for ORG/PERSON/GPE/MONEY/DATE; deterministic regexes for EMAIL/PHONE. Skip anything already matching ledger variants or placeholder tokens. Confidence heuristic: 0.9 multi-token ORG/PERSON, 0.75 single-token, 0.95 regex hits. Never auto-applied — suggestions only.

- [ ] Tests: email+phone regex found with counts; ledger-covered terms excluded; spaCy path returns ORG suggestion on obvious fixture sentence.
- [ ] Implement; pass; commit `feat: local NER and pattern suggesters`.

### Task 6: Redaction Auditor gate

**Files:** Create `auditor.py`, `tests/test_auditor.py`.

```python
@dataclass
class AuditFinding: kind: str; severity: str  # "hard"|"warning"
                    detail: str; span: tuple[int,int]
@dataclass
class AuditReport:
    findings: list[AuditFinding]
    @property
    def passed(self) -> bool          # no hard findings
    @property
    def blocking(self) -> bool        # hard findings OR unacknowledged warnings
def audit_sanitized(sanitized_text: str, ledger: RedactionLedger,
                    acknowledged: set[str] = frozenset()) -> AuditReport
```
Hard: any ledger original/variant surviving (case-insensitive); email/phone/PAN (`[A-Z]{5}\d{4}[A-Z]`)/Aadhaar (`\d{4}\s?\d{4}\s?\d{4}`) shapes. Warning: capitalised multi-word sequences (≥2 Titlecase tokens) not in ledger, not sentence-initial-only, not placeholder-adjacent — block until acknowledged (spec 1.5 → return to review UI).

- [ ] Tests: planted leak (raw company name survives) → hard finding, `passed=False` (spec acceptance #3); clean text passes; Aadhaar/PAN shapes caught; unacknowledged Titlecase pair blocks, acknowledged passes.
- [ ] Implement; pass; commit `feat: redaction auditor blocking gate`.

### Task 7: Structure-aware chunker

**Files:** Create `chunker.py`, `tests/test_chunker.py`.

```python
@dataclass
class Chunk:
    chunk_id: str; clause_ids: list[str]; section_path: list[str]; heading_context: str
    char_span: tuple[int, int]        # into ORIGINAL canonical_text (citation anchor)
    chunk_type: str                   # "clause"|"list"|"table"|"definition"|"preamble"
    obligation_flag: bool; contains_placeholder: list[str]
    prev_chunk_id: str | None; next_chunk_id: str | None
    text: str                         # sanitized raw text (retrieve this)
    enriched_text: str                # "sec > path: text" (embed this)
def chunk_document(doc: PolicyDocument, ledger: RedactionLedger) -> list[Chunk]
def est_tokens(text: str) -> int      # ~len/4
```
Rules: leaves are atomic (never split); accumulate consecutive same-parent leaves until adding next would exceed 500 est. tokens; oversize single leaf stays whole; a whole bullet-list run is one chunk; each table one chunk; trailing chunk < 60 tokens merges into previous same-parent chunk; obligation regex `\b(must|shall|required|prohibited|may not|must not|mandatory|obligated)\b/i`; sanitized via `apply_redaction` per chunk text; `contains_placeholder` from `PLACEHOLDER_RE`; prev/next linked after assembly; `chunk_type` "definition" when heading contains "definition", "preamble" for section 0.

- [ ] Tests: no chunk ends mid-sentence (endswith `.:;` or list/table); list stays together; table isolated; big clause whole; tiny trailing merged; every leaf clause_id ∈ exactly ≥1 chunk; enrichment prefix correct; placeholders detected.
- [ ] Implement; pass; commit `feat: structure-aware chunker with metadata`.

### Task 8: Embedder + LanceDB/BM25 index + validation gate

**Files:** Create `embedder.py`, `index.py`, `tests/test_index.py`.

```python
class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]
class HashingEmbedder:      # deterministic char-trigram hashing, dim=256, unit-norm — tests
class FastEmbedEmbedder:    # BAAI/bge-small-en-v1.5 via fastembed — prod, lazy import

class PolicyIndex:
    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder, db_dir: str) -> "PolicyIndex"
    def dense_search(self, query: str, k: int) -> list[tuple[str, float]]     # (chunk_id, score)
    def keyword_search(self, query: str, k: int) -> list[tuple[str, float]]   # BM25
    def hybrid_search(self, query: str, k: int) -> list[Chunk]                # RRF-fused, rrf_k=60
    def get(self, chunk_id: str) -> Chunk | None
    def neighbors(self, chunk_id: str) -> list[Chunk]                         # prev + next
    def by_clause_prefix(self, prefix: str) -> list[Chunk]                    # cross-ref expansion

@dataclass
class IndexValidation: passed: bool; errors: list[str]
def validate_index(doc: PolicyDocument, chunks: list[Chunk], index: PolicyIndex) -> IndexValidation
```
Validation asserts: every leaf clause in ≥1 chunk; no empty/whitespace chunk text; no duplicate chunk_id or duplicate text; vector row count == chunk count; embedding dim consistent. Fail loudly (errors list).

- [ ] Tests (HashingEmbedder + tmp LanceDB dir): build+get roundtrip preserves metadata; dense finds semantically-matching-by-overlap chunk; keyword finds exact term; hybrid RRF merges; neighbors/by_clause_prefix; validation passes on good index and reports each induced defect (dropped clause, empty chunk, dup id).
- [ ] Implement; pass; commit `feat: hybrid LanceDB+BM25 index with validation gate`.

### Task 9: LLM provider abstraction

**Files:** Create `llm.py`, `tests/test_llm.py`.

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str
class MockLLM:               # queue of scripted responses; records calls; raises when exhausted
class AnthropicLLM:          # anthropic SDK; model from env/ctor; NEVER called in tests
def extract_json(text: str) -> dict   # tolerant: strips fences, finds first {...}
```
Consult claude-api skill for current model id/params before writing `AnthropicLLM`.

- [ ] Tests: MockLLM replay + call recording; `extract_json` handles fenced / prefixed JSON.
- [ ] Implement; pass; commit `feat: LLM provider abstraction with mock`.

### Task 10: Agentic retrieval loop + report

**Files:** Create `retrieval.py`, `tests/test_retrieval.py`.

```python
@dataclass
class RetrievalIteration:
    query: str; retrieved_ids: list[str]; kept_ids: list[str]
    discarded: list[dict]     # {chunk_id, reason}
    sufficient: bool; refined_query: str | None
@dataclass
class RetrievalReport:
    intent: str; iterations: list[RetrievalIteration]
    consulted_ids: list[str]; retained_ids: list[str]; expanded_ids: list[str]
class AgenticRetriever:
    def __init__(self, index: PolicyIndex, llm: LLMClient, k: int = 8, max_iterations: int = 4)
    def retrieve(self, intent: str) -> tuple[list[Chunk], RetrievalReport]
```
Loop per spec 2.4: hybrid search → LLM sufficiency judgment (JSON: `{"sufficient": bool, "keep": [...], "discard": [{"chunk_id","reason"}], "refined_query": str|null}`) → if insufficient and iteration < 4, re-retrieve with refined query. After loop: cross-ref expansion via regex `(?:see|refer to|under|per)\s+(?:section|clause)?\s*(\d+(?:\.\d+)*)` over kept text → `by_clause_prefix`; plus prev/next neighbors of kept chunks. Only sanitized chunk text enters prompts; report lists every consulted/retained/discarded id with reasons.

- [ ] Tests (MockLLM scripted): sufficient-on-first-pass → 1 iteration; insufficient→refined→sufficient → 2 iterations with both queries in report (acceptance #5: demonstrable reformulation); hard cap at 4; cross-ref "see 4.1" pulls 4.1 chunk into `expanded_ids`; malformed LLM JSON → treat as sufficient-with-all-kept (fail-safe) and note in report.
- [ ] Implement; pass; commit `feat: agentic retrieval loop with retrieval report`.

### Task 11: Phase-1 integration test

**Files:** Create `tests/test_pipeline_integration.py`.

- [ ] End-to-end on generated DOCX fixture: parse → ledger(seed company name) → NER suggest → audit (assert planted leak blocks, then fix, pass) → chunk → index → validate → agentic retrieve (MockLLM) → assert retrieval report complete and no raw sensitive value appears in any prompt MockLLM recorded (C2 regression test).
- [ ] Pass; commit `test: phase 1 end-to-end integration`.

## Self-Review Notes

- Spec coverage: Stage 0 → Tasks 1–3; Stage 1.1–1.2 → Task 4; 1.3 → Task 5; 1.5 → Task 6; 2.1–2.3 → Task 7; 2.5 + hybrid → Task 8; 2.4 → Tasks 9–10. Stage 1.4 (review UI) is Phase 2 frontend; its backend primitives (occurrence spans, live counts, retroactive global apply, unmask) are delivered by Task 4/5 interfaces.
- Type consistency: `RedactionLedger` consumed by Tasks 5–7; `Chunk` consumed by 8/10; `PolicyIndex` consumed by 10; names match throughout.
