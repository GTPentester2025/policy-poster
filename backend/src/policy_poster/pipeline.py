"""Production poster pipeline graph: wires the agent roster into the
orchestrator (spec §6). Rewind topology:

  retrieve → generate → qa_groundedness → qa_citation → qa_coverage
           → qa_compliance → qa_tone → qa_layout → rehydrate → export

Root-cause mapping: generation-quality gates rewind to `generate`;
coverage failures rewind all the way to `retrieve` (a coverage gap is
usually a retrieval failure, not a writing failure — spec §6 example).
Rehydration failure rewinds to itself (ledger issue surfaces to human).
"""

from __future__ import annotations

import os
import uuid

from .agents.generator import generate_content
from .agents.qa import (
    check_citations,
    check_compliance,
    check_coverage,
    check_groundedness,
    check_layout_fit,
    check_tone,
)
from .chunker import Chunk
from .content import PosterContent, TemplateContract
from .index import PolicyIndex
from .llm import LLMClient
from .orchestrator import (
    CheckpointStore,
    Node,
    NodeResult,
    Orchestrator,
    RunOutcome,
    Supervisor,
)
from .redaction import RedactionLedger, rehydrated_length
from .rehydration import rehydrate, validate_rehydration
from .retrieval import AgenticRetriever
from .export_pptx import export_both_orientations
from .trace import TraceStore

# non-serialisable per-run objects live here, keyed by run token in state
_RUNTIME: dict[str, dict] = {}


def _verdict_result(verdict) -> NodeResult:
    return NodeResult(
        updates={f"verdict_{verdict.agent}": verdict.to_dict()},
        verdict=verdict.verdict if verdict.verdict != "revise" else "pass",
        findings=[vars(f) for f in verdict.findings],
    )


