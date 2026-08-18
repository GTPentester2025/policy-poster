"""API routes. Enforcement points live here: no indexing (and therefore no
model-visible artefact) until the Redaction Auditor passes (C2)."""

from __future__ import annotations

import os
import tempfile
import threading
import uuid

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agents.strategy import propose_angles
from ..auditor import audit_sanitized
from ..chunker import chunk_document
from ..content import DEFAULT_CONTRACT, PosterContent, TemplateContract
from ..docx_parser import parse_docx
from ..embedder import Embedder, HashingEmbedder
from ..index import PolicyIndex, validate_index
from ..llm import AnthropicLLM, LLMClient
from ..llm_offline import OfflineLLM
from ..ner import suggest_entities
from ..pdf_parser import parse_pdf
from ..pipeline import run_poster_pipeline
from ..redaction import apply_redaction
from ..trace import TraceStore
from .store import Project, ProjectStore, Run

TEMPLATE_FAMILIES: dict[str, TemplateContract] = {
    "default": DEFAULT_CONTRACT,
    "bold-banner": TemplateContract(
        family="bold-banner",
        budgets_landscape={"eyebrow": 20, "headline": 42, "subhead": 80,
                           "body_point": 100, "callout": 60, "cta": 32},
        budgets_portrait={"eyebrow": 20, "headline": 38, "subhead": 76,
                          "body_point": 96, "callout": 56, "cta": 32},
        max_body_points=3,
    ),
}


def _make_llm() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("POLICY_POSTER_LLM") != "offline":
        return AnthropicLLM()
    return OfflineLLM()


def _make_embedder() -> Embedder:
    if os.environ.get("POLICY_POSTER_EMBEDDER") == "fastembed":
        from ..embedder import FastEmbedEmbedder

        return FastEmbedEmbedder()
    return HashingEmbedder()


class TermIn(BaseModel):
    term: str
    category: str


class AcknowledgeIn(BaseModel):
    surfaces: list[str]


class RunIn(BaseModel):
    angle: str
    template_family: str = "default"


class ResumeIn(BaseModel):
    from_node: str
    edits: dict = {}


