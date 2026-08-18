"""End-to-end JPG export: real uvicorn server + built frontend + Playwright.

Skipped automatically when the frontend build or chromium is unavailable.
"""

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return bool(p.chromium.executable_path) and \
                os.path.exists(p.chromium.executable_path)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not DIST.exists() or not _chromium_available(),
    reason="frontend dist or chromium not available",
)


@pytest.fixture
def server(tmp_path, monkeypatch, sample_docx):
    import uvicorn

    from policy_poster.api.serve import create_full_app

    monkeypatch.setenv("POLICY_POSTER_LLM", "offline")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("POLICY_POSTER_BASE_URL", f"http://127.0.0.1:{port}")

    app = create_full_app(data_dir=str(tmp_path / "data"), dist_dir=str(DIST))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    base = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            httpx.get(base + "/api/templates", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def test_jpg_export_300dpi(server, sample_docx):
    base = server
    with open(sample_docx, "rb") as f:
        pid = httpx.post(f"{base}/api/projects", files={
            "file": ("policy.docx", f),
        }, timeout=30).json()["project_id"]
    for term, cat in [("Acme Corporation", "org"), ("VaultMaster", "system"),
                      ("security@acme.com", "email")]:
        httpx.post(f"{base}/api/projects/{pid}/terms",
                   json={"term": term, "category": cat}, timeout=10)
    audit = httpx.get(f"{base}/api/projects/{pid}/audit", timeout=10).json()
    surfaces = [f["detail"].split("'")[1] for f in audit["findings"]
                if f["severity"] == "warning" and "'" in f["detail"]]
    httpx.post(f"{base}/api/projects/{pid}/audit/acknowledge",
               json={"surfaces": surfaces}, timeout=10)
    assert httpx.post(f"{base}/api/projects/{pid}/index", timeout=120).status_code == 200
    run_id = httpx.post(f"{base}/api/projects/{pid}/runs", json={
        "angle": "general awareness", "template_family": "default",
    }, timeout=10).json()["run_id"]
    deadline = time.time() + 90
    while time.time() < deadline:
        status = httpx.get(f"{base}/api/runs/{run_id}", timeout=10).json()
        if status["status"] != "running":
            break
        time.sleep(0.5)
    assert status["status"] == "complete", status

    resp = httpx.get(f"{base}/api/runs/{run_id}/exports/landscape.jpg", timeout=120)
    assert resp.status_code == 200, resp.text
    assert resp.content[:3] == b"\xff\xd8\xff"  # JPEG magic

    from PIL import Image
    import io

    img = Image.open(io.BytesIO(resp.content))
    assert img.width == 4000  # 13.33in × 300dpi ≈ 4000 px
    assert img.height == 2250
