import pytest

from policy_poster.metrics import compute_metrics
from policy_poster.trace import TraceRecord, TraceStore


def rec(run_id, node, attempt, verdict, agent=None, tokens_in=100, tokens_out=50):
    return TraceRecord(
        run_id=run_id, node_id=node, attempt=attempt, agent=agent or node,
        timestamp="t", input_hash="h", output={}, verdict=verdict,
        tokens_in=tokens_in, tokens_out=tokens_out,
    )


def test_metrics_aggregation(tmp_path):
    store = TraceStore(str(tmp_path))
    # run1: groundedness fails once then passes; one rewind
    store.append(rec("r1", "generate", 1, "pass"))
    store.append(rec("r1", "qa_groundedness", 1, "reject"))
    store.append(rec("r1", "generate", 2, "pass"))
    store.append(rec("r1", "qa_groundedness", 2, "pass"))
    store.append(rec("r1", "qa_citation", 1, "pass"))
    store.append(rec("r1", "export", 1, "pass"))
    # run2: clean
    store.append(rec("r2", "generate", 1, "pass"))
    store.append(rec("r2", "qa_groundedness", 1, "pass"))
    store.append(rec("r2", "qa_citation", 1, "reject"))
    store.append(rec("r2", "qa_citation", 2, "pass"))
    store.append(rec("r2", "export", 1, "pass"))

    m = compute_metrics(store, ["r1", "r2"], halted_runs=0)
    assert m["runs"] == 2
    assert m["groundedness_pass_rate"] == 2 / 3  # 2 of 3 groundedness checks
    assert m["citation_drift_rate"] == pytest.approx(1 / 3)
    assert m["mean_rewinds_per_run"] >= 0.5
    assert m["human_intervention_rate"] == 0.0
    assert m["completed_posters"] == 2
    assert m["tokens_total"] > 0


def test_metrics_empty(tmp_path):
    store = TraceStore(str(tmp_path))
    m = compute_metrics(store, [], halted_runs=0)
    assert m["runs"] == 0
