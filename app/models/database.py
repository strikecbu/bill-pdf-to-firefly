from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel, create_engine, Session, Relationship

from app.config import get_settings


class StatementStatus(str, PyEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    PARSED = "parsed"
    IMPORTING = "importing"
    COMPLETED = "completed"
    ERROR = "error"


class ImportStatus(str, PyEnum):
    PENDING = "pending"
    IMPORTED = "imported"
    FAILED = "failed"
    SKIPPED = "skipped"


class Statement(SQLModel, table=True):
    __tablename__ = "statements"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    bank_code: str
    bank_name: str
    statement_date: Optional[str] = None
    email_id: Optional[str] = None
    email_subject: Optional[str] = None
    pdf_filename: Optional[str] = None
    status: str = StatementStatus.RECEIVED
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    transactions: list["Transaction"] = Relationship(back_populates="statement")


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    statement_id: str = Field(foreign_key="statements.id")
    transaction_date: Optional[str] = None
    posting_date: Optional[str] = None
    description: str = ""
    amount: float = 0.0
    currency: str = "TWD"
    card_last_four: Optional[str] = None
    transaction_type: str = "withdrawal"  # withdrawal / deposit / transfer
    source_account: Optional[str] = None
    destination_account: Optional[str] = None
    category: Optional[str] = None
    firefly_id: Optional[int] = None
    import_status: str = ImportStatus.PENDING
    external_id: Optional[str] = None
    raw_data: Optional[str] = None  # JSON string
    notes: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    statement: Optional[Statement] = Relationship(back_populates="transactions")


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = f"sqlite:///{settings.app.db_path}"
        _engine = create_engine(db_url, echo=False)
        SQLModel.metadata.create_all(_engine)
    return _engine


def get_session():
    engine = get_engine()
    with Session(engine) as session:
        yield session
