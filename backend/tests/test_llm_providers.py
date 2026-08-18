import json

import httpx
import pytest

from policy_poster.llm_offline import OfflineLLM
from policy_poster.llm_providers import (
    GeminiLLM,
    LLMSettings,
    OpenAICompatLLM,
    make_llm,
    probe_connection,
)


from policy_poster.llm_providers import list_models, normalize_base_url


def test_normalize_base_url():
    # bare host → loopback http + /v1
    assert normalize_base_url("localhost:11434") == "http://localhost:11434/v1"
    assert normalize_base_url("127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    # bare remote host → https + /v1
    assert normalize_base_url("api.example.com") == "https://api.example.com/v1"
    # pasted full chat-completions URL → stripped
    assert normalize_base_url("https://h/v1/chat/completions") == "https://h/v1"
    # already-correct API roots stay untouched
    assert normalize_base_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"
    assert normalize_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    # host with scheme but no path → /v1 appended
    assert normalize_base_url("https://myhost.internal") == "https://myhost.internal/v1"
    # dangerous schemes rejected
    with pytest.raises(ValueError):
        normalize_base_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        normalize_base_url("file:///etc/passwd")


class Transport(httpx.MockTransport):
    """Records requests, returns scripted response."""

    def __init__(self, payload, status=200):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, json=payload)

        super().__init__(handler)


def test_openai_compat_request_shape():
    transport = Transport({
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    })
    llm = OpenAICompatLLM(
        base_url="https://api.example.com/v1", model="test-model",
        api_key="sk-x", transport=transport,
    )
    out = llm.complete("SYS", "USER", max_tokens=99)
    assert out == "hello"
    req = transport.requests[0]
    assert str(req.url) == "https://api.example.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-x"
    body = json.loads(req.content)
    assert body["model"] == "test-model"
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "USER"}
    assert body["max_tokens"] == 99


def test_openai_compat_no_key_no_auth_header():
    # local servers (Ollama, LM Studio, vLLM) need no key
    transport = Transport({"choices": [{"message": {"content": "ok"}}]})
    llm = OpenAICompatLLM(base_url="http://localhost:11434/v1",
                          model="llama3", api_key=None, transport=transport)
    llm.complete("s", "u")
    assert "authorization" not in transport.requests[0].headers


def test_openai_compat_error_raises():
    transport = Transport({"error": {"message": "bad key"}}, status=401)
    llm = OpenAICompatLLM(base_url="https://x/v1", model="m",
                          api_key="k", transport=transport)
    with pytest.raises(RuntimeError, match="bad key"):
        llm.complete("s", "u")


def test_gemini_request_shape():
    transport = Transport({
        "candidates": [{"content": {"parts": [{"text": "gemini says"}]}}],
    })
    llm = GeminiLLM(model="gemini-2.0-flash", api_key="g-key", transport=transport)
    out = llm.complete("SYS", "USER", max_tokens=50)
    assert out == "gemini says"
    req = transport.requests[0]
    assert "generateContent" in str(req.url)
    assert "gemini-2.0-flash" in str(req.url)
    assert req.headers["x-goog-api-key"] == "g-key"
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert body["contents"][0]["parts"][0]["text"] == "USER"
    assert body["generationConfig"]["maxOutputTokens"] == 50


def test_make_llm_offline_default():
    llm = make_llm(LLMSettings(provider="offline"))
    assert isinstance(llm, OfflineLLM)


def test_make_llm_openai_family():
    for provider in ["openai", "ollama", "groq", "openrouter", "custom"]:
        llm = make_llm(LLMSettings(provider=provider, model="m",
                                   base_url="http://x/v1", api_key="k"))
        assert isinstance(llm, OpenAICompatLLM), provider


def test_make_llm_default_base_urls():
    llm = make_llm(LLMSettings(provider="ollama", model="llama3"))
    assert llm.base_url.startswith("http://localhost:11434")
    llm2 = make_llm(LLMSettings(provider="openai", model="gpt-4o", api_key="k"))
    assert "api.openai.com" in llm2.base_url


def test_make_llm_gemini():
    assert isinstance(
        make_llm(LLMSettings(provider="gemini", model="gemini-2.0-flash", api_key="k")),
        GeminiLLM,
    )


