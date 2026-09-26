"""FastAPI app: REST API for the dashboard plus the static front end."""
import io
import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

from . import config, db, pipeline, reasoner

STATIC_DIR = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    with db.session() as conn:  # a restart kills in-flight review threads
        conn.execute("""UPDATE reviews SET status = 'error', error = 'Server restarted during review; run it again.'
                        WHERE status IN ('queued', 'reading', 'reasoning')""")
    yield


app = FastAPI(title="PHHC Objection Agent", lifespan=lifespan)


# ------------------------------------------------------------------ helpers

def _review_or_404(conn, review_id: int) -> dict:
    rv = db.row(conn, "SELECT * FROM reviews WHERE id = ?", (review_id,))
    if not rv:
        raise HTTPException(404, "Review not found")
    return rv


def _filing_or_404(conn, filing_id: int) -> dict:
    f = db.row(conn, "SELECT * FROM filings WHERE id = ?", (filing_id,))
    if not f:
        raise HTTPException(404, "Filing not found")
    return f


def _reader_model(key: str) -> str:
    return config.READER_MODELS.get(key, config.READER_MODELS[config.DEFAULT_READER])


def _new_review(conn, filing_id: int, reader_key: str) -> int:
    cur = conn.execute(
        """INSERT INTO reviews (filing_id, status, progress, reader_model, reasoner_model, created_at)
           VALUES (?, 'queued', 'Queued', ?, ?, ?)""",
        (filing_id, _reader_model(reader_key), config.REASONER_MODEL, db.now()),
    )
    return cur.lastrowid


# ------------------------------------------------------------------ config & checklists

@app.get("/api/config")
def get_config():
    return {
        "categories": list(config.CATEGORIES),
        "readers": config.READER_MODELS,
        "default_reader": config.DEFAULT_READER,
        "reasoner": {"model": config.REASONER_MODEL, "effort": config.REASONER_EFFORT},
        "groups": reasoner.GROUP_LABELS,
    }


@app.get("/api/checklists/{category}")
def get_checklist(category: str):
    if category not in config.CATEGORIES:
        raise HTTPException(404, "Unknown category")
    with db.session() as conn:
        return db.checklist(conn, category)


# ------------------------------------------------------------------ house notes

class HouseNoteIn(BaseModel):
    category: str
    code: str = ""
    note: str


@app.get("/api/house-notes")
def list_house_notes():
    with db.session() as conn:
        return db.rows(conn, "SELECT * FROM house_notes ORDER BY category, code, id")


@app.post("/api/house-notes")
def add_house_note(body: HouseNoteIn):
    if body.category not in (*config.CATEGORIES, "all") or not body.note.strip():
        raise HTTPException(400, "category must be civil/criminal/writ/all and note non-empty")
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO house_notes (category, code, note, created_at) VALUES (?, ?, ?, ?)",
            (body.category, body.code.strip(), body.note.strip(), db.now()))
        return db.row(conn, "SELECT * FROM house_notes WHERE id = ?", (cur.lastrowid,))


@app.delete("/api/house-notes/{note_id}")
def delete_house_note(note_id: int):
    with db.session() as conn:
        conn.execute("DELETE FROM house_notes WHERE id = ?", (note_id,))
    return {"ok": True}


# ------------------------------------------------------------------ filings

