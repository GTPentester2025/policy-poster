from policy_poster.evals import run_evals
from policy_poster.llm_offline import OfflineLLM


def test_offline_eval_baseline(tmp_path):
    summary = run_evals(OfflineLLM(), work_root=str(tmp_path))
    assert summary["completion_rate"] == 1.0
    assert summary["budget_conformance"] is True
    assert summary["mean_coverage_pct"] >= 0.99
    for row in summary["rows"]:
        assert row["no_placeholder_residue"] is True