def test_reasoning_model_param_self_healing():
    """400 naming max_tokens → retry once with max_completion_tokens, cache winner."""
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
            }})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = OpenAICompatLLM(base_url="https://x/v1", model="o4-mini", api_key="k",
                          transport=httpx.MockTransport(handler))
    assert llm.complete("s", "u", max_tokens=10) == "ok"
    assert "max_tokens" in calls[0] and "max_completion_tokens" in calls[1]
    # cached: next call goes straight to the healed shape
    assert llm.complete("s", "u", max_tokens=10) == "ok"
    assert "max_completion_tokens" in calls[2] and len(calls) == 3


def test_list_models_tolerant_parsing():
    for payload in [
        {"data": [{"id": "m1"}, {"id": "m2"}]},          # OpenAI
        {"models": [{"name": "m1"}, {"model": "m2"}]},   # Ollama-ish
        ["m1", "m2"],                                     # bare array
    ]:
        transport = Transport(payload)
        result = list_models(
            LLMSettings(provider="custom", base_url="http://x/v1", api_key="k"),
            transport=transport,
        )
        assert result["ok"] is True
        assert result["models"] == ["m1", "m2"], payload


def test_list_models_failure_never_raises():
    transport = Transport({"error": {"message": "down"}}, status=502)
    result = list_models(
        LLMSettings(provider="custom", base_url="http://x/v1", api_key="k"),
        transport=transport,
    )
    assert result["ok"] is False


def test_provider_embedder_request_shape():
    from policy_poster.llm_providers import OpenAICompatEmbedder

    transport = Transport({"data": [
        {"index": 1, "embedding": [0.3, 0.4]},
        {"index": 0, "embedding": [0.1, 0.2]},
    ]})
    emb = OpenAICompatEmbedder(base_url="http://localhost:11434/v1",
                               model="nomic-embed-text", api_key=None,
                               transport=transport)
    vectors = emb.embed(["a", "b"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]  # input order restored via index
    assert emb.dim == 2
    body = json.loads(transport.requests[0].content)
    assert body == {"model": "nomic-embed-text", "input": ["a", "b"]}
    assert str(transport.requests[0].url).endswith("/v1/embeddings")


def test_make_embedder_selection(monkeypatch):
    from policy_poster.embedder import HashingEmbedder
    from policy_poster.llm_providers import OpenAICompatEmbedder, make_embedder

    monkeypatch.delenv("POLICY_POSTER_EMBEDDER", raising=False)
    # explicit embed_model on an openai-compat provider → provider embeddings
    emb, source = make_embedder(LLMSettings(provider="ollama", model="llama3",
                                            embed_model="mxbai-embed-large"))
    assert isinstance(emb, OpenAICompatEmbedder)
    assert "mxbai-embed-large" in source
    # blank embed_model but the provider has a known default → auto AI embeddings
    emb, source = make_embedder(LLMSettings(provider="ollama", model="llama3"))
    assert isinstance(emb, OpenAICompatEmbedder)
    assert "nomic-embed-text" in source
    # provider with no embeddings endpoint → semantic local fallback (fastembed)
    emb, source = make_embedder(LLMSettings(provider="anthropic",
                                            model="claude-opus-5"))
    assert "local" in source
    # forced hermetic mode → hashing
    monkeypatch.setenv("POLICY_POSTER_EMBEDDER", "hashing")
    emb, source = make_embedder(LLMSettings(provider="anthropic",
                                            model="claude-opus-5"))
    assert isinstance(emb, HashingEmbedder)
    monkeypatch.delenv("POLICY_POSTER_EMBEDDER")
    # gemini → Gemini embeddings
    from policy_poster.llm_providers import GeminiEmbedder
    emb, source = make_embedder(LLMSettings(provider="gemini", api_key="k"))
    assert isinstance(emb, GeminiEmbedder)


def test_connection_probe_ok():
    transport = Transport({"choices": [{"message": {"content": "pong"}}]})
    result = probe_connection(
        LLMSettings(provider="custom", model="m", base_url="http://x/v1", api_key="k"),
        transport=transport,
    )
    assert result["ok"] is True
    assert "pong" in result["reply"]


def test_connection_probe_failure():
    transport = Transport({"error": {"message": "nope"}}, status=500)
    result = probe_connection(
        LLMSettings(provider="custom", model="m", base_url="http://x/v1", api_key="k"),
        transport=transport,
    )
    assert result["ok"] is False
    assert "nope" in result["error"]

