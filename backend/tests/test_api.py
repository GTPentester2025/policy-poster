import time

import pytest
from fastapi.testclient import TestClient

from policy_poster.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_POSTER_LLM", "offline")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(data_dir=str(tmp_path / "data"))
    return TestClient(app)


def upload(client, sample_docx):
    with open(sample_docx, "rb") as f:
        resp = client.post("/projects", files={
            "file": ("policy.docx", f,
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        })
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"]


def redact_fully(client, pid):
    for term, cat in [("Acme Corporation", "org"), ("VaultMaster", "system"),
                      ("security@acme.com", "email")]:
        assert client.post(f"/projects/{pid}/terms", json={
            "term": term, "category": cat,
        }).status_code == 200
    audit = client.get(f"/projects/{pid}/audit").json()
    warn_surfaces = [
        f["detail"].split(":")[-1].strip().strip("'\"")
        for f in audit["findings"] if f["severity"] == "warning"
    ]
    client.post(f"/projects/{pid}/audit/acknowledge", json={"surfaces": warn_surfaces})


def wait_run(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.3)
    raise TimeoutError("run did not finish")


def test_upload_and_document_view(client, sample_docx):
    pid = upload(client, sample_docx)
    doc = client.get(f"/projects/{pid}/document").json()
    assert doc["clauses"]
    assert "Acme Corporation" in doc["sanitized_text"]  # nothing masked yet


def test_terms_preview_and_occurrences(client, sample_docx):
    pid = upload(client, sample_docx)
    preview = client.post(f"/projects/{pid}/terms/preview", json={
        "term": "Acme Corporation", "category": "org",
    }).json()
    assert preview["occurrences"] >= 1
    client.post(f"/projects/{pid}/terms", json={"term": "Acme Corporation", "category": "org"})
    terms = client.get(f"/projects/{pid}/terms").json()
    assert terms[0]["placeholder"] == "⟦ORG_001⟧"
    assert terms[0]["occurrences"] >= 1
    doc = client.get(f"/projects/{pid}/document").json()
    assert "Acme Corporation" not in doc["sanitized_text"]
    assert "⟦ORG_001⟧" in doc["sanitized_text"]


def test_index_blocked_until_audit_passes(client, sample_docx):
    pid = upload(client, sample_docx)
    resp = client.post(f"/projects/{pid}/index")
    assert resp.status_code == 409  # planted leak: email/etc. still visible
    redact_fully(client, pid)
    resp = client.post(f"/projects/{pid}/index")
    assert resp.status_code == 200, resp.text
    assert resp.json()["validated"]


def test_suggestions_endpoint(client, sample_docx):
    pid = upload(client, sample_docx)
    data = client.get(f"/projects/{pid}/suggestions").json()
    assert any(s["label"] == "EMAIL" for s in data["suggestions"])


def test_full_run_and_poster_citations(client, sample_docx):
    pid = upload(client, sample_docx)
    redact_fully(client, pid)
    assert client.post(f"/projects/{pid}/index").status_code == 200

    angles = client.get(f"/projects/{pid}/angles").json()
    assert angles

    run_id = client.post(f"/projects/{pid}/runs", json={
        "angle": angles[0]["angle"], "template_family": "default",
    }).json()["run_id"]
    status = wait_run(client, run_id)
    assert status["status"] == "complete", status

    poster = client.get(f"/runs/{run_id}/poster").json()
    headline = poster["content"]["content"]["headline"]
    assert headline["text"]
    cid = headline["citations"][0]
    assert cid in poster["citations"]
    assert poster["citations"][cid]["char_span"]  # click-through anchor

    pptx = client.get(f"/runs/{run_id}/exports/landscape.pptx")
    assert pptx.status_code == 200
    assert len(pptx.content) > 1000

    trace = client.get(f"/runs/{run_id}/trace").json()
    assert any(r["node_id"] == "generate" for r in trace)
