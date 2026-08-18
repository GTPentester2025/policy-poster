"""Eval runner: end-to-end pipeline runs over the golden corpus + scoreboard.

Usage:
    uv run python -m policy_poster.evals            # offline (hermetic)
    POLICY_POSTER_LLM=... uv run python -m policy_poster.evals
"""

from __future__ import annotations

import os
import tempfile
import time

from ..chunker import chunk_document
from ..content import DEFAULT_CONTRACT, PosterContent
from ..embedder import HashingEmbedder
from ..index import PolicyIndex, validate_index
from ..pipeline import run_poster_pipeline
from ..redaction import PLACEHOLDER_RE, RedactionLedger, apply_redaction
from ..trace import TraceStore
from ..tree import build_tree
from .corpus import CORPUS


def _first_attempt_pass_rate(trace_records, node_id: str) -> float | None:
    firsts = [r for r in trace_records if r.node_id == node_id and r.attempt == 1]
    if not firsts:
        return None
    return sum(1 for r in firsts if r.verdict == "pass") / len(firsts)


def score_run(outcome, doc, contract, work_dir: str, run_id: str) -> dict:
    trace = TraceStore(os.path.join(work_dir, "trace")).query(run_id)
    score: dict = {
        "completed": outcome.status == "complete",
        "groundedness_first_pass": _first_attempt_pass_rate(trace, "qa_groundedness"),
        "citation_first_pass": _first_attempt_pass_rate(trace, "qa_citation"),
        "generate_attempts": max(
            [r.attempt for r in trace if r.node_id == "generate"], default=0,
        ),
        "budget_conformant": None,
        "coverage_pct": None,
        "no_placeholder_residue": None,
    }
    if outcome.status == "complete":
        final = PosterContent.from_dict(outcome.state["rehydrated"])
        over = [
            name for name, slot in final.slots()
            if len(slot.text) > contract.budget(
                "body_point" if name.startswith("body_points") else name
            )
        ]
        score["budget_conformant"] = not over
        obligations = {
            cid for c in chunk_document(doc, RedactionLedger())
            if c.obligation_flag for cid in c.clause_ids
        }
        content = PosterContent.from_dict(outcome.state["content"])
        addressed = {
            cid for cid, state in content.coverage_map.items()
            if state in ("covered", "partial", "not_applicable")
        }
        score["coverage_pct"] = (
            len(obligations & addressed) / len(obligations) if obligations else 1.0
        )
        score["no_placeholder_residue"] = not PLACEHOLDER_RE.search(
            str(outcome.state["rehydrated"])
        )
    return score


def run_evals(llm, work_root: str | None = None, smart_retrieval: bool = False) -> dict:
    work_root = work_root or tempfile.mkdtemp(prefix="policy_poster_evals_")
    rows = []
    for name, angle, terms, blocks in CORPUS:
        doc = build_tree(list(blocks), doc_id=name, filename=f"{name}.docx")
        ledger = RedactionLedger()
        for term, category in terms:
            ledger.add(term, category)
        sanitized = apply_redaction(doc.canonical_text, ledger).sanitized_text
        assert all(t.term not in sanitized for t in ledger.terms)  # C2 sanity
        chunks = chunk_document(doc, ledger)
        work_dir = os.path.join(work_root, name)
        index = PolicyIndex.build(chunks, HashingEmbedder(),
                                  os.path.join(work_dir, "db"))
        assert validate_index(doc, chunks, index).passed
        t0 = time.monotonic()
        outcome = run_poster_pipeline(
            run_id=f"eval-{name}", index=index, ledger=ledger,
            all_chunks=chunks, angle=angle, contract=DEFAULT_CONTRACT,
            llm=llm, work_dir=work_dir, smart_retrieval=smart_retrieval,
        )
        score = score_run(outcome, doc, DEFAULT_CONTRACT, work_dir, f"eval-{name}")
        score["policy"] = name
        score["wall_s"] = round(time.monotonic() - t0, 1)
        rows.append(score)

    completed = [r for r in rows if r["completed"]]
    summary = {
        "completion_rate": len(completed) / len(rows),
        "mean_generate_attempts": (
            sum(r["generate_attempts"] for r in rows) / len(rows)
        ),
        "budget_conformance": all(r["budget_conformant"] for r in completed) if completed else False,
        "mean_coverage_pct": (
            sum(r["coverage_pct"] for r in completed) / len(completed)
            if completed else 0.0
        ),
        "rows": rows,
    }
    return summary


def main() -> None:
    from ..llm_providers import LLMSettings, make_llm

    settings = LLMSettings.from_env()
    llm = make_llm(settings)
    print(f"provider: {settings.provider} {settings.model}".strip())
    summary = run_evals(llm, smart_retrieval=settings.provider != "offline")
    for row in summary["rows"]:
        print(
            f"{row['policy']:14} completed={row['completed']} "
            f"gen_attempts={row['generate_attempts']} "
            f"ground1st={row['groundedness_first_pass']} "
            f"cite1st={row['citation_first_pass']} "
            f"coverage={row['coverage_pct']} wall={row['wall_s']}s"
        )
    print(
        f"\ncompletion={summary['completion_rate']:.0%} "
        f"mean_attempts={summary['mean_generate_attempts']:.1f} "
        f"budgets_ok={summary['budget_conformance']} "
        f"coverage={summary['mean_coverage_pct']:.0%}"
    )


if __name__ == "__main__":
    main()
