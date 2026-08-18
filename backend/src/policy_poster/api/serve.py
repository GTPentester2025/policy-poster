"""Single-origin server: API under /api plus the built React app (SPA).

Run:  uv run uvicorn policy_poster.api.serve:app --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .app import create_app

_DEFAULT_DIST = Path(__file__).resolve().parents[4] / "frontend" / "dist"


def create_full_app(data_dir: str | None = None,
                    dist_dir: str | None = None) -> FastAPI:
    root = FastAPI(title="Policy Poster (full)")
    api_app = create_app(data_dir)
    root.mount("/api", api_app)
    root.state.api = api_app

    dist = Path(dist_dir or os.environ.get("POLICY_POSTER_DIST", _DEFAULT_DIST))
    if dist.exists():
        root.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        index = dist / "index.html"

        @root.get("/{path:path}", include_in_schema=False)
        def spa(path: str):  # SPA fallback: /, /render/... all serve index.html
            candidate = dist / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return root


app = create_full_app()
