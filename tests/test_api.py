"""End-to-end API flow with the Claude calls replaced by fakes."""
import time

import pytest
from anthropic.lib._parse._transform import transform_schema
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import reader, reasoner
from app.main import app
from app.schemas import ChunkRead, Finding, PageNote, ReviewResult

CALLS = {}


def fake_read_chunk(model, first, last, blob, hint):
    CALLS.setdefault("reader", []).append((model, first, last))
    pages = [PageNote(page=p, doc_type="index" if p == 1 else "annexure", document_title=f"Doc {p}",
                      printed_page_number=str(p), page_number_position="top_right", language="English",
                      production="typed", legibility="clear", signatures=["counsel"], stamps_and_seals=[],
                      handwritten_corrections=False, key_content=f"page {p}", concerns=[])
             for p in range(first, last + 1)]
    return ChunkRead(case_category_guess="criminal", case_type_guess="CRM-M anticipatory bail",
                     pages=pages), {"input_tokens": 100, "output_tokens": 50}


def fake_review(system, digest):
    CALLS["reasoner_digest"] = digest
    CALLS["reasoner_system"] = system
    return ReviewResult(
        category="criminal", case_type="CRM-M u/s 482 BNSS", summary="Mostly in order.", readiness=72,
        strengths=["Index signed"],
        findings=[
            Finding(code="35(b)", title="Hard copy / soft copy note missing", severity="low",
                    confidence="likely", evidence="Index p.1 has no note.", pages="1",
                    fix="Add the note.", draft_text="Certified that contents of hard copy and soft copy are same."),
            Finding(code="50(a)", title="No AG advance copy acknowledgement", severity="high",
                    confidence="likely", evidence="No acknowledgement anywhere.", pages="",
                    fix="Serve AG office and annex acknowledgement.", draft_text=""),
        ],
    ), {"input_tokens": 1000, "output_tokens": 400}


def fake_chat(system, digest, findings_json, history, status_note, question):
    CALLS["chat"] = {"history": history, "status_note": status_note, "question": question}
    return f"Answer to: {question}", {}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(reader, "read_chunk", fake_read_chunk)
    monkeypatch.setattr(reasoner, "review", fake_review)
    monkeypatch.setattr(reasoner, "chat", fake_chat)
    with TestClient(app) as c:
        yield c


def wait_done(api, review_id):
    for _ in range(100):
        rv = api.get(f"/api/reviews/{review_id}").json()
        if rv["status"] in ("done", "error"):
            return rv
        time.sleep(0.05)
    raise AssertionError("review did not finish")


def test_schemas_are_accepted_by_structured_outputs():
    for model in (ChunkRead, ReviewResult):
        transform_schema(TypeAdapter(model).json_schema())


def test_rejects_non_pdf(api):
    r = api.post("/api/filings", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400


def test_full_flow(api, pdf_bytes):
    r = api.post("/api/filings", data={"title": "Test bail", "category": "auto", "reader": "sonnet"},
                 files={"file": ("bail.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 200, r.text
    rv = wait_done(api, r.json()["review_id"])
    assert rv["status"] == "done", rv["error"]
    assert rv["category"] == "criminal"            # auto-detected from reader votes
    assert rv["reader_model"] == "claude-sonnet-5"
    assert CALLS["reader"][0][0] == "claude-sonnet-5"
    assert rv["readiness"] == 72
    assert [f["severity"] for f in rv["findings"]] == ["high", "low"]   # sorted by severity
    assert rv["findings"][0]["grp"] == "service_advance_copy"
    assert "OFFICIAL CHECKLIST - CRIMINAL" in CALLS["reasoner_system"][1]["text"]
    assert "PREFLIGHT" in CALLS["reasoner_digest"] and "p.1 | index" in CALLS["reasoner_digest"]

    # Mark a finding dismissed with a note, then chat: the status reaches the model.
    fid = rv["findings"][0]["id"]
    assert api.patch(f"/api/findings/{fid}", json={"status": "dismissed", "user_note": "State is not a party"}).status_code == 200
    ans = api.post(f"/api/reviews/{rv['id']}/chat", json={"message": "Why 35(b)?"}).json()
    assert ans["answer"] == "Answer to: Why 35(b)?"
    assert "dismissed" in CALLS["chat"]["status_note"]
    api.post(f"/api/reviews/{rv['id']}/chat", json={"message": "Next?"})
    assert CALLS["chat"]["history"][0] == {"role": "user", "content": "Why 35(b)?"}

    listing = api.get("/api/filings").json()
    row = next(x for x in listing if x["id"] == rv["filing"]["id"])
    assert row["open_findings"] == 1 and row["open_high"] == 0

    # House note is fed into the next review's system prompt.
    api.post("/api/house-notes", json={"category": "criminal", "code": "50(a)", "note": "Always annex AG receipt"})
    rerun = api.post(f"/api/filings/{row['id']}/reviews", json={"reader": "haiku"}).json()
    rv2 = wait_done(api, rerun["review_id"])
    assert rv2["reader_model"] == "claude-haiku-4-5"
    assert "Always annex AG receipt" in CALLS["reasoner_system"][-1]["text"]

    assert api.delete(f"/api/filings/{row['id']}").status_code == 200
    assert api.get(f"/api/reviews/{rv['id']}").status_code == 404


def test_reader_failure_is_reported(api, monkeypatch, pdf_bytes):
    def boom(*a, **k):
        raise RuntimeError("reader exploded")
    monkeypatch.setattr(reader, "read_chunk", boom)
    r = api.post("/api/filings", data={"category": "civil"},
                 files={"file": ("f.pdf", pdf_bytes, "application/pdf")})
    rv = wait_done(api, r.json()["review_id"])
    assert rv["status"] == "error" and "reader exploded" in rv["error"]
