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
from .redaction import RedactionLedger
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
) -> list[Node]:
    token = str(uuid.uuid4())
    runtime: dict = {"retrieved": [], "content": None}
    _RUNTIME[token] = runtime

    def retrieve_fn(ctx):
        intent = angle if not ctx.corrective else f"{angle}. {ctx.corrective}"
        retrieved, report = AgenticRetriever(index, llm).retrieve(intent)
        runtime["retrieved"] = retrieved
        return NodeResult(updates={
            "runtime_token": token,
            "retrieval_report": vars(report) | {
                "iterations": [vars(i) for i in report.iterations],
            },
            "retrieved_chunk_ids": [c.chunk_id for c in retrieved],
        })

    def generate_fn(ctx):
        content, violations = generate_content(
            angle, contract, runtime["retrieved"], llm,
            corrective=ctx.corrective,
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
        return _verdict_result(check_groundedness(runtime["content"], runtime["retrieved"], llm))

    def citation_fn(ctx):
        return _verdict_result(check_citations(runtime["content"], runtime["retrieved"], llm))

    def coverage_fn(ctx):
        return _verdict_result(check_coverage([runtime["content"]], all_chunks))

    def compliance_fn(ctx):
        return _verdict_result(check_compliance(runtime["content"], runtime["retrieved"], llm))

    def tone_fn(ctx):
        return _verdict_result(check_tone(runtime["content"], angle, llm))

    def layout_fn(ctx):
        return _verdict_result(check_layout_fit(runtime["content"], contract))

    def rehydrate_fn(ctx):
        content: PosterContent = runtime["content"]
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
) -> RunOutcome:
    nodes = build_poster_pipeline(
        index=index, ledger=ledger, all_chunks=all_chunks, angle=angle,
        contract=contract, llm=llm, work_dir=work_dir,
    )
    orchestrator = Orchestrator(
        nodes=nodes,
        checkpoints=CheckpointStore(os.path.join(work_dir, "checkpoints")),
        trace=TraceStore(os.path.join(work_dir, "trace")),
        supervisor=Supervisor(),
    )
    return orchestrator.run(run_id, {})
