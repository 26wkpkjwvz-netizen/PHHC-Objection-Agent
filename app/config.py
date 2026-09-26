"""Runtime configuration. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PHHC_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("PHHC_DB_PATH", DATA_DIR / "objections.db"))
CHECKLIST_DIR = BASE_DIR / "app" / "checklists"

# How models are called:
#   claude_cli (default) - the local Claude Code CLI, running on its logged-in Claude subscription
#   api                  - the Anthropic API with ANTHROPIC_API_KEY
BACKEND = os.getenv("PHHC_BACKEND", "claude_cli")
CLAUDE_BIN = os.getenv("PHHC_CLAUDE_BIN", "claude")
CLI_TIMEOUT_S = int(os.getenv("PHHC_CLI_TIMEOUT_S", "1200"))

# Reader: reads the paper book page by page (vision + text). Haiku is the cheap
# default; switch a review to Sonnet from the dashboard for scanned or messy files.
READER_MODELS = {
    "haiku": os.getenv("PHHC_READER_HAIKU", "claude-haiku-4-5"),
    "sonnet": os.getenv("PHHC_READER_SONNET", "claude-sonnet-5"),
}
DEFAULT_READER = os.getenv("PHHC_DEFAULT_READER", "haiku")

# Reasoner: decides which Registry objections are likely and drafts the fixes.
REASONER_MODEL = os.getenv("PHHC_REASONER_MODEL", "claude-opus-5-5")
REASONER_EFFORT = os.getenv("PHHC_REASONER_EFFORT", "medium")

# Pages sent to the reader per request. Haiku 4.5 accepts at most 100 PDF pages
# per request; smaller chunks keep per-page notes detailed.
READER_CHUNK_PAGES = int(os.getenv("PHHC_READER_CHUNK_PAGES", "20"))
# Parallel reader calls. Kept low by default so a subscription's rate limits are not hit.
READER_CONCURRENCY = int(os.getenv("PHHC_READER_CONCURRENCY", "2" if BACKEND != "api" else "4"))

MAX_UPLOAD_MB = int(os.getenv("PHHC_MAX_UPLOAD_MB", "80"))

CATEGORIES = ("civil", "criminal", "writ")
