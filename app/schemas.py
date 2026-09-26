"""Structured-output schemas for the reader and reasoner calls."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- reader (Haiku / Sonnet)

class PageNote(_Strict):
    page: int = Field(description="Page number within the uploaded PDF (1-indexed, as instructed).")
    doc_type: str = Field(description=(
        "One of: opening_sheet, urgent_form, index, court_fee, synopsis, list_of_dates, "
        "memo_of_parties, petition, application, grounds, prayer, affidavit, vakalatnama, "
        "power_of_attorney, identity_proof, impugned_order, annexure, vernacular, translation, "
        "certificate, postal_receipt, photograph, other"))
    document_title: str = Field(description="Heading of the document this page belongs to, e.g. 'Annexure P-3: FIR No. 12 dated ...'.")
    printed_page_number: str = Field(description="Page number marked on the page exactly as written; '' if none.")
    page_number_position: Literal["top_right", "top_middle", "top_left", "bottom", "none"]
    language: str = Field(description="English, Hindi, Punjabi, Urdu, mixed, ...")
    production: Literal["typed", "handwritten", "mixed", "photocopy_or_scan"]
    legibility: Literal["clear", "dim", "illegible"]
    signatures: list[str] = Field(description="Whose signatures appear: counsel, party, deponent, oath_commissioner, notary, identifier, court_official, unknown. Empty if none.")
    stamps_and_seals: list[str] = Field(description="E.g. court_fee_stamp, welfare_fund_stamp, oath_commissioner_seal, identification_seal, certified_copy_stamp, postal_receipt.")
    handwritten_corrections: bool
    key_content: str = Field(description=(
        "Dense factual notes needed for Registry scrutiny. Quote verbatim: party names with "
        "parentage/age/address/mobile/ID numbers, FIR/complaint number, date, sections, police "
        "station, district, case numbers of courts below, dates of impugned orders, court fee "
        "amounts, annexure labels referred to, index rows (serial, particulars, date, pages, "
        "court fee), notes below index, headnote and prayer wording, advocate name/enrolment/"
        "contact/email, affidavit verification and read-over certificate wording."))
    concerns: list[str] = Field(description="Anything that looks defective or missing on this page. Empty if none.")


class ChunkRead(_Strict):
    case_category_guess: Literal["civil", "criminal", "writ", "unclear"]
    case_type_guess: str = Field(description="E.g. 'CRM-M anticipatory bail', 'CWP service matter', 'RSA', 'FAO (MACT)'. '' if unclear from these pages.")
    pages: list[PageNote]


# ---------------------------------------------------------------- reasoner (Opus)

class Finding(_Strict):
    code: str = Field(description="Checklist code exactly as listed, e.g. '16(A)(1)', '35(b)', or '99' for anything not listed.")
    title: str = Field(description="One-line objection as the Registry would phrase it.")
    severity: Literal["high", "medium", "low"] = Field(description="high = filing will be returned; medium = routinely objected; low = cosmetic or occasionally raised.")
    confidence: Literal["likely", "possible", "check"] = Field(description="likely = clear evidence of the defect; possible = partial evidence; check = cannot be verified from the file, counsel should confirm.")
    evidence: str = Field(description="What was (or was not) seen, with page references.")
    pages: str = Field(description="Page refs like '3, 7-9', or '' if the problem is an absence.")
    fix: str = Field(description="Concrete, step-by-step cure before filing.")
    draft_text: str = Field(description="Ready-to-paste text (para, note below index, certificate, headnote) when useful, else ''.")


class ReviewResult(_Strict):
    category: Literal["civil", "criminal", "writ"]
    case_type: str
    summary: str = Field(description="3-6 sentence overview of the filing and its scrutiny risk.")
    readiness: int = Field(description="0-100: how ready the paper book is for filing without objections.")
    strengths: list[str] = Field(description="Checklist points that are clearly complied with (short).")
    findings: list[Finding]
