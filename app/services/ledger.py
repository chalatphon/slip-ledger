"""Service layer: the app's use-cases. Routers call these; these call the extractor,
the repository and the agent. No HTTP here, so each use-case is testable as a plain
function call."""
import sqlite3

from app import config
from app.agent import loop
from app.repositories import transactions as repo
from app.schemas import AskResponse, ExtractResult, StoredTransaction, Transaction
from app.services import extractor


class UploadError(ValueError):
    pass


def check_upload(data: bytes, content_type: str | None) -> str:
    """Validate an upload before spending a model call on it. Returns the MIME type."""
    if not data:
        raise UploadError("No file uploaded.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError("File too large (max ~4 MB).")
    # Check the real file signature, not just the client-supplied content type.
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise UploadError("Please upload a PNG, JPEG or WebP image.")


async def extract_draft(data: bytes, content_type: str | None) -> ExtractResult:
    """Read a slip into a draft. Nothing is saved — the user confirms first."""
    mime = check_upload(data, content_type)
    return await extractor.extract(data, mime)


def save(db: sqlite3.Connection, tx: Transaction) -> StoredTransaction:
    tx_id = repo.add(db, tx)  # raises repo.DuplicateError for a slip already saved
    return StoredTransaction(id=tx_id, **tx.model_dump())


def recent(db: sqlite3.Connection, limit: int = 100) -> list[StoredTransaction]:
    return repo.list_recent(db, limit)


def remove(db: sqlite3.Connection, tx_id: int) -> bool:
    return repo.delete(db, tx_id)


async def ask(question: str) -> AskResponse:
    return await loop.run(question, db_path=config.LEDGER_DB)
