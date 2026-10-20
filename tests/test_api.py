"""End-to-end tests: the real FastAPI app in-process (httpx), with the model STUBBED —
no server, no key, deterministic."""
import json

import httpx
import pytest

from app import config, llm, main

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # enough of a PNG header to pass the upload check
SLIP_JSON = json.dumps({
    "date": "18 ต.ค. 69", "time": "16:22", "amount": "59.00", "fee": "0",
    "sender_name": "นาย ชลัฐพล", "receiver_name": "บริษัท ไมเนอร์ ดีคิว จำกัด",
    "ref_no": "REF123", "category": "food",
}, ensure_ascii=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setattr(config, "OWNER_NAMES", ["ชลัฐพล"])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def stub_replies(monkeypatch, replies: list[str]):
    it = iter(replies)

    async def fake_complete(messages, **kw):
        return next(it)

    monkeypatch.setattr(llm, "complete", fake_complete)


async def test_extract_returns_validated_draft(client, monkeypatch):
    stub_replies(monkeypatch, ["```json\n" + SLIP_JSON + "\n```"])
    r = await client.post("/api/extract", files={"image": ("s.png", PNG, "image/png")})
    assert r.status_code == 200
    draft = r.json()["draft"]
    assert draft["date"] == "2026-10-18" and draft["amount"] == "59.00"
    assert draft["direction"] == "expense"


async def test_extract_repairs_non_json_once(client, monkeypatch):
    stub_replies(monkeypatch, ["Sure! The slip shows 59 baht.", SLIP_JSON])
    r = await client.post("/api/extract", files={"image": ("s.png", PNG, "image/png")})
    assert r.json()["draft"]["ref_no"] == "REF123"


async def test_extract_rejects_non_image(client):
    r = await client.post("/api/extract", files={"image": ("a.txt", b"hello", "image/png")})
    assert r.status_code == 422


async def test_save_list_and_duplicate(client):
    tx = {"date": "2026-10-18", "amount": "59.00", "counterparty": "DQ", "direction": "expense",
          "category": "food", "ref_no": "REF123"}
    assert (await client.post("/api/transactions", json=tx)).status_code == 201
    assert (await client.post("/api/transactions", json=tx)).status_code == 409
    rows = (await client.get("/api/transactions")).json()
    assert len(rows) == 1 and rows[0]["counterparty"] == "DQ"


async def test_save_rejects_invalid(client):
    r = await client.post("/api/transactions", json={"date": "2026-10-18", "amount": "-5", "counterparty": "x"})
    assert r.status_code == 422


async def test_ask_runs_agent_loop(client, monkeypatch):
    await client.post("/api/transactions", json={
        "date": "2026-10-18", "amount": "59", "counterparty": "DQ", "direction": "expense", "category": "food"})
    stub_replies(monkeypatch, [
        "ACTION: sql(\"SELECT SUM(amount) FROM transactions WHERE category = 'food'\")",
        "ANSWER: 59 บาท",
    ])
    r = await client.post("/api/ask", json={"question": "ค่ากินเท่าไหร่"})
    body = r.json()
    assert body["answer"] == "59 บาท" and body["stopped_by"] == "answer"
    assert body["steps"][0]["observation"].endswith("59.0")


async def test_agent_stops_at_max_steps(client, monkeypatch):
    stub_replies(monkeypatch, ['ACTION: sql("SELECT 1")'] * 5 + ["ANSWER: forced"])
    r = await client.post("/api/ask", json={"question": "loop forever"})
    assert r.json() == {**r.json(), "answer": "forced", "stopped_by": "max_steps"}
