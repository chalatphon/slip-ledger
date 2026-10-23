"""All configuration comes from the environment (.env is loaded for local runs).
Nothing secret lives in code — OPENAI_API_KEY / OPENAI_BASE_URL are read by the
OpenAI client itself."""
import os

from dotenv import load_dotenv

load_dotenv()

CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemma-4-E4B-it")
LEDGER_DB = os.environ.get("LEDGER_DB", "data/ledger.db")
OWNER_NAMES = [n.strip() for n in os.environ.get("OWNER_NAMES", "").split(",") if n.strip()]
# Last digits of your own account(s), comma-separated, e.g. "569". Matched before names.
OWNER_ACCOUNTS = [a.strip() for a in os.environ.get("OWNER_ACCOUNTS", "").split(",") if a.strip()]

MAX_UPLOAD_BYTES = 4_000_000
