import pytest

from policy_poster.orchestrator import (
    CheckpointStore,
    Node,
    NodeResult,
    Orchestrator,
    Supervisor,
)
from policy_poster.trace import TraceStore


def make_orchestrator(nodes, tmp_path, **sup_kwargs):
    return Orchestrator(
        nodes=nodes,
        checkpoints=CheckpointStore(str(tmp_path / "ckpt")),
        trace=TraceStore(str(tmp_path / "trace")),
        supervisor=Supervisor(**sup_kwargs),
    )


def counting_node(name, log, verdicts=None, blocking=False, root_cause=None):
    """verdicts: list consumed per attempt; default always pass."""
    verdicts = list(verdicts or [])

    def fn(ctx):
        log.append((name, ctx.attempt, ctx.corrective))
        verdict = verdicts.pop(0) if verdicts else "pass"
        return NodeResult(updates={f"{name}_ran": ctx.attempt}, verdict=verdict,
                          findings=[{"detail": f"{name} said {verdict}"}] if verdict != "pass" else [])

    return Node(name=name, fn=fn, blocking=blocking, root_cause=root_cause)


def test_linear_happy_path(tmp_path):
    log = []
    nodes = [counting_node(n, log) for n in ["a", "b", "c"]]
    outcome = make_orchestrator(nodes, tmp_path).run("r1", {"seed": 1})
    assert outcome.status == "complete"
    assert outcome.state["a_ran"] == 1
    assert [n for n, _, _ in log] == ["a", "b", "c"]


def test_rewind_to_non_adjacent_upstream_node(tmp_path):
    # d fails once; its root cause maps to b (non-adjacent). a must NOT re-run.
    log = []
    nodes = [
        counting_node("a", log),
        counting_node("b", log),
        counting_node("c", log),
        counting_node("d", log, verdicts=["reject", "pass"], blocking=True,
                      root_cause="b"),
    ]
    outcome = make_orchestrator(nodes, tmp_path).run("r1", {})
    assert outcome.status == "complete"
    names = [n for n, _, _ in log]
    assert names == ["a", "b", "c", "d", "b", "c", "d"]
    # corrective instruction was injected into the rewind target
    b_attempt2 = [entry for entry in log if entry[0] == "b" and entry[1] == 2][0]
    assert b_attempt2[2] is not None


def test_halt_on_third_failure_with_diagnostic(tmp_path):
    log = []
    nodes = [
        counting_node("gen", log, verdicts=["reject", "reject", "reject"],
                      blocking=True),
    ]
    outcome = make_orchestrator(nodes, tmp_path).run("r1", {"retrieval_report": {"x": 1}})
    assert outcome.status == "halted"
    diag = outcome.diagnostic
    assert diag.node_id == "gen"
    assert len(diag.attempts) == 3  # all attempts side by side
    assert diag.supervisor_diagnosis
    assert diag.retrieval_spans == {"x": 1}
    # exactly 3 executions, no 4th
    assert len(log) == 3


def test_supervisor_cannot_override_blocking_gate(tmp_path):
    log = []
    nodes = [counting_node("gate", log, verdicts=["reject"] * 5, blocking=True)]
    outcome = make_orchestrator(nodes, tmp_path).run("r1", {})
    assert outcome.status == "halted"  # never "complete" while gate rejects


def test_global_rewind_budget(tmp_path):
    log = []
    nodes = [
        counting_node("a", log, verdicts=["reject", "pass"], blocking=True),
        counting_node("b", log, verdicts=["reject", "pass"], blocking=True),
        counting_node("c", log, verdicts=["reject", "reject", "pass"], blocking=True),
    ]
    outcome = make_orchestrator(nodes, tmp_path, max_total_rewinds=2).run("r1", {})
    assert outcome.status == "halted"


def test_checkpoints_persisted_per_attempt(tmp_path):
    log = []
    nodes = [
        counting_node("a", log),
        counting_node("b", log, verdicts=["reject", "pass"], blocking=True),
    ]
    orch = make_orchestrator(nodes, tmp_path)
    orch.run("r1", {})
    assert orch.checkpoints.load("r1", "b", 1) is not None
    assert orch.checkpoints.load("r1", "b", 2) is not None
    assert orch.checkpoints.load("r1", "a", 1) is not None


def test_resume_from_midpoint_reuses_upstream(tmp_path):
    log = []
    nodes = [counting_node(n, log) for n in ["a", "b", "c"]]
    orch = make_orchestrator(nodes, tmp_path)
    orch.run("r1", {"seed": 7})

    log2 = []
    nodes2 = [counting_node(n, log2) for n in ["a", "b", "c"]]
    orch2 = Orchestrator(
        nodes=nodes2, checkpoints=orch.checkpoints,
        trace=orch.trace, supervisor=Supervisor(),
    )
    outcome = orch2.run("r1", {}, resume_from="b")
    assert outcome.status == "complete"
    # a not re-executed; its checkpointed state present
    assert [n for n, _, _ in log2] == ["b", "c"]
    assert outcome.state["a_ran"] == 1
    assert outcome.state["seed"] == 7
