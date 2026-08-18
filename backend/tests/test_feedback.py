from policy_poster.feedback import FeedbackEntry, FeedbackStore


def make_store(tmp_path):
    return FeedbackStore(str(tmp_path / "feedback"))


def entry(**over):
    base = dict(
        kind="user_edit",
        policy_type="data handling",
        angle="urgency",
        before="Report incidents when convenient",
        after="Report incidents within 24 hours",
        note="user tightened the deadline wording",
    )
    base.update(over)
    return FeedbackEntry(**base)


def test_record_and_list(tmp_path):
    store = make_store(tmp_path)
    eid = store.record(entry())
    items = store.list()
    assert len(items) == 1
    assert items[0].entry_id == eid
    assert not items[0].promoted


def test_only_promoted_entries_shape_generation(tmp_path):
    store = make_store(tmp_path)
    e1 = store.record(entry(after="Promoted example"))
    store.record(entry(after="Unpromoted example"))
    store.promote(e1)
    exemplars = store.exemplars("data handling policy with urgency", n=5)
    texts = [e.after for e in exemplars]
    assert "Promoted example" in texts
    assert "Unpromoted example" not in texts


def test_similarity_ranking(tmp_path):
    store = make_store(tmp_path)
    a = store.record(entry(policy_type="phishing response", angle="urgency",
                           after="Phishing example"))
    b = store.record(entry(policy_type="leave policy", angle="reassuring",
                           after="Leave example"))
    store.promote(a)
    store.promote(b)
    top = store.exemplars("urgency around phishing reporting", n=1)
    assert top[0].after == "Phishing example"


def test_remove_stops_influence(tmp_path):
    store = make_store(tmp_path)
    eid = store.record(entry())
    store.promote(eid)
    assert store.exemplars("data handling", n=5)
    store.remove(eid)
    assert store.exemplars("data handling", n=5) == []
    assert store.list() == []


def test_no_auto_promotion(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.record(entry(after=f"example {i}"))
    assert store.exemplars("anything", n=5) == []  # HARD: human promotion only
