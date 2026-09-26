"""Stage 2: Opus 5.5 (medium effort) reasons over the digest against the Registry checklist."""
from . import config, llm
from .schemas import ReviewResult

# Document types whose full text the reasoner needs; annexures are summarised by the reader.
PLEADING_TYPES = {
    "opening_sheet", "urgent_form", "index", "court_fee", "synopsis", "list_of_dates",
    "memo_of_parties", "petition", "application", "grounds", "prayer", "affidavit",
    "vakalatnama", "power_of_attorney", "certificate",
}
MAX_TEXT_PER_PAGE = 6000

GROUP_LABELS = {
    "competency_maintainability": "Competency, maintainability and nomenclature",
    "affidavit": "Affidavits",
    "court_fee": "Court fee and stamps",
    "memo_of_parties": "Memo of parties",
    "identity_proof": "Identity proof",
    "vakalatnama_poa": "Vakalatnama / power of attorney",
    "index_notes": "Index, notes below index, synopsis",
    "paper_book_format": "Paper book arrangement and format",
    "annexures_documents": "Annexures and documents",
    "pleadings_content": "Pleadings: headnote, prayer, grounds, paras",
    "limitation_delay": "Limitation and delay",
    "service_advance_copy": "Advance copies and service",
    "case_specific": "Case-type specific requirements",
    "other": "Other",
}

REASONER_RULES = """You are a senior scrutiny clerk of the Filing Branch of the Punjab and Haryana High Court at Chandigarh, advising the advocate's office before they file. Your job: predict which objections the Registry will raise on this paper book under the official checklist below, and tell the office exactly how to cure each one before filing.

How to work
- You receive (1) machine preflight checks, (2) page-by-page notes taken by a reader model, including verbatim text of the pleading pages, and (3) the advocate's own description of the filing. Treat the reader notes as your eyes on the file; they can miss things, so where the notes are silent on something the checklist requires, say "check" rather than asserting a defect.
- Decide first what the filing is (category, nomenclature, provision, who files it) and which checklist items apply to that case type. Items with an "applies when" condition apply only if the condition holds.
- Cross-check consistency the way the Registry does: title vs memo of parties vs index vs headnote vs prayer vs impugned order; FIR/complaint particulars everywhere; serial numbers of parties across MOP, POA and petition; annexure labels in the text vs annexures actually filed vs index page numbers; dates of impugned orders; signatures on every document that needs one.
- Only raise an objection you can tie to evidence or to a required item that is plainly absent. Do not pad the list. Merge duplicates. Use code 99 for a genuine defect not covered by any code.
- For each finding give a practical fix, and where it helps, draft the exact text to insert (a note below index, an averment para, a certificate, a corrected headnote).
- Criminal law since 1 July 2024: the checklist cites Cr.P.C. sections. Treat BNSS equivalents as the same requirement (s.438 CrPC = s.482 BNSS, s.439 = s.483, s.482 = s.528, s.389 = s.430, s.397 = s.438, s.378 = s.419, s.372 = s.413, s.125 = s.144) and flag a wrong provision under code 2 where the wrong regime is cited for the date of the FIR/proceedings.
- House notes from this office override your defaults where they conflict with your judgement on style, but never excuse a real checklist defect.
- readiness: 90+ means file as is; 70-89 minor objections likely; 40-69 several objections, will probably be returned; below 40 incomplete."""


def _checklist_block(category: str, items: list[dict]) -> str:
    lines = [f"OFFICIAL CHECKLIST - {category.upper()} (Registry list as on 27.05.2024)"]
    current = None
    for it in items:
        if it["grp"] != current:
            current = it["grp"]
            lines.append(f"\n## {GROUP_LABELS.get(current, current)}")
        cis = f" [CIS {it['cis_code']}]" if it.get("cis_code") else ""
        when = f"  (applies when: {it['applies_when']})" if it.get("applies_when") else ""
        lines.append(f"- {it['code']}{cis}: {it['text']}{when}")
    return "\n".join(lines)


