import os
from pathlib import Path
from typing import Optional

import pikepdf
import structlog

from app.config import get_settings, BankConfig

logger = structlog.get_logger()


class PdfService:
    def __init__(self):
        self.settings = get_settings()

    def generate_password(self, bank_config: BankConfig) -> str:
        """Generate PDF password based on bank's template and user info.

        Supported template variables:
        - {id_number}       : full ID number (e.g. A123456789)
        - {id_number_last2} : last 2 digits of ID number (e.g. 89)
        - {birthday}        : full birthday string (e.g. 19900704)
        - {birthday_mmdd}   : birthday month+day 4 digits (e.g. 0704)
        - {phone}           : phone number
        """
        template = bank_config.pdf_password_template
        user = self.settings.user

        password = template.format(
            id_number=user.id_number,
            id_number_last2=user.id_number[-2:] if len(user.id_number) >= 2 else "",
            birthday=user.birthday,
            birthday_mmdd=user.birthday[4:8] if len(user.birthday) >= 8 else user.birthday,
            phone=user.phone,
        )
        return password

    def unlock_pdf(self, pdf_path: str, bank_code: str) -> Optional[str]:
        """Unlock a password-protected PDF. Returns path to unlocked file, or None if not encrypted."""
        # First check if the PDF is actually encrypted
        try:
            pdf = pikepdf.open(pdf_path)
            pdf.close()
            logger.info("pdf_not_encrypted", path=pdf_path)
            return None  # Not encrypted, caller should use original path
        except pikepdf.PasswordError:
            pass  # PDF is encrypted, proceed to unlock

        bank_config = self.settings.banks.get(bank_code)
        if not bank_config:
            logger.error("bank_config_not_found", bank_code=bank_code)
            return None

        password = self.generate_password(bank_config)
        if not password:
            logger.error("pdf_password_empty", bank=bank_code)
            return None

        unlocked_path = pdf_path.replace(".pdf", "_unlocked.pdf")

        try:
            pdf = pikepdf.open(pdf_path, password=password)
            pdf.save(unlocked_path)
            pdf.close()
            logger.info("pdf_unlocked", path=unlocked_path)
            return unlocked_path
        except pikepdf.PasswordError:
            logger.error("pdf_password_error", bank=bank_code, path=pdf_path)
            return None
        except Exception as e:
            logger.error("pdf_unlock_error", bank=bank_code, error=str(e))
            return None

    def cleanup(self, *paths: str):
        """Remove temporary PDF files."""
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
                    logger.debug("file_cleaned", path=path)
            except OSError as e:
                logger.warning("cleanup_error", path=path, error=str(e))
