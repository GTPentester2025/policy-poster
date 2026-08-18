import pytest

from policy_poster.egress_log import LoggingLLM, read_egress_log
from policy_poster.llm import MockLLM


def test_records_complete_and_json(tmp_path):
    path = str(tmp_path / "egress.jsonl")
    llm = LoggingLLM(MockLLM(["hello", '{"ok": true}']), path,
                     context={"run_id": "r1"})
    llm.context.update(node="generate", attempt=1)
    assert llm.complete("SYS", "USER") == "hello"
    assert llm.complete_json("SYS2", "USER2", {"type": "object"}) == {"ok": True}
    calls = read_egress_log(path)
    assert len(calls) == 2
    assert calls[0]["node"] == "generate" and calls[0]["run_id"] == "r1"
    assert calls[0]["response"] == "hello"
    assert calls[1]["kind"] == "complete_json"
    assert '"ok"' in calls[1]["response"]


def test_records_errors_and_reraises(tmp_path):
    path = str(tmp_path / "egress.jsonl")
    llm = LoggingLLM(MockLLM([]), path)
    with pytest.raises(RuntimeError):
        llm.complete("s", "u")
    calls = read_egress_log(path)
    assert calls[0]["error"]


def test_log_write_failure_never_breaks_call(tmp_path):
    llm = LoggingLLM(MockLLM(["ok"]), str(tmp_path))  # path is a dir → write fails
    assert llm.complete("s", "u") == "ok"