def create_app(data_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="Policy Poster")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    store = ProjectStore(data_dir or os.environ.get(
        "POLICY_POSTER_DATA",
        os.path.join(tempfile.gettempdir(), "policy_poster_data"),
    ))
    app.state.store = store

    def project_or_404(project_id: str) -> Project:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        return project

    def run_or_404(run_id: str) -> tuple[Project, Run]:
        found = store.find_run(run_id)
        if found is None:
            raise HTTPException(404, "run not found")
        return found

    # -- Stage 0: ingest ----------------------------------------------------

    @app.post("/projects")
    async def upload(file: UploadFile):
        suffix = os.path.splitext(file.filename or "")[1].lower()
        if suffix not in (".docx", ".pdf"):
            raise HTTPException(400, "only .docx and .pdf are supported")
        project = store.create()
        path = os.path.join(project.work_dir, f"source{suffix}")
        with open(path, "wb") as f:
            f.write(await file.read())
        project.doc = parse_docx(path) if suffix == ".docx" else parse_pdf(path)
        return {
            "project_id": project.project_id,
            "filename": file.filename,
            "clauses": len(project.doc.leaves()),
        }

    # -- Stage 1: redaction lifecycle --------------------------------------

    @app.get("/projects/{project_id}/terms")
    def list_terms(project_id: str):
        project = project_or_404(project_id)
        result = apply_redaction(project.doc.canonical_text, project.ledger)
        counts: dict[str, int] = {}
        for occ in result.occurrences:
            counts[occ.placeholder] = counts.get(occ.placeholder, 0) + 1
        return [
            {"term": t.term, "category": t.category, "placeholder": t.placeholder,
             "variants": t.variants, "occurrences": counts.get(t.placeholder, 0)}
            for t in project.ledger.terms
        ]

    @app.post("/projects/{project_id}/terms")
    def add_term(project_id: str, body: TermIn):
        project = project_or_404(project_id)
        try:
            entry = project.ledger.add(body.term, body.category)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        project.chunks, project.index = [], None  # ledger changed → re-index required
        return {"placeholder": entry.placeholder}

    @app.post("/projects/{project_id}/terms/preview")
    def preview_term(project_id: str, body: TermIn):
        """Live `Show all occurrences (n found)` before committing a mask."""
        project = project_or_404(project_id)
        from ..redaction import RedactionLedger

        probe = RedactionLedger()
        probe.add(body.term, body.category)
        result = apply_redaction(project.doc.canonical_text, probe)
        return {"occurrences": len(result.occurrences)}

    @app.delete("/projects/{project_id}/terms/{placeholder}")
    def remove_term(project_id: str, placeholder: str):
        project = project_or_404(project_id)
        project.ledger.remove(placeholder)
        project.chunks, project.index = [], None
        return {"removed": placeholder}

    @app.get("/projects/{project_id}/suggestions")
    def suggestions(project_id: str):
        project = project_or_404(project_id)
        items = suggest_entities(project.doc.canonical_text, project.ledger)
        out = [
            {"text": s.text, "label": s.label, "category": s.category,
             "count": s.count, "confidence": s.confidence, "spans": s.spans}
            for s in items if s.text not in project.dismissed_suggestions
        ]
        unreviewed = sum(1 for s in out if s["confidence"] >= 0.9)
        return {"suggestions": out, "unreviewed_high_confidence": unreviewed}

    @app.post("/projects/{project_id}/suggestions/dismiss")
    def dismiss_suggestion(project_id: str, body: AcknowledgeIn):
        project = project_or_404(project_id)
        project.dismissed_suggestions.update(body.surfaces)
        return {"dismissed": sorted(project.dismissed_suggestions)}

    @app.get("/projects/{project_id}/document")
    def document_view(project_id: str):
        project = project_or_404(project_id)
        result = apply_redaction(project.doc.canonical_text, project.ledger)
        category_of = {t.placeholder: t.category for t in project.ledger.terms}
        return {
            "sanitized_text": result.sanitized_text,
            "occurrences": [
                {"placeholder": o.placeholder, "original_masked": True,
                 "category": category_of.get(o.placeholder, "custom"),
                 "start": o.start, "end": o.end}
                for o in result.occurrences
            ],
            "clauses": [
                {"clause_id": leaf.clause_id, "kind": leaf.kind,
                 "char_span": leaf.char_span,
                 "text": apply_redaction(leaf.text, project.ledger).sanitized_text,
                 "section_path": project.doc.section_path(leaf.clause_id)}
                for leaf in project.doc.leaves()
            ],
        }

    @app.get("/projects/{project_id}/audit")
    def audit(project_id: str):
        project = project_or_404(project_id)
        sanitized = apply_redaction(project.doc.canonical_text, project.ledger).sanitized_text
        report = audit_sanitized(sanitized, project.ledger, project.acknowledged)
        return {
            "passed": report.passed,
            "blocking": report.blocking,
            "findings": [
                {"kind": f.kind, "severity": f.severity, "detail": f.detail,
                 "span": f.span,
                 "acknowledged": getattr(f, "acknowledged", False)}
                for f in report.findings
            ],
        }

    @app.post("/projects/{project_id}/audit/acknowledge")
    def acknowledge(project_id: str, body: AcknowledgeIn):
        project = project_or_404(project_id)
        project.acknowledged.update(body.surfaces)
        return {"acknowledged": sorted(project.acknowledged)}

    # -- Stage 2: index (gated on the auditor) ------------------------------

    @app.post("/projects/{project_id}/index")
    def build_index(project_id: str):
        project = project_or_404(project_id)
        sanitized = apply_redaction(project.doc.canonical_text, project.ledger).sanitized_text
        report = audit_sanitized(sanitized, project.ledger, project.acknowledged)
        if report.blocking:
            raise HTTPException(
                409, "Redaction Auditor blocks egress: resolve findings first",
            )
        project.chunks = chunk_document(project.doc, project.ledger)
        project.index = PolicyIndex.build(
            project.chunks, _make_embedder(),
            os.path.join(project.work_dir, "lancedb"),
        )
        validation = validate_index(project.doc, project.chunks, project.index)
        if not validation.passed:
            project.index = None
            raise HTTPException(500, f"index validation failed: {validation.errors}")
        return {"chunks": len(project.chunks), "validated": True}

    # -- Stage 3/4: angle + templates ---------------------------------------

    @app.get("/projects/{project_id}/angles")
    def angles(project_id: str):
        project = project_or_404(project_id)
        if project.index is None:
            raise HTTPException(409, "build the index first")
        proposals = propose_angles(project.index, _make_llm())
        return [vars(p) for p in proposals]

    @app.get("/templates")
    def templates():
        return [
            {"family": name,
             "budgets_landscape": c.budgets_landscape,
             "budgets_portrait": c.budgets_portrait,
             "max_body_points": c.max_body_points}
            for name, c in TEMPLATE_FAMILIES.items()
        ]

    # -- Stages 5–9: run the agent pipeline ---------------------------------

    def _launch(project: Project, run: Run, resume_from: str | None = None,
                edits: dict | None = None):
        def worker():
            try:
                outcome = run_poster_pipeline(
                    run_id=run.run_id,
                    index=project.index,
                    ledger=project.ledger,
                    all_chunks=project.chunks,
                    angle=run.angle,
                    contract=TEMPLATE_FAMILIES[run.template_family],
                    llm=_make_llm(),
                    work_dir=os.path.join(project.work_dir, "runs", run.run_id),
                    resume_from=resume_from,
                    state_overrides=edits,
                )
                run.outcome = outcome
                run.status = outcome.status
            except Exception as exc:  # surfaced to the user, never silent
                run.status = "error"
                run.error = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        run.thread = thread
        run.status = "running"
        thread.start()

    @app.post("/projects/{project_id}/runs")
    def start_run(project_id: str, body: RunIn):
        project = project_or_404(project_id)
        if project.index is None:
            raise HTTPException(409, "build the index first")
        if body.template_family not in TEMPLATE_FAMILIES:
            raise HTTPException(400, "unknown template family")
        run = Run(run_id=str(uuid.uuid4()), angle=body.angle,
                  template_family=body.template_family)
        project.runs[run.run_id] = run
        _launch(project, run)
        return {"run_id": run.run_id}

    @app.get("/runs/{run_id}")
    def run_status(run_id: str):
        _, run = run_or_404(run_id)
        payload = {"run_id": run.run_id, "status": run.status,
                   "angle": run.angle, "template_family": run.template_family,
                   "error": run.error}
        if run.outcome is not None:
            payload["state_keys"] = sorted(run.outcome.state.keys())
            if run.outcome.diagnostic is not None:
                d = run.outcome.diagnostic
                payload["diagnostic"] = {
                    "node_id": d.node_id,
                    "node_index": d.node_index,
                    "input_state_keys": d.input_state_keys,
                    "attempts": [vars(a) for a in d.attempts],
                    "supervisor_diagnosis": d.supervisor_diagnosis,
                    "retrieval_spans": d.retrieval_spans,
                }
        return payload

    @app.post("/runs/{run_id}/resume")
    def resume(run_id: str, body: ResumeIn):
        project, run = run_or_404(run_id)
        if run.status == "running":
            raise HTTPException(409, "run is still in progress")
        _launch(project, run, resume_from=body.from_node, edits=body.edits)
        return {"run_id": run.run_id, "resumed_from": body.from_node}

    @app.get("/runs/{run_id}/poster")
    def poster(run_id: str):
        project, run = run_or_404(run_id)
        if run.outcome is None or "content" not in run.outcome.state:
            raise HTTPException(409, "no content generated yet")
        state = run.outcome.state
        content = PosterContent.from_dict(
            state.get("rehydrated") or state["content"]
        )
        citations = {}
        for _, slot in content.slots():
            for cid in slot.citations:
                if cid in citations or project.doc is None:
                    continue
                node = project.doc.find(cid)
                if node is not None:
                    citations[cid] = {
                        "clause_id": cid,
                        "text": node.text,
                        "char_span": node.char_span,
                        "section_path": project.doc.section_path(cid),
                    }
        return {
            "content": content.to_dict(),
            "sanitized_content": state["content"],
            "citations": citations,
            "coverage_map": content.coverage_map,
        }

    @app.get("/runs/{run_id}/exports/{orientation}.pptx")
    def export_file(run_id: str, orientation: str):
        _, run = run_or_404(run_id)
        if run.outcome is None or "exports" not in run.outcome.state:
            raise HTTPException(409, "exports not ready")
        path = run.outcome.state["exports"].get(orientation)
        if not path or not os.path.exists(path):
            raise HTTPException(404, "export not found")
        return FileResponse(path, filename=os.path.basename(path))

    @app.get("/runs/{run_id}/trace")
    def trace(run_id: str):
        project, run = run_or_404(run_id)
        store_dir = os.path.join(project.work_dir, "runs", run.run_id, "trace")
        records = TraceStore(store_dir).query(run.run_id)
        return [vars(r) for r in records]

    return app
