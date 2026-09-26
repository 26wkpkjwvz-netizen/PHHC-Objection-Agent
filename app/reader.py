"""Stage 1: a fast model (Haiku 4.5 / Sonnet 5) reads the paper book page by page."""
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import anthropic

from . import config
from .pdf_tools import split_pdf
from .schemas import ChunkRead

READER_SYSTEM = """You read paper books prepared for filing in the Punjab and Haryana High Court at Chandigarh and take page-by-page scrutiny notes for the Registry's objection checklist.

For every page in the attached PDF chunk, record what the page is, what is physically on it (page number marking and its position, signatures, stamps, seals, handwritten corrections, legibility, language) and the facts a scrutiny clerk would cross-check between documents (party details, FIR/case particulars, dates, index rows, headnote and prayer wording, advocate details).

Be literal. Quote names, numbers and dates exactly as printed. If something is absent, do not invent it; say so in concerns only when its absence on that page is itself notable (e.g. an affidavit page with no deponent signature, an index without a page column, a vernacular annexure without translation nearby). Keep key_content dense: no filler, no legal opinion."""


def _usage_dict(usage) -> dict:
    return {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def read_chunk(client: anthropic.Anthropic, model: str, first: int, last: int, blob: bytes,
               filing_hint: str) -> tuple[ChunkRead, dict]:
    prompt = (
        f"This chunk contains pages {first} to {last} of the uploaded paper book "
        f"(PDF page 1 of this chunk = page {first}). Report one entry per page, numbered "
        f"{first}..{last}.\nFiling details given by the advocate: {filing_hint or 'none'}."
    )
    with client.messages.stream(
        model=model,
        max_tokens=32000,
        system=[{"type": "text", "text": READER_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(blob).decode()}},
                {"type": "text", "text": prompt},
            ],
        }],
        output_format=ChunkRead,
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError(f"Reader declined pages {first}-{last}.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"Reader output for pages {first}-{last} was cut off; lower PHHC_READER_CHUNK_PAGES.")
    parsed = message.parsed_output
    if parsed is None:
        raise RuntimeError(f"Reader returned no structured output for pages {first}-{last}.")
    return parsed, _usage_dict(message.usage)


def read_filing(client: anthropic.Anthropic, pdf_bytes: bytes, reader_key: str,
                filing_hint: str, on_progress=lambda msg: None) -> dict:
    """Read the whole PDF in parallel chunks and merge into one digest."""
    model = config.READER_MODELS.get(reader_key, config.READER_MODELS[config.DEFAULT_READER])
    chunks = list(split_pdf(pdf_bytes, config.READER_CHUNK_PAGES))
    done = 0
    usage = Counter()

    def work(chunk):
        first, last, blob = chunk
        return first, *read_chunk(client, model, first, last, blob, filing_hint)

    results = []
    with ThreadPoolExecutor(max_workers=config.READER_CONCURRENCY) as pool:
        for first, parsed, u in pool.map(work, chunks):
            results.append((first, parsed))
            usage.update(u)
            done += 1
            on_progress(f"Read {done}/{len(chunks)} page chunks")

    results.sort(key=lambda r: r[0])
    pages = [p.model_dump() for _, parsed in results for p in parsed.pages]
    votes = Counter(parsed.case_category_guess for _, parsed in results
                    if parsed.case_category_guess != "unclear")
    type_guesses = [parsed.case_type_guess for _, parsed in results if parsed.case_type_guess]
    return {
        "reader_model": model,
        "category_guess": votes.most_common(1)[0][0] if votes else "unclear",
        "case_type_guess": type_guesses[0] if type_guesses else "",
        "pages": pages,
        "usage": dict(usage),
    }
