import pytest
from fastapi.testclient import TestClient

from policy_poster.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_POSTER_LLM", "offline")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return TestClient(create_app(data_dir=str(tmp_path / "data")))


def test_get_settings_defaults_offline(client):
    data = client.get("/settings/llm").json()
    assert data["current"]["provider"] == "offline"
    assert "openai" in data["providers"]
    assert "anthropic" in data["providers"]
    assert "gemini" in data["providers"]
    assert "ollama" in data["providers"]
    assert data["current"]["api_key_set"] is False


def test_set_provider_and_persist(client, tmp_path):
    resp = client.post("/settings/llm", json={
        "provider": "ollama", "model": "llama3.1", "base_url": "", "api_key": "",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "ollama"
    assert client.get("/settings/llm").json()["current"]["model"] == "llama3.1"


def test_set_provider_validates(client):
    assert client.post("/settings/llm", json={
        "provider": "nonsense", "model": "x",
    }).status_code == 400
    # openai-compat needs a model name
    assert client.post("/settings/llm", json={
        "provider": "openai", "model": "", "api_key": "k",
    }).status_code == 400


def test_api_key_write_only(client):
    client.post("/settings/llm", json={
        "provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-secret",
    })
    data = client.get("/settings/llm").json()["current"]
    assert data["api_key_set"] is True
    assert "sk-secret" not in str(data)  # never echoed back
    # posting without a key keeps the stored key
    client.post("/settings/llm", json={
        "provider": "openai", "model": "gpt-4o", "api_key": "",
    })
    assert client.get("/settings/llm").json()["current"]["api_key_set"] is True


def test_probe_endpoint_offline(client):
    result = client.post("/settings/llm/test", json={
        "provider": "offline", "model": "", "base_url": "", "api_key": "",
    }).json()
    assert result["ok"] is True
