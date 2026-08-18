import json

import httpx

from policy_poster.llm import MockLLM, complete_json
from policy_poster.llm_offline import OfflineLLM
from policy_poster.llm_providers import LLMSettings, OpenAICompatLLM, GeminiLLM

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def recording_transport(handler):
    return httpx.MockTransport(handler)


def test_openai_compat_uses_json_schema_response_format():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
        })

    llm = OpenAICompatLLM(base_url="https://x/v1", model="m", api_key="k",
                          transport=recording_transport(handler))
    out = complete_json(llm, "sys", "user", SCHEMA)
    assert out == {"ok": True}
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == SCHEMA


def test_openai_compat_falls_back_to_json_object_then_plain():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        rf = body.get("response_format", {})
        if rf.get("type") == "json_schema":
            return httpx.Response(400, json={"error": {
                "message": "response_format json_schema is not supported"}})
        if rf.get("type") == "json_object":
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"ok": false}'}}],
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "plain"}}],
        })

    llm = OpenAICompatLLM(base_url="https://x/v1", model="m", api_key="k",
                          transport=recording_transport(handler))
    out = complete_json(llm, "sys", "user", SCHEMA)
    assert out == {"ok": False}
    # capability cached: next call goes straight to json_object (2 + 1 calls)
    out2 = complete_json(llm, "sys", "user", SCHEMA)
    assert out2 == {"ok": False}
    assert len(seen) == 3


def test_gemini_uses_response_schema():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
        })

    llm = GeminiLLM(model="gemini-2.0-flash", api_key="k",
                    transport=recording_transport(handler))
    out = complete_json(llm, "sys", "user", SCHEMA)
    assert out == {"ok": True}
    gc = seen[0]["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert "responseSchema" in gc
    # gemini schema subset: additionalProperties stripped
    assert "additionalProperties" not in json.dumps(gc["responseSchema"])


def test_mock_and_offline_fall_back_to_extract_json():
    out = complete_json(MockLLM(['{"ok": true}']), "s", "u", SCHEMA)
    assert out == {"ok": True}
    # offline llm answers its scripted dialects through plain complete
    out2 = complete_json(OfflineLLM(), "groundedness verifier", "x", SCHEMA)
    assert isinstance(out2, dict)
