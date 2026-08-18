import pytest

from policy_poster.llm import MockLLM, extract_json


def test_mock_replays_and_records():
    llm = MockLLM(['{"ok": true}', "second"])
    out1 = llm.complete("sys", "user one")
    out2 = llm.complete("sys", "user two")
    assert out1 == '{"ok": true}'
    assert out2 == "second"
    assert len(llm.calls) == 2
    assert llm.calls[0] == ("sys", "user one")


def test_mock_raises_when_exhausted():
    llm = MockLLM([])
    with pytest.raises(RuntimeError):
        llm.complete("s", "u")


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = 'Here you go:\n```json\n{"a": [1, 2]}\n```\nDone.'
    assert extract_json(text) == {"a": [1, 2]}


def test_extract_json_prefixed_prose():
    text = 'Sure. The result is {"sufficient": false, "refined_query": "x"} as shown.'
    assert extract_json(text) == {"sufficient": False, "refined_query": "x"}


def test_extract_json_nested_braces():
    text = 'prefix {"outer": {"inner": 1}} suffix'
    assert extract_json(text) == {"outer": {"inner": 1}}


def test_extract_json_failure_returns_none():
    assert extract_json("no json here") is None
