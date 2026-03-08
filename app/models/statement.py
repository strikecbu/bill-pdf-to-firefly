from typing import List, Optional
from pydantic import BaseModel


class TransactionSchema(BaseModel):
    id: Optional[str] = None
    statement_id: Optional[str] = None
    transaction_date: Optional[str] = None
    posting_date: Optional[str] = None
    description: str = ""
    amount: float = 0.0
    currency: str = "TWD"
    card_last_four: Optional[str] = None
    transaction_type: str = "withdrawal"
    source_account: Optional[str] = None
    destination_account: Optional[str] = None
    category: Optional[str] = None
    firefly_id: Optional[int] = None
    import_status: str = "pending"
    notes: Optional[str] = None


class StatementSchema(BaseModel):
    id: Optional[str] = None
    bank_code: str = ""
    bank_name: str = ""
    statement_date: Optional[str] = None
    email_id: Optional[str] = None
    email_subject: Optional[str] = None
    pdf_filename: Optional[str] = None
    status: str = "received"
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    transactions: List[TransactionSchema] = []


class ImportReport(BaseModel):
    statement_id: str
    total: int = 0
    imported: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[str] = []