def build_poster_pipeline(
    index: PolicyIndex,
    ledger: RedactionLedger,
    all_chunks: list[Chunk],
    angle: str,
    contract: TemplateContract,
    llm: LLMClient,
    work_dir: str,
    feedback=None,  # FeedbackStore | None — promoted exemplars shape generation
    smart_retrieval: bool = False,  # decomposition + LLM rerank (real providers)
) -> list[Node]:
    token = str(uuid.uuid4())
    runtime: dict = {"retrieved": [], "content": None}
    _RUNTIME[token] = runtime

    def _retrieved(ctx) -> list[Chunk]:
        # resume path: rebuild from checkpointed state when runtime is cold
        if not runtime["retrieved"] and ctx.state.get("retrieved_chunk_ids"):
            runtime["retrieved"] = [
                c for c in (index.get(cid) for cid in ctx.state["retrieved_chunk_ids"])
                if c is not None
            ]
        return runtime["retrieved"]

    def _content(ctx) -> PosterContent | None:
        if runtime["content"] is None and ctx.state.get("content"):
            runtime["content"] = PosterContent.from_dict(ctx.state["content"])
        return runtime["content"]

    def retrieve_fn(ctx):
        intent = angle if not ctx.corrective else f"{angle}. {ctx.corrective}"
        retrieved, report = AgenticRetriever(
            index, llm, decompose=smart_retrieval, rerank=smart_retrieval,
        ).retrieve(intent)
        runtime["retrieved"] = retrieved
        return NodeResult(updates={
            "runtime_token": token,
            "retrieval_report": vars(report) | {
                "iterations": [vars(i) for i in report.iterations],
            },
            "retrieved_chunk_ids": [c.chunk_id for c in retrieved],
        })

    def generate_fn(ctx):
        exemplar_block = (
            feedback.exemplar_prompt_block(angle) if feedback is not None else None
        )
        # targeted-repair mode: a QA gate rejected specific slots of an
        # otherwise-passing poster → rewrite only those slots, keep the rest
        previous, fix_slots = None, []
        if ctx.corrective and ctx.state.get("content"):
            for key in ("verdict_citation", "verdict_groundedness",
                        "verdict_compliance"):
                verdict = ctx.state.get(key)
                if isinstance(verdict, dict) and verdict.get("verdict") == "reject":
                    fix_slots += [
                        f.get("slot") for f in verdict.get("findings", [])
                        if f.get("slot")
                    ]
            if fix_slots:
                previous = ctx.state["content"]
        def length_of(t):
            return rehydrated_length(t, ledger)

        content, violations = generate_content(
            angle, contract, _retrieved(ctx), llm,
            corrective=ctx.corrective,
            exemplar_block=exemplar_block,
            previous=previous,
            fix_slots=fix_slots or None,
            length_of=length_of,
        )
        if content is None and violations:
            # in-node repair pass: tell the model exactly what to fix before
            # escalating to a supervisor rewind (helps smaller models a lot)
            repair_note = (
                "Your previous attempt violated the schema. Fix EXACTLY these "
                "issues and return the corrected full JSON:\n- "
                + "\n- ".join(violations[:12])
            )
            if ctx.corrective:
                repair_note = f"{ctx.corrective}\n\n{repair_note}"
            content, violations = generate_content(
                angle, contract, _retrieved(ctx), llm,
                corrective=repair_note,
                exemplar_block=exemplar_block,
                length_of=length_of,
            )
        if content is None:
            return NodeResult(
                verdict="reject",
                findings=[{"detail": v} for v in violations],
            )
        runtime["content"] = content
        return NodeResult(updates={"content": content.to_dict()})

    def qa(fn_name, fn):
        def node_fn(ctx):
            return _verdict_result(fn())
        return node_fn

    def groundedness_fn(ctx):
        return _verdict_result(check_groundedness(_content(ctx), _retrieved(ctx), llm))

    def citation_fn(ctx):
        return _verdict_result(check_citations(_content(ctx), _retrieved(ctx), llm))

    def coverage_fn(ctx):
        return _verdict_result(check_coverage([_content(ctx)], all_chunks))

    def compliance_fn(ctx):
        return _verdict_result(check_compliance(_content(ctx), _retrieved(ctx), llm))

    def tone_fn(ctx):
        return _verdict_result(check_tone(_content(ctx), angle, llm))

    def layout_fn(ctx):
        return _verdict_result(check_layout_fit(_content(ctx), contract))

    def rehydrate_fn(ctx):
        content: PosterContent = _content(ctx)
        entering = set(content.placeholders_present)
        result = rehydrate(
            content, ledger,
            metadata={"alt_text": content.headline.text},
            filename=f"poster_{content.poster_id}",
        )
        report = validate_rehydration(result, entering, ledger)
        if not report.passed:
            return NodeResult(
                verdict="reject",
                findings=[{"detail": e} for e in report.errors],
            )
        runtime["rehydrated"] = result
        return NodeResult(updates={
            "rehydrated": result.content.to_dict(),
            "export_filename": result.filename,
        })

    def export_fn(ctx):
        if "rehydrated" not in runtime:
            from .rehydration import RehydrationResult

            runtime["rehydrated"] = RehydrationResult(
                content=PosterContent.from_dict(ctx.state["rehydrated"]),
                metadata={}, filename=ctx.state["export_filename"],
            )
        result = runtime["rehydrated"]
        os.makedirs(work_dir, exist_ok=True)
        base = os.path.join(work_dir, result.filename)
        paths = export_both_orientations(result.content, base)
        return NodeResult(updates={"exports": paths})

    return [
        Node("retrieve", retrieve_fn, agent="retrieval"),
        Node("generate", generate_fn, blocking=True, root_cause="generate",
             agent="content_generator"),
        Node("qa_groundedness", groundedness_fn, blocking=True,
             root_cause="generate", agent="groundedness_verifier"),
        Node("qa_citation", citation_fn, blocking=True, root_cause="generate",
             agent="citation_verifier"),
        Node("qa_coverage", coverage_fn, blocking=True, root_cause="retrieve",
             agent="coverage_agent"),
        Node("qa_compliance", compliance_fn, blocking=True, root_cause="generate",
             agent="compliance_gate"),
        Node("qa_tone", tone_fn, agent="tone_editor"),
        Node("qa_layout", layout_fn, agent="layout_fit"),
        Node("rehydrate", rehydrate_fn, blocking=True, root_cause="rehydrate",
             agent="rehydration_validator"),
        Node("export", export_fn, agent="export"),
    ]


def run_poster_pipeline(
    run_id: str,
    index: PolicyIndex,
    ledger: RedactionLedger,
    all_chunks: list[Chunk],
    angle: str,
    contract: TemplateContract,
    llm: LLMClient,
    work_dir: str,
    resume_from: str | None = None,
    state_overrides: dict | None = None,
    feedback=None,
    on_event=None,
    smart_retrieval: bool = False,
) -> RunOutcome:
    nodes = build_poster_pipeline(
        index=index, ledger=ledger, all_chunks=all_chunks, angle=angle,
        contract=contract, llm=llm, work_dir=work_dir, feedback=feedback,
        smart_retrieval=smart_retrieval,
    )
    orchestrator = Orchestrator(
        nodes=nodes,
        checkpoints=CheckpointStore(os.path.join(work_dir, "checkpoints")),
        trace=TraceStore(os.path.join(work_dir, "trace")),
        supervisor=Supervisor(),
        on_event=on_event,
    )
    return orchestrator.run(run_id, state_overrides or {}, resume_from=resume_from)
