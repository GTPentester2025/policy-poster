from policy_poster.chunker import chunk_document
from policy_poster.content import DEFAULT_CONTRACT
from policy_poster.docx_parser import parse_docx
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex
from policy_poster.llm_offline import OfflineLLM
from policy_poster.agents.strategy import propose_angles
from policy_poster.pipeline import run_poster_pipeline
from policy_poster.redaction import RedactionLedger


def build(sample_docx, tmp_path):
    doc = parse_docx(str(sample_docx))
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")
    ledger.add("security@acme.com", "email")
    chunks = chunk_document(doc, ledger)
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    return doc, ledger, chunks, index


def test_strategy_offline(sample_docx, tmp_path):
    _, _, _, index = build(sample_docx, tmp_path)
    angles = propose_angles(index, OfflineLLM(), n=4)
    assert angles
    for a in angles:
        assert a.clause_ids


def test_full_pipeline_offline(sample_docx, tmp_path):
    doc, ledger, chunks, index = build(sample_docx, tmp_path)
    outcome = run_poster_pipeline(
        run_id="offline1", index=index, ledger=ledger, all_chunks=chunks,
        angle="general awareness of data handling", contract=DEFAULT_CONTRACT,
        llm=OfflineLLM(), work_dir=str(tmp_path / "run"),
    )
    assert outcome.status == "complete", getattr(outcome.diagnostic, "attempts", None)
    content = outcome.state["content"]
    # every slot within budget and cited
    for name in ["eyebrow", "headline", "subhead", "callout", "cta"]:
        slot = content["content"][name]
        assert slot["text"].strip()
        assert slot["citations"]
    assert outcome.state["exports"]
