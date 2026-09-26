from app.pdf_tools import _ranges, inspect_pdf, preflight, split_pdf

from .conftest import make_pdf


def test_ranges():
    assert _ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"
    assert _ranges([]) == ""


def test_preflight_flags_non_legal_pages(pdf_bytes):
    info = inspect_pdf(pdf_bytes)
    assert info["page_count"] == 5
    size = next(s for s in preflight(info) if s["check"].startswith("Legal size"))
    assert size["status"] == "warn" and size["pages"] == "4-5"
    scanned = next(s for s in preflight(info) if s["check"].startswith("Text layer"))
    assert scanned["pages"] == "1-5"   # blank pages have no text layer


def test_all_legal_passes():
    info = inspect_pdf(make_pdf([(8.5, 14)] * 2))
    size = next(s for s in preflight(info) if s["check"].startswith("Legal size"))
    assert size["status"] == "pass"


def test_split_covers_every_page_once():
    data = make_pdf([(8.5, 14)] * 45)
    chunks = list(split_pdf(data, 20))
    assert [(a, b) for a, b, _ in chunks] == [(1, 20), (21, 40), (41, 45)]


def test_split_halves_oversized_chunks():
    data = make_pdf([(8.5, 14)] * 8)
    chunks = list(split_pdf(data, 8, max_chunk_bytes=1))
    assert [(a, b) for a, b, _ in chunks] == [(i, i) for i in range(1, 9)]
