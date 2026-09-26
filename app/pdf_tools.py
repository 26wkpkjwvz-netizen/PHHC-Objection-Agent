"""Deterministic PDF checks and page chunking (no model calls)."""
import io
from collections import Counter

from pypdf import PdfReader, PdfWriter

LEGAL_IN = (8.5, 14.0)
A4_IN = (8.27, 11.69)
SIZE_TOLERANCE_IN = 0.15
PERMITTED_FONTS = ("times", "thorndale")
MIN_TEXT_CHARS = 40  # below this a page is treated as scanned / image-only


def _page_fonts(page) -> set[str]:
    fonts = set()
    try:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else {}
        font_dict = resources.get("/Font")
        if font_dict is None:
            return fonts
        for ref in font_dict.get_object().values():
            base = str(ref.get_object().get("/BaseFont", ""))
            # Subset fonts look like /ABCDEF+TimesNewRomanPSMT
            fonts.add(base.lstrip("/").split("+")[-1])
    except Exception:  # malformed resources should never break a review
        pass
    return fonts


def _size_label(w_in: float, h_in: float) -> str:
    short, long_ = sorted((w_in, h_in))
    if abs(short - LEGAL_IN[0]) <= SIZE_TOLERANCE_IN and abs(long_ - LEGAL_IN[1]) <= SIZE_TOLERANCE_IN:
        return "legal"
    if abs(short - A4_IN[0]) <= SIZE_TOLERANCE_IN and abs(long_ - A4_IN[1]) <= SIZE_TOLERANCE_IN:
        return "a4"
    if abs(short - 8.5) <= SIZE_TOLERANCE_IN and abs(long_ - 11.0) <= SIZE_TOLERANCE_IN:
        return "letter"
    return f"{w_in:.2f}x{h_in:.2f}in"


def inspect_pdf(data: bytes) -> dict:
    """Page count, sizes, fonts and text-layer coverage for a PDF."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        w_in, h_in = float(box.width) / 72, float(box.height) / 72
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append({
            "page": i,
            "size": _size_label(w_in, h_in),
            "text_chars": len(text.strip()),
            "fonts": sorted(_page_fonts(page)),
        })
    return {"page_count": len(pages), "encrypted": reader.is_encrypted, "pages": pages}


def _ranges(nums: list[int]) -> str:
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'."""
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def preflight(info: dict) -> list[dict]:
    """Machine checks mapped to checklist code 32 (format) and legibility."""
    pages = info["pages"]
    signals = []
    if not pages:
        return [{"check": "Readable PDF", "status": "fail", "code": "62",
                 "detail": "The PDF has no pages.", "pages": ""}]

    non_legal = [p["page"] for p in pages if p["size"] != "legal"]
    sizes = Counter(p["size"] for p in pages)
    if non_legal:
        signals.append({
            "check": "Legal size paper (8.5 x 14 in)", "status": "warn", "code": "32(a)",
            "detail": f"{len(non_legal)} of {len(pages)} pages are not legal size "
                      f"({', '.join(f'{k}: {v}' for k, v in sizes.most_common())}). "
                      "Pleadings must be on legal size paper; annexure copies may differ but "
                      "the Registry often flags this.",
            "pages": _ranges(non_legal),
        })
    else:
        signals.append({"check": "Legal size paper (8.5 x 14 in)", "status": "pass",
                        "code": "32(a)", "detail": "All pages are legal size.", "pages": ""})

    scanned = [p["page"] for p in pages if p["text_chars"] < MIN_TEXT_CHARS]
    if scanned:
        signals.append({
            "check": "Text layer / scanned pages", "status": "info", "code": "32(b)",
            "detail": f"{len(scanned)} pages have no usable text layer (scans or images). "
                      "The reader checks them visually; dim or illegible scans need a typed copy.",
            "pages": _ranges(scanned),
        })

    typed = [p for p in pages if p["text_chars"] >= MIN_TEXT_CHARS and p["fonts"]]
    off_font = [p["page"] for p in typed
                if not any(f.lower().startswith(PERMITTED_FONTS) or
                           any(k in f.lower() for k in PERMITTED_FONTS) for f in p["fonts"])]
    if typed:
        all_fonts = Counter(f for p in typed for f in p["fonts"])
        if off_font:
            signals.append({
                "check": "Font (Times New Roman / Thorndale)", "status": "warn", "code": "32(a)",
                "detail": f"{len(off_font)} typed pages use no Times New Roman/Thorndale font. "
                          f"Fonts seen: {', '.join(f for f, _ in all_fonts.most_common(6))}.",
                "pages": _ranges(off_font),
            })
        else:
            signals.append({"check": "Font (Times New Roman / Thorndale)", "status": "pass",
                            "code": "32(a)", "detail": "Typed pages use a permitted font.",
                            "pages": ""})
    return signals


def split_pdf(data: bytes, chunk_pages: int, max_chunk_bytes: int = 24 * 1024 * 1024):
    """Yield (first_page, last_page, pdf_bytes) chunks, 1-indexed and inclusive.

    A chunk that is still too large for one request (heavy scans) is halved until it fits.
    """
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)

    def build(first: int, last: int) -> bytes:
        writer = PdfWriter()
        for idx in range(first - 1, last):
            writer.add_page(reader.pages[idx])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def emit(first: int, last: int):
        blob = build(first, last)
        if len(blob) > max_chunk_bytes and last > first:
            mid = (first + last) // 2
            yield from emit(first, mid)
            yield from emit(mid + 1, last)
        else:
            yield first, last, blob

    for start in range(1, total + 1, chunk_pages):
        yield from emit(start, min(start + chunk_pages - 1, total))
