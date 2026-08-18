"""SQLite persistence for projects and runs (roadmap C2).

Documents are re-parsed from the stored source file on load (parsers are
deterministic); ledgers are rebuilt by re-adding terms in insertion order,
which regenerates identical placeholders. Chunks/index are rebuilt on demand
via the normal /index endpoint; finished run outcomes are stored whole.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    work_dir TEXT NOT NULL,
    source_path TEXT,
    embedding_source TEXT DEFAULT '',
    terms TEXT DEFAULT '[]',
    acknowledged TEXT DEFAULT '[]',
    dismissed TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    angle TEXT, template_family TEXT,
    status TEXT, state TEXT, error TEXT
);
"""


class Persistence:
    def __init__(self, data_dir: str) -> None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._path = str(Path(data_dir) / "projects.db")
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def save_project(self, project) -> None:
        source = None
        for candidate in ("source.docx", "source.pdf"):
            path = Path(project.work_dir) / candidate
            if path.exists():
                source = str(path)
        with self._lock, self._connect() as db:
            db.execute(
                "REPLACE INTO projects VALUES (?,?,?,?,?,?,?)",
                (
                    project.project_id, project.work_dir, source,
                    project.embedding_source,
                    json.dumps([{"term": t.term, "category": t.category}
                                for t in project.ledger.terms]),
                    json.dumps(sorted(project.acknowledged)),
                    json.dumps(sorted(project.dismissed_suggestions)),
                ),
            )

    def save_run(self, project_id: str, run) -> None:
        state = None
        if run.outcome is not None:
            try:
                state = json.dumps(run.outcome.state, default=str)
            except Exception:
                state = None
        with self._lock, self._connect() as db:
            db.execute(
                "REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                (run.run_id, project_id, run.angle, run.template_family,
                 run.status, state, run.error),
            )

    def load_all(self) -> list[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            projects = [dict(r) for r in db.execute("SELECT * FROM projects")]
            runs = [dict(r) for r in db.execute("SELECT * FROM runs")]
        by_project: dict[str, list[dict]] = {}
        for run in runs:
            by_project.setdefault(run["project_id"], []).append(run)
        for project in projects:
            project["runs"] = by_project.get(project["project_id"], [])
        return projects
