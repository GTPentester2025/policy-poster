"""Graph orchestration engine (spec §6).

Forward flow over an ordered node list, with Supervisor rewind edges that can
jump back to ANY prior node (not just the previous one). Blocking gates can
never be overridden — the Supervisor only re-runs work. Budgets: max 2 rewinds
per failing node (3rd failure halts with a diagnostic), plus a global rewind
ceiling. Every attempt checkpoints the full state, keyed
{run_id, node_id, attempt}, so rewind and resume never cold-restart.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .trace import TraceRecord, TraceStore


@dataclass
class NodeContext:
    state: dict
    attempt: int
    corrective: str | None = None


@dataclass
class NodeResult:
    updates: dict = field(default_factory=dict)
    verdict: str = "pass"  # "pass" | "revise" | "reject"
    findings: list = field(default_factory=list)


@dataclass
class Node:
    name: str
    fn: Callable[[NodeContext], NodeResult]
    blocking: bool = False
    root_cause: str | None = None  # node to rewind to when this node fails
    agent: str = ""


@dataclass
class AttemptRecord:
    attempt: int
    output: dict
    verdict: str
    findings: list


@dataclass
class DiagnosticSnapshot:
    node_id: str
    node_index: int
    input_state_keys: list[str]
    attempts: list[AttemptRecord]
    supervisor_diagnosis: str
    retrieval_spans: Any


@dataclass
class RunOutcome:
    status: str  # "complete" | "halted"
    state: dict
    diagnostic: DiagnosticSnapshot | None = None


class CheckpointStore:
    """Full-state snapshots per {run_id, node_id, attempt}."""

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str, node_id: str, attempt: int) -> Path:
        d = self._base / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{node_id}__{attempt}.json"

    def save(self, run_id: str, node_id: str, attempt: int, payload: dict) -> None:
        self._path(run_id, node_id, attempt).write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def load(self, run_id: str, node_id: str, attempt: int) -> dict | None:
        path = self._path(run_id, node_id, attempt)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def latest(self, run_id: str, node_id: str) -> dict | None:
        run_dir = self._base / run_id
        if not run_dir.exists():
            return None
        best, best_attempt = None, -1
        for path in run_dir.glob(f"{node_id}__*.json"):
            attempt = int(path.stem.split("__")[-1])
            if attempt > best_attempt:
                best_attempt = attempt
                best = json.loads(path.read_text(encoding="utf-8"))
        return best

    def invalidate(self, run_id: str, node_ids: list[str]) -> None:
        run_dir = self._base / run_id
        if not run_dir.exists():
            return
        for node_id in node_ids:
            for path in run_dir.glob(f"{node_id}__*.json"):
                path.unlink()


class Supervisor:
    """Observes verdicts, diagnoses root-cause nodes, injects corrective
    instructions, enforces budgets. May never override a blocking gate."""

    def __init__(self, rewind_budget_per_node: int = 2,
                 max_total_rewinds: int = 10,
                 max_wall_clock_s: float | None = None) -> None:
        self.rewind_budget_per_node = rewind_budget_per_node
        self.max_total_rewinds = max_total_rewinds
        self.max_wall_clock_s = max_wall_clock_s

    def diagnose_target(self, node: Node) -> str:
        return node.root_cause or node.name

    def corrective_instruction(self, node: Node, result: NodeResult) -> str:
        details = "; ".join(
            str(f.get("detail", f)) if isinstance(f, dict) else str(f)
            for f in result.findings
        ) or "no details provided"
        target = self.diagnose_target(node)
        return (
            f"Downstream node '{node.name}' rejected its input "
            f"(findings: {details}). Re-run '{target}' addressing these findings."
        )

    def diagnosis_text(self, node: Node, failures: int, result: NodeResult) -> str:
        return (
            f"Node '{node.name}' failed {failures} times; budget of "
            f"{self.rewind_budget_per_node} rewinds exhausted. Root cause was "
            f"diagnosed as '{self.diagnose_target(node)}'. Last findings: "
            + "; ".join(
                str(f.get("detail", f)) if isinstance(f, dict) else str(f)
                for f in result.findings
            )
        )


class Orchestrator:
    def __init__(self, nodes: list[Node], checkpoints: CheckpointStore,
                 trace: TraceStore, supervisor: Supervisor,
                 on_event=None) -> None:
        self.nodes = nodes
        self.checkpoints = checkpoints
        self.trace = trace
        self.supervisor = supervisor
        self._order = {n.name: i for i, n in enumerate(nodes)}
        self._on_event = on_event

    def _emit(self, event: dict) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                pass  # progress reporting must never break the run

    def _diagnostic(self, run_id: str, node: Node, state: dict,
                    failures: int, last_result: NodeResult) -> DiagnosticSnapshot:
        attempts = []
        for attempt in range(1, failures + 1):
            ckpt = self.checkpoints.load(run_id, node.name, attempt)
            if ckpt:
                attempts.append(AttemptRecord(
                    attempt=attempt, output=ckpt.get("updates", {}),
                    verdict=ckpt.get("verdict", "?"), findings=ckpt.get("findings", []),
                ))
        return DiagnosticSnapshot(
            node_id=node.name,
            node_index=self._order[node.name],
            input_state_keys=sorted(state.keys()),
            attempts=attempts,
            supervisor_diagnosis=self.supervisor.diagnosis_text(node, failures, last_result),
            retrieval_spans=state.get("retrieval_report"),
        )

    def run(self, run_id: str, initial_state: dict,
            resume_from: str | None = None) -> RunOutcome:
        started = time.monotonic()
        state = dict(initial_state)
        start_idx = 0

        if resume_from is not None:
            start_idx = self._order[resume_from]
            if start_idx > 0:
                prior = self.checkpoints.latest(run_id, self.nodes[start_idx - 1].name)
                if prior is not None:
                    state = dict(prior.get("state_after", {}))
                    state.update(initial_state)
            # downstream checkpoints (incl. the resume node) are stale
            self.checkpoints.invalidate(
                run_id, [n.name for n in self.nodes[start_idx:]]
            )

        attempts: dict[str, int] = {}
        failures: dict[str, int] = {}
        corrective: dict[str, str] = {}
        total_rewinds = 0
        i = start_idx

        while i < len(self.nodes):
            node = self.nodes[i]
            attempts[node.name] = attempts.get(node.name, 0) + 1
            attempt = attempts[node.name]
            ctx = NodeContext(
                state=state, attempt=attempt,
                corrective=corrective.pop(node.name, None),
            )
            self._emit({"type": "node_start", "node": node.name,
                        "agent": node.agent or node.name, "attempt": attempt,
                        "corrective": ctx.corrective})
            t0 = time.monotonic()
            try:
                result = node.fn(ctx)
            except Exception as exc:
                self._emit({"type": "node_error", "node": node.name,
                            "attempt": attempt, "error": str(exc)})
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit({"type": "node_end", "node": node.name,
                        "agent": node.agent or node.name, "attempt": attempt,
                        "verdict": result.verdict, "findings": result.findings,
                        "latency_ms": latency_ms})

            state.update(result.updates)
            self.checkpoints.save(run_id, node.name, attempt, {
                "updates": result.updates,
                "verdict": result.verdict,
                "findings": result.findings,
                "state_after": state,
            })
            self.trace.append(TraceRecord(
                run_id=run_id, node_id=node.name, attempt=attempt,
                agent=node.agent or node.name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                input_hash=hashlib.sha256(
                    json.dumps(sorted(state.keys())).encode()
                ).hexdigest(),
                output={"verdict": result.verdict},
                verdict=result.verdict,
                findings=result.findings,
                latency_ms=latency_ms,
            ))

            if result.verdict == "reject" and node.blocking:
                failures[node.name] = failures.get(node.name, 0) + 1
                if failures[node.name] > self.supervisor.rewind_budget_per_node:
                    self._emit({"type": "halt", "node": node.name,
                                "failures": failures[node.name]})
                    return RunOutcome(
                        status="halted", state=state,
                        diagnostic=self._diagnostic(
                            run_id, node, state, failures[node.name], result,
                        ),
                    )
                total_rewinds += 1
                over_wall_clock = (
                    self.supervisor.max_wall_clock_s is not None
                    and time.monotonic() - started > self.supervisor.max_wall_clock_s
                )
                if total_rewinds > self.supervisor.max_total_rewinds or over_wall_clock:
                    return RunOutcome(
                        status="halted", state=state,
                        diagnostic=self._diagnostic(
                            run_id, node, state, failures[node.name], result,
                        ),
                    )
                target = self.supervisor.diagnose_target(node)
                corrective[target] = self.supervisor.corrective_instruction(node, result)
                self._emit({"type": "rewind", "from": node.name, "to": target,
                            "corrective": corrective[target]})
                i = self._order[target]
                continue

            i += 1

        return RunOutcome(status="complete", state=state)
