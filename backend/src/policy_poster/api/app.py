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
from ..index import PolicyIndex, validate_index
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


from ..llm_providers import (
    LLMSettings,
    OPENAI_COMPAT_BASES,
    PROVIDERS,
    list_models,
    make_embedder,
    make_llm,
    probe_connection,
)




class TermIn(BaseModel):
    term: str
    category: str


class AcknowledgeIn(BaseModel):
    surfaces: list[str]


class RunIn(BaseModel):
    angle: str
    template_family: str = "default"


class LLMSettingsIn(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    embed_model: str = ""


class ResumeIn(BaseModel):
    from_node: str
    edits: dict = {}


class FeedbackIn(BaseModel):
    kind: str
    policy_type: str
    angle: str
    before: str
    after: str
    note: str = ""


def create_app(data_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="Policy Poster")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    resolved_data_dir = data_dir or os.environ.get(
        "POLICY_POSTER_DATA",
        os.path.join(tempfile.gettempdir(), "policy_poster_data"),
    )
    store = ProjectStore(resolved_data_dir)
    app.state.store = store

    from ..feedback import FeedbackEntry, FeedbackStore

    feedback_store = FeedbackStore(os.path.join(resolved_data_dir, "feedback"))
    app.state.feedback = feedback_store

    # -- LLM settings: env defaults, runtime-configurable, persisted locally --
    import json as _json

    settings_path = os.path.join(resolved_data_dir, "llm_settings.json")

    def _load_llm_settings() -> LLMSettings:
        if os.path.exists(settings_path):
            try:
                return LLMSettings(**_json.load(open(settings_path, encoding="utf-8")))
            except Exception:
                pass
        return LLMSettings.from_env()

    app.state.llm_settings = _load_llm_settings()

    def _make_llm():
        return make_llm(app.state.llm_settings)

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
            project.chunks, make_embedder(app.state.llm_settings),
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
                    feedback=feedback_store,
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

    @app.get("/runs/{run_id}/exports/{orientation}.jpg")
    def export_jpg_file(run_id: str, orientation: str):
        """Lazily renders the React poster at 300 DPI via Playwright."""
        project, run = run_or_404(run_id)
        if run.outcome is None or "exports" not in run.outcome.state:
            raise HTTPException(409, "run not complete")
        out_dir = os.path.join(project.work_dir, "runs", run.run_id)
        out_path = os.path.join(out_dir, f"poster_{orientation}.jpg")
        if not os.path.exists(out_path):
            from ..export_jpg import export_jpg

            base_url = os.environ.get(
                "POLICY_POSTER_BASE_URL", "http://127.0.0.1:8000",
            )
            try:
                export_jpg(base_url, run_id, orientation, out_path)
            except Exception as exc:
                raise HTTPException(
                    500,
                    f"JPG render failed (is the full app with frontend served "
                    f"at {base_url}? is chromium installed?): {exc}",
                )
        return FileResponse(out_path, filename=os.path.basename(out_path))

    @app.get("/runs/{run_id}/trace")
    def trace(run_id: str):
        project, run = run_or_404(run_id)
        store_dir = os.path.join(project.work_dir, "runs", run.run_id, "trace")
        records = TraceStore(store_dir).query(run.run_id)
        return [vars(r) for r in records]

    # -- Governance: feedback (self-learning) + metrics ---------------------

    @app.get("/feedback")
    def feedback_list():
        return [vars(e) for e in feedback_store.list()]

    @app.post("/feedback")
    def feedback_record(body: FeedbackIn):
        entry_id = feedback_store.record(FeedbackEntry(**body.model_dump()))
        return {"entry_id": entry_id}

    @app.post("/feedback/{entry_id}/promote")
    def feedback_promote(entry_id: str):
        feedback_store.promote(entry_id)
        return {"promoted": entry_id}

    @app.post("/feedback/{entry_id}/demote")
    def feedback_demote(entry_id: str):
        feedback_store.demote(entry_id)
        return {"demoted": entry_id}

    @app.delete("/feedback/{entry_id}")
    def feedback_remove(entry_id: str):
        feedback_store.remove(entry_id)
        return {"removed": entry_id}

    # -- LLM provider settings (model-independent: any configured AI works) --

    @app.get("/settings/llm")
    def llm_settings_get():
        return {
            "current": app.state.llm_settings.redacted(),
            "providers": PROVIDERS,
            "default_base_urls": OPENAI_COMPAT_BASES,
        }

    @app.post("/settings/llm")
    def llm_settings_set(body: LLMSettingsIn):
        current: LLMSettings = app.state.llm_settings
        new = LLMSettings(
            provider=body.provider.lower().strip(),
            model=body.model.strip(),
            base_url=body.base_url.strip(),
            # empty api_key in the request keeps the stored one
            api_key=body.api_key.strip() or current.api_key,
            embed_model=body.embed_model.strip(),
        )
        if new.provider not in PROVIDERS:
            raise HTTPException(400, f"unknown provider (known: {PROVIDERS})")
        try:
            make_llm(new)  # validates required fields eagerly
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        app.state.llm_settings = new
        with open(settings_path, "w", encoding="utf-8") as f:
            _json.dump(vars(new) | {"extra_headers": {}}, f)
        return new.redacted()

    @app.post("/settings/llm/models")
    def llm_models(body: LLMSettingsIn):
        """'Load models' — query the provider's live catalog. Never raises."""
        current: LLMSettings = app.state.llm_settings
        settings = LLMSettings(
            provider=body.provider.lower().strip(),
            model=body.model.strip(),
            base_url=body.base_url.strip(),
            api_key=body.api_key.strip() or current.api_key,
            embed_model=body.embed_model.strip(),
        )
        return list_models(settings)

    @app.post("/settings/llm/test")
    def llm_settings_test(body: LLMSettingsIn | None = None):
        if body is not None:
            current: LLMSettings = app.state.llm_settings
            settings = LLMSettings(
                provider=body.provider.lower().strip(),
                model=body.model.strip(),
                base_url=body.base_url.strip(),
                api_key=body.api_key.strip() or current.api_key,
                    embed_model=body.embed_model.strip(),
            )
        else:
            settings = app.state.llm_settings
        return probe_connection(settings)

    @app.get("/metrics")
    def metrics():
        from ..metrics import compute_metrics

        totals = {"runs": 0, "completed_posters": 0, "tokens_total": 0}
        rates: list[dict] = []
        halted = 0
        for project in store._projects.values():
            run_ids = list(project.runs.keys())
            halted += sum(1 for r in project.runs.values() if r.status == "halted")
            for run_id in run_ids:
                trace_dir = os.path.join(project.work_dir, "runs", run_id, "trace")
                m = compute_metrics(TraceStore(trace_dir), [run_id])
                rates.append(m)
        if not rates:
            return compute_metrics(TraceStore(os.path.join(resolved_data_dir, "_none")), [])
        merged = {
            "runs": sum(m["runs"] for m in rates),
            "completed_posters": sum(m["completed_posters"] for m in rates),
            "tokens_total": sum(m["tokens_total"] for m in rates),
            "redaction_leakage_incidents": 0,
        }
        def avg(key):
            vals = [m[key] for m in rates if m[key] is not None]
            return sum(vals) / len(vals) if vals else None
        merged["groundedness_pass_rate"] = avg("groundedness_pass_rate")
        merged["citation_drift_rate"] = avg("citation_drift_rate")
        merged["coverage_completeness"] = avg("coverage_completeness")
        merged["mean_rewinds_per_run"] = avg("mean_rewinds_per_run") or 0.0
        merged["human_intervention_rate"] = (
            halted / merged["runs"] if merged["runs"] else 0.0
        )
        merged["mean_tokens_per_poster"] = (
            merged["tokens_total"] / merged["completed_posters"]
            if merged["completed_posters"] else None
        )
        return merged

    return app
