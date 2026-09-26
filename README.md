# PHHC Objection Agent

An interactive dashboard that reads a paper book before it is filed in the **Punjab and Haryana High Court** and predicts the objections the Registry is likely to raise. For each objection it gives the evidence, how to fix it and, where useful, ready-to-paste text. It reviews against the Registry's *New Objections Check Lists* (as on 27.05.2024) for **Civil**, **Criminal** and **Civil Writ** cases.

## How a review works

```
PDF upload ──► 1. Preflight (pypdf, no model)
               page size (legal 8.5x14), fonts (Times New Roman / Thorndale), scanned pages
          ──► 2. Reader: Haiku 4.5 (default) or Sonnet 5
               reads the PDF in 20-page chunks, in parallel, with vision:
               per page it records the document type, page marking, signatures, stamps,
               handwritten corrections, legibility, language, and verbatim key facts
          ──► 3. Reasoner: Opus 5.5, effort "medium", adaptive thinking
               checks the digest against the right checklist and the office's house notes
               and returns structured findings: code, severity, confidence, evidence,
               pages, fix, draft text, plus a readiness score
          ──► 4. Dashboard: triage (fixed / dismissed / notes), chat with Opus about the
               filing, save lessons as house notes, print the report
```

* **Checklist selection:** choose Civil, Criminal or Writ at upload, or leave it on *Detect automatically*. In that case the reader's majority vote picks the list.
* **Reader choice:** Haiku is fast and cheap for typed paper books. Pick Sonnet for scanned or handwritten files, and for vernacular annexures. You can re-run any filing with the other reader, and every review is kept.
* **Criminal law after 1 July 2024:** the checklist cites Cr.P.C. sections. The reasoner treats the BNSS equivalents as the same requirement (438→482, 439→483, 482→528, 389→430, 397→438, 378→419, 372→413, 125→144).
* **House notes:** add lessons from objection memos you actually receive, either globally or against a specific code. You can also click *Save as house note* on any finding. Every later review reads them.
* **Where the models run:** by default through the Claude Code CLI on the office Mac, using its Claude subscription login (see below).
* **Chat:** Opus answers follow-up questions from the same digest, checklist and findings. It also sees which findings the office has marked fixed or dismissed.

## Running it on a Mac with your Claude subscription

The app does not need an API key. Every model call goes through the **Claude Code CLI** installed on the Mac (`claude -p`), which runs on the Claude Pro/Max subscription it is logged in with. Any `ANTHROPIC_API_KEY` in the environment is deliberately hidden from the CLI, so usage always goes to the subscription.

One-time setup:

```bash
# 1. Claude Code: install it, then run it once and sign in with the office's Claude account
curl -fsSL https://claude.ai/install.sh | bash     # or: brew install --cask claude-code
claude                                             # choose "Claude account with subscription", then /exit
claude auth status                                 # should show "loggedIn": true

# 2. Python 3.10+ (macOS ships an older one)
brew install python@3.12

# 3. The app
git clone https://github.com/26wkpkjwvz-netizen/PHHC-Objection-Agent.git
cd PHHC-Objection-Agent
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                               # optional: only for overrides
```

Every day:

```bash
cd PHHC-Objection-Agent && source .venv/bin/activate && ./run.sh
# open http://127.0.0.1:8000
```

If Claude Code is missing or logged out, the dashboard shows a red banner telling you what to do.

**Usage and limits.** Reviews count against the subscription's Claude Code usage limits. Each review makes one reader call per 20 pages plus one Opus call; each chat question is one more Opus call. A 200-page paper book uses about 11 calls. The review page shows the equivalent API cost for reference only. Your plan needs Opus access in Claude Code. If it doesn't have it, set `PHHC_REASONER_MODEL=claude-sonnet-5` in `.env`. The subscription belongs to the office, so run the app for the office's own use only; don't offer it to outside users on your login.

**Using an API key instead** (for example on a server): set `PHHC_BACKEND=api` and `ANTHROPIC_API_KEY` in `.env`.

The SQLite database and uploaded PDFs are kept in `./data/` (change this with `PHHC_DATA_DIR`). The checklists are seeded into SQLite on every start.

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `PHHC_BACKEND` | `claude_cli` | `claude_cli` (subscription via Claude Code) or `api` |
| `PHHC_CLAUDE_BIN` | `claude` | Full path to the CLI if it is not on PATH (e.g. `~/.local/bin/claude`) |
| `PHHC_CLI_TIMEOUT_S` | `1200` | Maximum seconds for one CLI call |
| `PHHC_REASONER_MODEL` | `claude-opus-5-5` | Reasoning / chat model |
| `PHHC_REASONER_EFFORT` | `medium` | `low` … `max` |
| `PHHC_READER_HAIKU` / `PHHC_READER_SONNET` | `claude-haiku-4-5` / `claude-sonnet-5` | Reader models |
| `PHHC_DEFAULT_READER` | `haiku` | Pre-selected reader |
| `PHHC_READER_CHUNK_PAGES` | `20` | Pages per reader call |
| `PHHC_READER_CONCURRENCY` | `2` (CLI) / `4` (API) | Parallel reader calls |
| `PHHC_MAX_UPLOAD_MB` | `80` | Upload limit |
| `ANTHROPIC_API_KEY` | – | Only for `PHHC_BACKEND=api` |

## Project layout

```
app/
  checklists/{civil,criminal,writ}.json   Registry checklists (source of truth, edit here)
  config.py      backend, models, effort, paths
  llm.py         model calls: Claude Code CLI (subscription) or Anthropic API
  db.py          SQLite schema + seeding
  pdf_tools.py   preflight checks, chunking
  schemas.py     structured-output schemas (reader page notes, review findings)
  reader.py      stage 2: Haiku/Sonnet page reader
  reasoner.py    stage 3: Opus review + chat prompts
  pipeline.py    background review orchestration
  main.py        FastAPI routes + static front end
static/
  theme.css      design tokens (swap for the house dashboard look)
  app.css, app.js, index.html
tests/           pytest suite; Claude calls are mocked
```

### Database tables

`checklist_items`, `house_notes`, `filings`, `reviews` (status, preflight, reader digest, summary, readiness, token usage), `findings` (code, severity, confidence, evidence, pages, fix, draft text, office status/note), `chat_messages`.

### Editing the checklists

Edit the JSON in `app/checklists/` and restart the server. Each item has:
* `code`: the Registry serial.
* `cis`: writ only, the CIS code printed alongside the serial.
* `group`: used for dashboard filters.
* `text`: the objection itself.
* `when`: the applicability condition. The reasoner uses it to skip items that don't apply to the case type.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Known limits

* Only PDF uploads are accepted. Password-protected PDFs are rejected.
* Font **size**, line spacing and margins (code 32(a)) aren't measured by machine. Only the page size and font family are checked; the reader comments on layout visually.
* Findings are predictions. Anything marked *check* could not be verified from the file and needs a human look.
