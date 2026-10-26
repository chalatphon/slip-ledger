"""Router: HTTP only — parse the request, call a service, map errors to status codes."""
import sqlite3
from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import config
from app.repositories import transactions as repo
from app.schemas import (AskRequest, AskResponse, ExtractResult, StoredTransaction,
                         TextEntryRequest, Transaction)
from app.services import ledger

router = APIRouter(prefix="/api")


def get_db() -> Iterator[sqlite3.Connection]:
    db = repo.connect(config.LEDGER_DB)
    try:
        yield db
    finally:
        db.close()


@router.post("/extract", response_model=ExtractResult)
async def extract(image: UploadFile = File(...)) -> ExtractResult:
    """Upload a slip/receipt image -> a validated draft (not saved yet)."""
    data = await image.read()
    try:
        return await ledger.extract_draft(data, image.content_type)
    except ledger.UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/extract-text", response_model=ExtractResult)
async def extract_text(req: TextEntryRequest) -> ExtractResult:
    """Type a transaction in words -> a validated draft (not saved yet)."""
    return await ledger.draft_from_text(req.text)


@router.post("/transactions", response_model=StoredTransaction, status_code=201)
def create(tx: Transaction, db: sqlite3.Connection = Depends(get_db)) -> StoredTransaction:
    """Save a confirmed transaction (the draft from /extract, possibly edited)."""
    try:
        return ledger.save(db, tx)
    except repo.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=f"Slip {exc} is already in the ledger.") from exc


@router.get("/transactions", response_model=list[StoredTransaction])
def list_transactions(limit: int = 100, db: sqlite3.Connection = Depends(get_db)):
    return ledger.recent(db, min(max(limit, 1), 500))


@router.delete("/transactions/{tx_id}", status_code=204)
def delete(tx_id: int, db: sqlite3.Connection = Depends(get_db)) -> None:
    if not ledger.remove(db, tx_id):
        raise HTTPException(status_code=404, detail="No such transaction.")


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """Ask a question about your spending; the agent answers by querying the ledger."""
    return await ledger.ask(req.question)


@router.get("/health")
def health() -> dict:
    return {"ok": True}