@app.post("/api/filings")
async def upload_filing(
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("auto"),
    case_type_hint: str = Form(""),
    reader: str = Form(config.DEFAULT_READER),
):
    if category not in (*config.CATEGORIES, "auto"):
        raise HTTPException(400, "category must be civil, criminal, writ or auto")
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Upload the paper book as a PDF")
    try:
        reader_pdf = PdfReader(io.BytesIO(data))
        if reader_pdf.is_encrypted:
            raise HTTPException(400, "The PDF is password protected; upload an unlocked copy")
        pages = len(reader_pdf.pages)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Could not open the PDF")

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "filing.pdf")[-80:]
    path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe}"
    path.write_bytes(data)

    with db.session() as conn:
        cur = conn.execute(
            """INSERT INTO filings (title, category, case_type_hint, filename, stored_path,
                   pages, size_bytes, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title.strip() or file.filename or "Untitled filing", category, case_type_hint.strip(),
             file.filename or safe, str(path), pages, len(data), db.now()))
        filing_id = cur.lastrowid
        review_id = _new_review(conn, filing_id, reader)
    pipeline.start_review(review_id)
    return {"filing_id": filing_id, "review_id": review_id}


@app.get("/api/filings")
def list_filings():
    with db.session() as conn:
        return db.rows(conn, """
            SELECT f.*, r.id AS review_id, r.status, r.progress, r.readiness, r.category AS review_category,
                   r.case_type,
                   (SELECT COUNT(*) FROM findings x WHERE x.review_id = r.id AND x.status = 'open') AS open_findings,
                   (SELECT COUNT(*) FROM findings x WHERE x.review_id = r.id AND x.status = 'open'
                        AND x.severity = 'high') AS open_high
            FROM filings f
            LEFT JOIN reviews r ON r.id = (SELECT MAX(id) FROM reviews WHERE filing_id = f.id)
            ORDER BY f.id DESC""")


@app.get("/api/filings/{filing_id}")
def get_filing(filing_id: int):
    with db.session() as conn:
        f = _filing_or_404(conn, filing_id)
        f["reviews"] = db.rows(conn, """SELECT id, status, readiness, reader_model, created_at, finished_at
                                        FROM reviews WHERE filing_id = ? ORDER BY id DESC""", (filing_id,))
        f.pop("stored_path", None)
        return f


@app.get("/api/filings/{filing_id}/pdf")
def get_filing_pdf(filing_id: int):
    with db.session() as conn:
        f = _filing_or_404(conn, filing_id)
    return FileResponse(f["stored_path"], media_type="application/pdf", filename=f["filename"],
                        content_disposition_type="inline")


@app.delete("/api/filings/{filing_id}")
def delete_filing(filing_id: int):
    with db.session() as conn:
        f = _filing_or_404(conn, filing_id)
        conn.execute("DELETE FROM filings WHERE id = ?", (filing_id,))
    try:
        Path(f["stored_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


class RerunIn(BaseModel):
    reader: str = config.DEFAULT_READER


@app.post("/api/filings/{filing_id}/reviews")
def rerun_review(filing_id: int, body: RerunIn):
    with db.session() as conn:
        _filing_or_404(conn, filing_id)
        busy = db.row(conn, """SELECT id FROM reviews WHERE filing_id = ?
                               AND status IN ('queued', 'reading', 'reasoning')""", (filing_id,))
        if busy:
            raise HTTPException(409, "A review of this filing is already running")
        review_id = _new_review(conn, filing_id, body.reader)
    pipeline.start_review(review_id)
    return {"review_id": review_id}


# ------------------------------------------------------------------ reviews & findings

@app.get("/api/reviews/{review_id}")
def get_review(review_id: int):
    with db.session() as conn:
        rv = _review_or_404(conn, review_id)
        filing = _filing_or_404(conn, rv["filing_id"])
        filing.pop("stored_path", None)
        digest = json.loads(rv.pop("digest_json") or "{}")
        rv["preflight"] = json.loads(rv.pop("preflight_json") or "[]")
        rv["strengths"] = json.loads(rv.pop("strengths_json") or "[]")
        rv["usage"] = json.loads(rv.pop("usage_json") or "{}")
        rv["page_map"] = [
            {k: p[k] for k in ("page", "doc_type", "document_title", "printed_page_number",
                               "legibility", "concerns")}
            for p in digest.get("pages", [])
        ]
        rv["filing"] = filing
        rv["findings"] = db.rows(conn, "SELECT * FROM findings WHERE review_id = ? ORDER BY sort_order",
                                 (review_id,))
        rv["chat"] = db.rows(conn, "SELECT * FROM chat_messages WHERE review_id = ? ORDER BY id",
                             (review_id,))
        return rv


class FindingPatch(BaseModel):
    status: str | None = None
    user_note: str | None = None


@app.patch("/api/findings/{finding_id}")
def update_finding(finding_id: int, body: FindingPatch):
    if body.status is not None and body.status not in ("open", "fixed", "dismissed"):
        raise HTTPException(400, "status must be open, fixed or dismissed")
    with db.session() as conn:
        f = db.row(conn, "SELECT * FROM findings WHERE id = ?", (finding_id,))
        if not f:
            raise HTTPException(404, "Finding not found")
        conn.execute("UPDATE findings SET status = ?, user_note = ? WHERE id = ?",
                     (body.status if body.status is not None else f["status"],
                      body.user_note if body.user_note is not None else f["user_note"], finding_id))
        return db.row(conn, "SELECT * FROM findings WHERE id = ?", (finding_id,))


class ChatIn(BaseModel):
    message: str


@app.post("/api/reviews/{review_id}/chat")
def chat(review_id: int, body: ChatIn):
    question = body.message.strip()
    if not question:
        raise HTTPException(400, "Empty message")
    with db.session() as conn:
        rv = _review_or_404(conn, review_id)
        if rv["status"] != "done":
            raise HTTPException(409, "The review has not finished yet")
        filing = _filing_or_404(conn, rv["filing_id"])
        system, digest = pipeline.load_context(conn, rv, filing)
        findings = db.rows(conn, "SELECT * FROM findings WHERE review_id = ? ORDER BY sort_order",
                           (review_id,))
        history = db.rows(conn, "SELECT role, content FROM chat_messages WHERE review_id = ? ORDER BY id",
                          (review_id,))

    delivered = json.dumps([{k: f[k] for k in ("id", "code", "title", "severity", "confidence",
                                               "evidence", "pages", "fix")} for f in findings],
                           ensure_ascii=False)
    changed = [f"#{f['id']} [{f['code']}] {f['status']}" + (f" - note: {f['user_note']}" if f["user_note"] else "")
               for f in findings if f["status"] != "open" or f["user_note"]]
    status_note = ("Current status of findings as marked by the office: " + "; ".join(changed)) if changed else ""

    answer, _usage = reasoner.chat(pipeline.client(), system, digest, delivered, history,
                                   status_note, question)
    with db.session() as conn:
        conn.execute("INSERT INTO chat_messages (review_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                     (review_id, question, db.now()))
        conn.execute("INSERT INTO chat_messages (review_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                     (review_id, answer, db.now()))
    return {"answer": answer}


# ------------------------------------------------------------------ front end

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