def _notes_block(notes: list[dict]) -> str:
    if not notes:
        return "HOUSE NOTES: none recorded yet."
    lines = ["HOUSE NOTES (guidance recorded by this office from past Registry objections):"]
    for n in notes:
        code = f"[{n['code']}] " if n["code"] else ""
        lines.append(f"- {code}{n['note']}")
    return "\n".join(lines)


def system_blocks(category: str, items: list[dict], notes: list[dict]) -> list[dict]:
    """Stable prefix: rules + checklist are cached; house notes change rarely."""
    return [
        {"type": "text", "text": REASONER_RULES},
        {"type": "text", "text": _checklist_block(category, items)},
        {"type": "text", "text": _notes_block(notes), "cache_control": {"type": "ephemeral"}},
    ]


def digest_text(filing: dict, preflight: list[dict], digest: dict, page_texts: dict[int, str]) -> str:
    parts = [
        "FILING AS DESCRIBED BY THE ADVOCATE",
        f"Title: {filing['title']}",
        f"Category selected: {filing['category']}",
        f"Case type / provision: {filing['case_type_hint'] or 'not given'}",
        f"File: {filing['filename']} ({filing['pages']} pages)",
        f"Reader's guess: {digest.get('category_guess')} / {digest.get('case_type_guess') or '-'}",
        "",
        "PREFLIGHT (machine checks on the PDF)",
    ]
    for s in preflight:
        pages = f" pages {s['pages']}" if s.get("pages") else ""
        parts.append(f"- [{s['status'].upper()}] {s['check']} (code {s['code']}): {s['detail']}{pages}")

    parts += ["", "PAGE-BY-PAGE NOTES"]
    for p in digest["pages"]:
        header = (f"--- p.{p['page']} | {p['doc_type']} | {p['document_title']} | "
                  f"marked '{p['printed_page_number'] or '-'}' ({p['page_number_position']}) | "
                  f"{p['language']}, {p['production']}, {p['legibility']}")
        parts.append(header)
        if p["signatures"]:
            parts.append(f"signatures: {', '.join(p['signatures'])}")
        if p["stamps_and_seals"]:
            parts.append(f"stamps/seals: {', '.join(p['stamps_and_seals'])}")
        if p["handwritten_corrections"]:
            parts.append("HANDWRITTEN CORRECTIONS PRESENT")
        parts.append(f"notes: {p['key_content']}")
        if p["concerns"]:
            parts.append(f"reader concerns: {'; '.join(p['concerns'])}")
        text = page_texts.get(p["page"], "")
        if p["doc_type"] in PLEADING_TYPES and text:
            parts.append(f"text layer:\n{text[:MAX_TEXT_PER_PAGE]}")
    return "\n".join(parts)


def review(system: list[dict], digest: str) -> tuple[ReviewResult, dict]:
    try:
        return llm.reason(
            model=config.REASONER_MODEL, effort=config.REASONER_EFFORT, system_blocks=system,
            user_blocks=[{"type": "text", "text": digest},
                         {"type": "text", "text": "Scrutinise this paper book against the checklist and return the review."}],
            output=ReviewResult)
    except llm.LLMError as exc:
        raise RuntimeError(f"Review failed: {exc}") from exc


CHAT_RULES = """You are now in a follow-up conversation with the advocate's office about this review. Answer from the file notes, the checklist and your findings. Be concrete: cite codes and page numbers, draft text when asked, and say plainly when something cannot be verified from the file. If the office disputes a finding and is right, say so. Reply in plain prose with short lists where useful; no JSON."""


def chat(system: list[dict], digest: str, findings_json: str, history: list[dict],
         status_note: str, question: str) -> tuple[str, dict]:
    context = [
        {"type": "text", "text": digest},
        {"type": "text", "text": f"YOUR REVIEW FINDINGS (as first delivered)\n{findings_json}",
         "cache_control": {"type": "ephemeral"}},
    ]
    try:
        return llm.converse(model=config.REASONER_MODEL, effort=config.REASONER_EFFORT,
                            system_blocks=system + [{"type": "text", "text": CHAT_RULES}],
                            context_blocks=context, history=history,
                            question=f"{status_note}\n\n{question}".strip())
    except llm.LLMError as exc:
        return f"Could not get an answer: {exc}", {}
