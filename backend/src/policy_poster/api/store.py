"""In-process project registry. Each project owns its document, ledger,
index, and runs; heavy artefacts live in a per-project work dir."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..chunker import Chunk
from ..index import PolicyIndex
from ..models import PolicyDocument
from ..orchestrator import RunOutcome
from ..redaction import RedactionLedger


@dataclass
class Run:
    run_id: str
    angle: str
    template_family: str
    status: str = "running"  # running | complete | halted | error
    outcome: RunOutcome | None = None
    error: str | None = None
    thread: threading.Thread | None = None
    events: list = field(default_factory=list)  # live agent progress feed
    current_node: str | None = None
    cancel_requested: bool = False


@dataclass
class Project:
    project_id: str
    work_dir: str
    doc: PolicyDocument | None = None
    ledger: RedactionLedger = field(default_factory=RedactionLedger)
    acknowledged: set[str] = field(default_factory=set)
    dismissed_suggestions: set[str] = field(default_factory=set)
    chunks: list[Chunk] = field(default_factory=list)
    index: PolicyIndex | None = None
    embedding_source: str = ""
    runs: dict[str, Run] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ProjectStore:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir
        self._projects: dict[str, Project] = {}
        os.makedirs(data_dir, exist_ok=True)
        from .persist import Persistence

        self.persistence = Persistence(data_dir)
        self._restore()

    def _restore(self) -> None:
        """Rebuild projects + finished runs from SQLite after a restart."""
        import json

        from ..docx_parser import parse_docx
        from ..orchestrator import RunOutcome
        from ..pdf_parser import parse_pdf

        for row in self.persistence.load_all():
            try:
                project = Project(project_id=row["project_id"],
                                  work_dir=row["work_dir"])
                source = row.get("source_path")
                if source and os.path.exists(source):
                    project.doc = (parse_docx(source) if source.endswith(".docx")
                                   else parse_pdf(source))
                for term in json.loads(row.get("terms") or "[]"):
                    project.ledger.add(term["term"], term["category"])
                project.acknowledged = set(json.loads(row.get("acknowledged") or "[]"))
                project.dismissed_suggestions = set(
                    json.loads(row.get("dismissed") or "[]"))
                project.embedding_source = row.get("embedding_source") or ""
                for run_row in row.get("runs", []):
                    run = Run(run_id=run_row["run_id"], angle=run_row["angle"],
                              template_family=run_row["template_family"],
                              status=run_row["status"] or "error",
                              error=run_row.get("error"))
                    if run.status == "running":  # interrupted by the restart
                        run.status = "error"
                        run.error = "server restarted mid-run — resume from any step"
                    if run_row.get("state"):
                        try:
                            run.outcome = RunOutcome(
                                status=run.status,
                                state=json.loads(run_row["state"]),
                            )
                        except Exception:
                            run.outcome = None
                    project.runs[run.run_id] = run
                self._projects[project.project_id] = project
            except Exception:
                continue  # a corrupt row must not block startup

    def create(self) -> Project:
        project_id = str(uuid.uuid4())
        work_dir = os.path.join(self._data_dir, project_id)
        os.makedirs(work_dir, exist_ok=True)
        project = Project(project_id=project_id, work_dir=work_dir)
        self._projects[project_id] = project
        return project

    def save(self, project: Project) -> None:
        self.persistence.save_project(project)

    def save_run(self, project: Project, run: Run) -> None:
        self.persistence.save_run(project.project_id, run)

    def get(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def find_run(self, run_id: str) -> tuple[Project, Run] | None:
        for project in self._projects.values():
            if run_id in project.runs:
                return project, project.runs[run_id]
        return None
