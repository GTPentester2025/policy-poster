"""Metrics rollup (spec §8): computed from trace records across runs."""

from __future__ import annotations

from .trace import TraceStore


def compute_metrics(trace: TraceStore, run_ids: list[str],
                    halted_runs: int = 0,
                    leakage_incidents: int = 0) -> dict:
    records = [r for rid in run_ids for r in trace.query(rid)]
    if not records:
        return {
            "runs": 0, "completed_posters": 0, "groundedness_pass_rate": None,
            "citation_drift_rate": None, "mean_rewinds_per_run": 0.0,
            "coverage_completeness": None, "redaction_leakage_incidents":
            leakage_incidents, "human_intervention_rate": 0.0,
            "tokens_total": 0, "mean_tokens_per_poster": None,
        }

    def rate(agent_prefix: str) -> float | None:
        checks = [r for r in records if r.node_id == agent_prefix]
        if not checks:
            return None
        return sum(1 for r in checks if r.verdict == "pass") / len(checks)

    rewinds = sum(1 for r in records if r.attempt > 1 and r.node_id != "export")
    completed = sum(
        1 for rid in run_ids
        if any(r.node_id == "export" and r.verdict == "pass" for r in trace.query(rid))
    )
    tokens_total = sum(r.tokens_in + r.tokens_out for r in records)
    coverage_checks = [r for r in records if r.node_id == "qa_coverage"]

    return {
        "runs": len(run_ids),
        "completed_posters": completed,
        "groundedness_pass_rate": rate("qa_groundedness"),
        "citation_drift_rate": (
            None if rate("qa_citation") is None else 1 - rate("qa_citation")
        ),
        "mean_rewinds_per_run": rewinds / len(run_ids) if run_ids else 0.0,
        "coverage_completeness": (
            sum(1 for r in coverage_checks if r.verdict == "pass") / len(coverage_checks)
            if coverage_checks else None
        ),
        "redaction_leakage_incidents": leakage_incidents,  # target: 0
        "human_intervention_rate": halted_runs / len(run_ids) if run_ids else 0.0,
        "tokens_total": tokens_total,
        "mean_tokens_per_poster": tokens_total / completed if completed else None,
    }
