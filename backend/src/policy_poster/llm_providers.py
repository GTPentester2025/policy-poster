"""Provider-independent LLM layer.

Every pipeline component talks to the `LLMClient` protocol
(`complete(system, user, max_tokens) -> str`), so the model vendor is a pure
configuration concern. Supported out of the box:

- Any **OpenAI-compatible** chat endpoint (`/chat/completions`): OpenAI,
  Azure OpenAI (compat URL), Groq, Mistral, DeepSeek, Together, OpenRouter,
  xAI, and local servers: Ollama, LM Studio, vLLM, llama.cpp.
- **Anthropic** (Claude) via the existing `AnthropicLLM`.
- **Google Gemini** via the REST `generateContent` endpoint.
- **offline**: the deterministic zero-egress fallback.

Settings come from the runtime settings API or environment variables; raw
HTTP (httpx) is used for the generic providers so no vendor SDK is required.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx

from .llm import LLMClient
from .llm_offline import OfflineLLM

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# providers that speak the OpenAI chat-completions dialect, with default bases
OPENAI_COMPAT_BASES = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8001/v1",
    "custom": "",  # base_url required
}

PROVIDERS = ["offline", "anthropic", "gemini", *OPENAI_COMPAT_BASES.keys()]


@dataclass
class LLMSettings:
    provider: str = "offline"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    embed_model: str = ""  # optional: /embeddings model on the same endpoint
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "LLMSettings":
        provider = os.environ.get("POLICY_POSTER_LLM", "").strip().lower()
        if not provider:
            # back-compat: bare ANTHROPIC_API_KEY selects anthropic, else offline
            provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "offline"
        return cls(
            provider=provider,
            model=os.environ.get("POLICY_POSTER_LLM_MODEL", ""),
            base_url=os.environ.get("POLICY_POSTER_LLM_BASE_URL", ""),
            api_key=os.environ.get("POLICY_POSTER_LLM_API_KEY", ""),
            embed_model=os.environ.get("POLICY_POSTER_EMBED_MODEL", ""),
        )

    def redacted(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "embed_model": self.embed_model,
            "api_key_set": bool(self.api_key),
        }


_DANGEROUS_SCHEMES = ("javascript:", "data:", "file:", "vbscript:")
_LOOPBACK_RE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|\[::1\])(:\d+)?$", re.IGNORECASE
)


def normalize_base_url(raw: str) -> str:
    """Accepts a bare host, an API root, or a pasted .../chat/completions URL.
    Loopback hosts default to http, everything else to https; /v1 is appended
    when only a host was given. (Pattern adopted from the reference app.)"""
    s = (raw or "").strip().rstrip("/")
    if not s:
        return s
    low = s.lower()
    if any(low.startswith(scheme) for scheme in _DANGEROUS_SCHEMES):
        raise ValueError(f"unsupported URL scheme in base_url: {raw!r}")
    if not low.startswith(("http://", "https://")):
        host = s.split("/", 1)[0]
        scheme = "http" if _LOOPBACK_RE.match(host) else "https"
        s = f"{scheme}://{s}"
    if s.lower().endswith("/chat/completions"):
        s = s[: -len("/chat/completions")]
    # append /v1 only when the URL has no path at all
    rest = s.split("://", 1)[1]
    if "/" not in rest:
        s = f"{s}/v1"
    return s.rstrip("/")


class OpenAICompatLLM:
    """Raw-HTTP client for any /chat/completions endpoint.

    Self-heals the token-parameter shape: reasoning-family models reject
    `max_tokens`; on a 400 naming the parameter we retry once with
    `max_completion_tokens` and cache the winning shape per model."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 extra_headers: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._headers.update(extra_headers or {})
        self._client = httpx.Client(timeout=_TIMEOUT, transport=transport)
        self._token_param = "max_tokens"

    def _post(self, system: str, user: str, max_tokens: int,
              token_param: str) -> httpx.Response:
        return self._client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json={
                "model": self.model,
                token_param: max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

    @staticmethod
    def _is_token_param_error(resp: httpx.Response) -> bool:
        if resp.status_code != 400:
            return False
        text = resp.text.lower()
        return "max_tokens" in text or "max_completion_tokens" in text

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        resp = self._post(system, user, max_tokens, self._token_param)
        if self._is_token_param_error(resp):
            alternate = (
                "max_completion_tokens"
                if self._token_param == "max_tokens" else "max_tokens"
            )
            retry = self._post(system, user, max_tokens, alternate)
            if retry.status_code == 200:
                self._token_param = alternate  # cache the winning shape
            resp = retry
        if resp.status_code != 200:
            raise RuntimeError(_error_detail(resp))
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected response shape: {exc}: {str(data)[:300]}")


class GeminiLLM:
    """Google Gemini REST generateContent client (no SDK)."""

    def __init__(self, model: str, api_key: str,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT, transport=transport)

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        resp = self._client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key,
                     "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(_error_detail(resp))
        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected response shape: {exc}: {str(data)[:300]}")


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        detail = (
            data.get("error", {}).get("message")
            or data.get("message")
            or str(data)[:300]
        )
    except Exception:
        detail = resp.text[:300]
    return f"LLM provider returned HTTP {resp.status_code}: {detail}"


def make_llm(settings: LLMSettings,
             transport: httpx.BaseTransport | None = None) -> LLMClient:
    provider = (settings.provider or "offline").lower()

    if provider == "offline":
        return OfflineLLM()

    if provider == "anthropic":
        from .llm import AnthropicLLM

        if settings.api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", settings.api_key)
        return AnthropicLLM(model=settings.model or "claude-opus-5")

    if provider == "gemini":
        return GeminiLLM(
            model=settings.model or "gemini-2.0-flash",
            api_key=settings.api_key,
            transport=transport,
        )

    if provider in OPENAI_COMPAT_BASES:
        base_url = settings.base_url or OPENAI_COMPAT_BASES[provider]
        if not base_url:
            raise ValueError(f"provider {provider!r} requires base_url")
        if not settings.model:
            raise ValueError(f"provider {provider!r} requires a model name")
        return OpenAICompatLLM(
            base_url=normalize_base_url(base_url),
            model=settings.model,
            api_key=settings.api_key or None,
            extra_headers=settings.extra_headers,
            transport=transport,
        )

    raise ValueError(f"unknown LLM provider: {provider!r} (known: {PROVIDERS})")


class OpenAICompatEmbedder:
    """Embeddings from the linked provider's /embeddings endpoint (OpenAI
    dialect — OpenAI, Ollama /v1, LM Studio, vLLM, OpenRouter, ...). Vector
    dimension is discovered on first call."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = normalize_base_url(base_url)
        self.model = model
        self.dim = 0  # set after first embed
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(timeout=_TIMEOUT, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers=self._headers,
            json={"model": self.model, "input": texts},
        )
        if resp.status_code != 200:
            raise RuntimeError(_error_detail(resp))
        data = resp.json().get("data", [])
        # OpenAI returns items with an "index" field; keep input order
        data = sorted(data, key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in data]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


def make_embedder(settings: LLMSettings,
                  transport: httpx.BaseTransport | None = None):
    """Embedder for retrieval: the linked provider's /embeddings endpoint when
    `embed_model` is set on an OpenAI-compatible provider; else fastembed via
    POLICY_POSTER_EMBEDDER=fastembed; else the deterministic hashing embedder."""
    from .embedder import HashingEmbedder

    provider = (settings.provider or "").lower()
    if settings.embed_model and provider in OPENAI_COMPAT_BASES:
        base = settings.base_url or OPENAI_COMPAT_BASES[provider]
        if base:
            return OpenAICompatEmbedder(
                base_url=base, model=settings.embed_model,
                api_key=settings.api_key or None, transport=transport,
            )
    if os.environ.get("POLICY_POSTER_EMBEDDER") == "fastembed":
        from .embedder import FastEmbedEmbedder

        return FastEmbedEmbedder()
    return HashingEmbedder()


def _parse_models_payload(data) -> list[str]:
    """Tolerates {data:[...]}, {models:[...]}, or a bare array; items may be
    strings or {id|name|model} objects. Bad shapes yield []."""
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
            if model_id:
                out.append(str(model_id))
    return out


def list_models(settings: LLMSettings,
                transport: httpx.BaseTransport | None = None) -> dict:
    """Query the provider's model catalog; never raises."""
    provider = (settings.provider or "").lower()
    client = httpx.Client(timeout=httpx.Timeout(20.0), transport=transport)
    try:
        if provider in OPENAI_COMPAT_BASES:
            base = normalize_base_url(
                settings.base_url or OPENAI_COMPAT_BASES[provider]
            )
            headers = (
                {"Authorization": f"Bearer {settings.api_key}"}
                if settings.api_key else {}
            )
            resp = client.get(f"{base}/models", headers=headers)
        elif provider == "anthropic":
            resp = client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": settings.api_key,
                         "anthropic-version": "2023-06-01"},
            )
        elif provider == "gemini":
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": settings.api_key},
            )
        else:
            return {"ok": False, "error": f"model listing unsupported for {provider!r}"}
        if resp.status_code != 200:
            return {"ok": False, "error": _error_detail(resp)}
        models = _parse_models_payload(resp.json())
        # gemini ids come as "models/gemini-..." — strip the prefix
        models = [m.split("/", 1)[1] if m.startswith("models/") else m for m in models]
        return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.close()


def probe_connection(settings: LLMSettings,
                    transport: httpx.BaseTransport | None = None) -> dict:
    """One cheap probe call; never raises."""
    try:
        llm = make_llm(settings, transport=transport)
        reply = llm.complete(
            "You are a connectivity probe. Reply with the single word: pong",
            "ping", max_tokens=16,
        )
        return {"ok": True, "reply": reply.strip()[:100]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

