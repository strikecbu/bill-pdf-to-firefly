import re
from typing import Optional, Tuple

import structlog

from app.config import get_settings

logger = structlog.get_logger()


class MailClassifier:
    def __init__(self):
        self.settings = get_settings()

    def classify(self, sender: str, subject: str) -> Tuple[bool, Optional[str]]:
        """
        Classify an email as a credit card statement.
        Returns (is_statement, bank_code).
        """
        sender_lower = sender.lower()

        for bank_code, bank_config in self.settings.banks.items():
            # Check sender patterns
            sender_match = any(
                pattern.lower() in sender_lower
                for pattern in bank_config.sender_patterns
            )
            if not sender_match:
                continue

            # Check subject keywords
            subject_match = any(
                keyword in subject
                for keyword in bank_config.subject_keywords
            )
            if subject_match:
                logger.info(
                    "mail_classified",
                    bank=bank_code,
                    sender=sender,
                    subject=subject,
                )
                return True, bank_code

        logger.debug("mail_not_classified", sender=sender, subject=subject)
        return False, None

    def has_pdf_attachment(self, attachments: list) -> bool:
        """Check if any attachment is a PDF file."""
        return any(
            name.lower().endswith(".pdf") for _, name in attachments
        )
