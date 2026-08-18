"""C2: server restart must not lose projects, ledgers, or finished runs."""

import time

import pytest
from fastapi.testclient import TestClient

from policy_poster.api import create_app


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_POSTER_LLM", "offline")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return str(tmp_path / "data")


def upload(client, sample_docx):
    with open(sample_docx, "rb") as f:
        return client.post("/projects", files={
            "file": ("policy.docx", f),
        }).json()["project_id"]


def redact(client, pid):
    for term, cat in [("Acme Corporation", "org"), ("VaultMaster", "system"),
                      ("security@acme.com", "email")]:
        client.post(f"/projects/{pid}/terms", json={"term": term, "category": cat})
    audit = client.get(f"/projects/{pid}/audit").json()
    surfaces = [f["detail"].split("'")[1] for f in audit["findings"]
                if f["severity"] == "warning" and "'" in f["detail"]]
    client.post(f"/projects/{pid}/audit/acknowledge", json={"surfaces": surfaces})


def wait_run(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.3)
    raise TimeoutError


def test_project_survives_restart(data_dir, sample_docx):
    client = TestClient(create_app(data_dir=data_dir))
    pid = upload(client, sample_docx)
    redact(client, pid)

    # "restart": brand-new app instance, same data dir
    client2 = TestClient(create_app(data_dir=data_dir))
    terms = client2.get(f"/projects/{pid}/terms").json()
    assert [t["placeholder"] for t in terms] == ["⟦ORG_001⟧", "⟦SYSTEM_001⟧", "⟦EMAIL_001⟧"]
    doc = client2.get(f"/projects/{pid}/document").json()
    assert "⟦ORG_001⟧" in doc["sanitized_text"]
    audit = client2.get(f"/projects/{pid}/audit").json()
    assert not audit["blocking"]  # acknowledgements survived


def test_completed_run_survives_restart(data_dir, sample_docx):
    client = TestClient(create_app(data_dir=data_dir))
    pid = upload(client, sample_docx)
    redact(client, pid)
    assert client.post(f"/projects/{pid}/index").status_code == 200
    run_id = client.post(f"/projects/{pid}/runs", json={
        "angle": "general awareness", "template_family": "default",
    }).json()["run_id"]
    assert wait_run(client, run_id)["status"] == "complete"

    client2 = TestClient(create_app(data_dir=data_dir))
    status = client2.get(f"/runs/{run_id}").json()
    assert status["status"] == "complete"
    poster = client2.get(f"/runs/{run_id}/poster").json()
    assert poster["content"]["content"]["headline"]["text"]
    pptx = client2.get(f"/runs/{run_id}/exports/landscape.pptx")
    assert pptx.status_code == 200
