import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from fastapi import UploadFile
from sqlmodel import Session

from app.config import get_settings
from app.models.database import (
    Statement,
    Transaction,
    StatementStatus,
    ImportStatus,
    get_engine,
)
from app.parsers import ParserFactory
from app.services.firefly_service import FireflyService
from app.services.gmail_service import GmailService
from app.services.mail_classifier import MailClassifier
from app.services.pdf_service import PdfService

logger = structlog.get_logger()


async def process_notification(history_id: str):
    """Process a Gmail push notification."""
    settings = get_settings()

    try:
        gmail = GmailService()
        classifier = MailClassifier()
        pdf_service = PdfService()

        messages = gmail.get_messages_since(history_id)
        logger.info("processing_notification", message_count=len(messages))

        for msg_info in messages:
            message_id = msg_info["id"]
            message = gmail.get_message(message_id)
            if not message:
                continue

            headers = gmail.get_message_headers(message)
            sender = headers.get("from", "")
            subject = headers.get("subject", "")

            is_statement, bank_code = classifier.classify(sender, subject)
            if not is_statement:
                continue

            attachments = gmail.get_attachments(message)
            if not classifier.has_pdf_attachment(attachments):
                logger.warning("no_pdf_attachment", message_id=message_id)
                continue

            engine = get_engine()
            with Session(engine) as session:
                for att_id, filename in attachments:
                    if not filename.lower().endswith(".pdf"):
                        continue

                    await _process_attachment(
                        gmail, pdf_service, session,
                        message_id, att_id, filename,
                        bank_code, subject, settings,
                    )

    except Exception as e:
        logger.error("notification_processing_error", error=str(e))


async def _process_attachment(
    gmail, pdf_service, session,
    message_id, att_id, filename,
    bank_code, subject, settings,
):
    """Process a single PDF attachment."""
    save_path = os.path.join(settings.app.temp_dir, filename)
    unlocked_path = None

    try:
        # Create statement record
        stmt = Statement(
            bank_code=bank_code,
            bank_name=settings.banks[bank_code].name,
            email_id=message_id,
            email_subject=subject,
            pdf_filename=filename,
            status=StatementStatus.RECEIVED,
        )
        session.add(stmt)
        session.commit()
        session.refresh(stmt)

        # Download PDF
        gmail.download_attachment(message_id, att_id, save_path)

        # Unlock PDF
        stmt.status = StatementStatus.PARSING
        session.add(stmt)
        session.commit()

        unlocked_path = pdf_service.unlock_pdf(save_path, bank_code)
        parse_path = unlocked_path or save_path

        # Parse PDF
        parser = ParserFactory.get_parser(bank_code)
        transactions = parser.parse(parse_path)

        # Save transactions
        for txn_data in transactions:
            txn = Transaction(
                statement_id=stmt.id,
                **txn_data,
            )
            session.add(txn)

        stmt.status = StatementStatus.PARSED
        stmt.updated_at = datetime.now().isoformat()
        session.add(stmt)
        session.commit()

        # Import to Firefly III
        if settings.firefly.base_url and settings.firefly.api_token:
            stmt.status = StatementStatus.IMPORTING
            session.add(stmt)
            session.commit()

            txns = session.query(Transaction).filter(
                Transaction.statement_id == stmt.id
            ).all()

            firefly = FireflyService(settings.firefly)
            report = firefly.batch_create_transactions(txns)

            for txn in txns:
                session.add(txn)

            stmt.status = StatementStatus.COMPLETED
            stmt.updated_at = datetime.now().isoformat()
            session.add(stmt)
            session.commit()

            logger.info("import_completed", report=report)
        else:
            logger.info("firefly_not_configured_skipping_import")

    except Exception as e:
        logger.error("attachment_processing_error", error=str(e), filename=filename)
        stmt.status = StatementStatus.ERROR
        stmt.error_message = str(e)
        session.add(stmt)
        session.commit()
    finally:
        pdf_service.cleanup(save_path, unlocked_path)


async def process_pdf_file(
    file: UploadFile, bank_code: str, session: Session
) -> Statement:
    """Process an uploaded PDF file directly."""
    settings = get_settings()
    pdf_service = PdfService()

    filename = file.filename or "upload.pdf"
    save_path = os.path.join(settings.app.temp_dir, filename)
    unlocked_path = None

    try:
        # Save uploaded file
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        # Create statement record
        bank_config = settings.banks.get(bank_code)
        bank_name = bank_config.name if bank_config else bank_code

        stmt = Statement(
            bank_code=bank_code,
            bank_name=bank_name,
            pdf_filename=filename,
            status=StatementStatus.PARSING,
        )
        session.add(stmt)
        session.commit()
        session.refresh(stmt)

        # Unlock PDF
        unlocked_path = pdf_service.unlock_pdf(save_path, bank_code)
        parse_path = unlocked_path or save_path

        # Parse PDF
        parser = ParserFactory.get_parser(bank_code)
        transactions = parser.parse(parse_path)

        # Save transactions
        for txn_data in transactions:
            txn = Transaction(
                statement_id=stmt.id,
                **txn_data,
            )
            session.add(txn)

        stmt.status = StatementStatus.PARSED
        stmt.updated_at = datetime.now().isoformat()
        session.add(stmt)
        session.commit()
        session.refresh(stmt)

        logger.info(
            "pdf_processed",
            bank=bank_code,
            transactions=len(transactions),
            statement_id=stmt.id,
        )
        return stmt

    except Exception as e:
        logger.error("pdf_processing_error", error=str(e))
        if 'stmt' in locals():
            stmt.status = StatementStatus.ERROR
            stmt.error_message = str(e)
            session.add(stmt)
            session.commit()
            session.refresh(stmt)
            return stmt
        raise
    finally:
        pdf_service.cleanup(save_path, unlocked_path)
