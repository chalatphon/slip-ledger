"""Data shapes that cross a boundary. Anything the model produces is validated here
before it can reach the database."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

CATEGORIES = (
    "food", "transport", "shopping", "bills", "health",
    "education", "entertainment", "transfer", "salary", "other",
)
Category = Literal[
    "food", "transport", "shopping", "bills", "health",
    "education", "entertainment", "transfer", "salary", "other",
]
Direction = Literal["expense", "income", "unknown"]


class Transaction(BaseModel):
    """One validated ledger row. The extractor produces a *draft* of this; the user
    confirms (or edits) it in the UI before it is saved."""
    date: date
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    direction: Direction = "unknown"
    counterparty: str = Field(min_length=1, max_length=200)
    bank: str | None = Field(default=None, max_length=100)
    ref_no: str | None = Field(default=None, max_length=100)
    category: Category = "other"
    memo: str | None = Field(default=None, max_length=300)
    source: Literal["slip", "receipt", "manual"] = "slip"


class StoredTransaction(Transaction):
    id: int


class ExtractResult(BaseModel):
    """What /api/extract returns: a draft if validation passed, plus the raw model
    output so the user (and the eval) can see exactly what was read."""
    draft: Transaction | None
    raw: dict
    errors: list[str] = []


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AgentStep(BaseModel):
    action: str
    observation: str


class AskResponse(BaseModel):
    answer: str
    steps: list[AgentStep]
    stopped_by: Literal["answer", "max_steps"]
