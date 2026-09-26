"""Orchestrates one review: preflight -> reader -> reasoner -> findings in SQLite."""
import io
import json
import logging
import threading
from collections import Counter
from pathlib import Path

import anthropic
from pypdf import PdfReader

from . import config, db, reader, reasoner
from .pdf_tools import inspect_pdf, preflight

log = logging.getLogger("phhc.pipeline")
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _set(review_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.session() as conn:
        conn.execute(f"UPDATE reviews SET {cols} WHERE id = ?", (*fields.values(), review_id))


def page_texts(pdf_bytes: bytes) -> dict[int, str]:
    out = {}
    for i, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages, start=1):
        try:
            out[i] = (page.extract_text() or "").strip()
        except Exception:
            out[i] = ""
    return out


def load_context(conn, review: dict, filing: dict) -> tuple[list[dict], str]:
    """Rebuild the reasoner's system prompt and digest for a stored review."""
    category = review["category"]
    system = reasoner.system_blocks(category, db.checklist(conn, category),
                                    db.house_notes_for(conn, category))
    pdf_bytes = Path(filing["stored_path"]).read_bytes()
    digest = reasoner.digest_text(filing, json.loads(review["preflight_json"]),
                                  json.loads(review["digest_json"]), page_texts(pdf_bytes))
    return system, digest


def run_review(review_id: int) -> None:
    try:
        _run(review_id)
    except Exception as exc:  # surface every failure on the dashboard
        log.exception("review %s failed", review_id)
        _set(review_id, status="error", error=str(exc)[:2000], finished_at=db.now())


def _run(review_id: int) -> None:
    with db.session() as conn:
        rv = db.row(conn, "SELECT * FROM reviews WHERE id = ?", (review_id,))
        filing = db.row(conn, "SELECT * FROM filings WHERE id = ?", (rv["filing_id"],))
    pdf_bytes = Path(filing["stored_path"]).read_bytes()

    _set(review_id, status="reading", progress="Running preflight checks")
    info = inspect_pdf(pdf_bytes)
    signals = preflight(info)
    _set(review_id, preflight_json=json.dumps(signals))

    hint = f"{filing['title']}; category: {filing['category']}; type: {filing['case_type_hint']}"
    reader_key = "sonnet" if "sonnet" in rv["reader_model"] else "haiku"
    digest = reader.read_filing(client(), pdf_bytes, reader_key, hint,
                                on_progress=lambda m: _set(review_id, progress=m))

    category = filing["category"]
    if category not in config.CATEGORIES:
        category = digest["category_guess"] if digest["category_guess"] in config.CATEGORIES else "civil"
    _set(review_id, digest_json=json.dumps(digest), category=category, status="reasoning",
         progress="Opus is scrutinising the paper book against the checklist")

    with db.session() as conn:
        rv = db.row(conn, "SELECT * FROM reviews WHERE id = ?", (review_id,))
        system, digest_str = load_context(conn, rv, filing)

    result, usage = reasoner.review(client(), system, digest_str)
    total = Counter(digest["usage"])
    total_usage = {"reader": dict(total), "reasoner": usage}

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings = sorted(result.findings, key=lambda f: severity_rank[f.severity])
    with db.session() as conn:
        groups = {i["code"]: i["grp"] for i in db.checklist(conn, category)}
        for order, f in enumerate(findings):
            code = f.code.strip()
            conn.execute(
                """INSERT INTO findings (review_id, code, grp, title, severity, confidence,
                       evidence, pages, fix, draft_text, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (review_id, code, groups.get(code, "other"), f.title, f.severity, f.confidence,
                 f.evidence, f.pages, f.fix, f.draft_text, order),
            )
        conn.execute(
            """UPDATE reviews SET status = 'done', progress = '', category = ?, case_type = ?,
                   summary = ?, readiness = ?, strengths_json = ?, usage_json = ?,
                   finished_at = ? WHERE id = ?""",
            (category, result.case_type, result.summary,
             max(0, min(100, result.readiness)), json.dumps(result.strengths),
             json.dumps(total_usage), db.now(), review_id),
        )


def start_review(review_id: int) -> None:
    threading.Thread(target=run_review, args=(review_id,), daemon=True).start()
