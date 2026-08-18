from policy_poster.trace import TraceRecord, TraceStore


def test_append_and_query_roundtrip(tmp_path):
    store = TraceStore(str(tmp_path))
    rec = TraceRecord(
        run_id="r1", node_id="generate", attempt=1, agent="content_generator",
        timestamp="2026-08-18T00:00:00Z", input_hash="abc", output={"ok": True},
        retrieved_chunk_ids=["c1"], verdict="pass", findings=[],
        tokens_in=100, tokens_out=50, latency_ms=1200, model="mock",
    )
    store.append(rec)
    store.append(TraceRecord(
        run_id="r2", node_id="x", attempt=1, agent="a", timestamp="t",
        input_hash="h", output={}, retrieved_chunk_ids=[], verdict="pass",
        findings=[], tokens_in=0, tokens_out=0, latency_ms=0, model="mock",
    ))
    got = store.query("r1")
    assert len(got) == 1
    assert got[0].node_id == "generate"
    assert got[0].output == {"ok": True}
    assert len(store.query("r2")) == 1
    assert store.query("missing") == []
