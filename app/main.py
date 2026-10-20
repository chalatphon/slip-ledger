"""FastAPI app: the API router + the static web UI.

Run:  uv run uvicorn app.main:app --reload --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers.api import router

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Slip Ledger")
app.include_router(router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/web", StaticFiles(directory=WEB), name="web")
