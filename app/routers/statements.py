from typing import List

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlmodel import Session, select

from app.models.database import get_session, Statement, Transaction
from app.models.statement import StatementSchema, TransactionSchema, ImportReport
from app.services.import_service import process_pdf_file

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["statements"])


@router.get("/statements", response_model=List[StatementSchema])
def list_statements(session: Session = Depends(get_session)):
    """取得對帳單列表"""
    stmts = session.exec(select(Statement).order_by(Statement.created_at.desc())).all()
    return stmts


@router.get("/statements/{statement_id}", response_model=StatementSchema)
def get_statement(statement_id: str, session: Session = Depends(get_session)):
    """取得單張對帳單詳情"""
    stmt = session.get(Statement, statement_id)
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    return stmt


@router.get("/statements/{statement_id}/transactions", response_model=List[TransactionSchema])
def get_statement_transactions(statement_id: str, session: Session = Depends(get_session)):
    """取得單張對帳單的交易明細"""
    txns = session.exec(
        select(Transaction).where(Transaction.statement_id == statement_id)
    ).all()
    return txns


@router.put("/transactions/{transaction_id}", response_model=TransactionSchema)
def update_transaction(
    transaction_id: str,
    data: TransactionSchema,
    session: Session = Depends(get_session),
):
    """修改交易資料"""
    txn = session.get(Transaction, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    update_data = data.model_dump(exclude_unset=True, exclude={"id", "statement_id", "created_at"})
    for key, value in update_data.items():
        setattr(txn, key, value)
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


@router.post("/transactions/{transaction_id}/import")
def import_single_transaction(
    transaction_id: str, session: Session = Depends(get_session)
):
    """手動觸發單筆交易匯入"""
    from app.services.firefly_service import FireflyService
    from app.config import get_settings

    txn = session.get(Transaction, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    settings = get_settings()
    firefly = FireflyService(settings.firefly)
    result = firefly.create_transaction(txn)
    if result:
        txn.firefly_id = result
        txn.import_status = "imported"
    else:
        txn.import_status = "failed"
    session.add(txn)
    session.commit()
    return {"status": txn.import_status, "firefly_id": txn.firefly_id}


@router.post("/upload", response_model=StatementSchema)
async def upload_statement(
    file: UploadFile = File(...),
    bank_code: str = "sinopac",
    session: Session = Depends(get_session),
):
    """手動上傳 PDF 對帳單進行解析"""
    result = await process_pdf_file(file, bank_code, session)
    return result
