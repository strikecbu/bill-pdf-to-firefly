import json
import re
from typing import Dict, List, Optional

import pdfplumber
import structlog

from app.parsers.base_parser import BaseParser
from app.utils.account_mapper import get_destination_for_withdrawal

logger = structlog.get_logger()


class SinopacParser(BaseParser):
    """Parser for 永豐銀行 (Sinopac) statements."""

    def parse(self, pdf_path: str) -> List[Dict]:
        """Parse Sinopac bank statement PDF."""
        transactions = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        all_tables.append(table)

                # Find the deposit transaction table (交易日, 摘要, 支出, 存入, 餘額)
                transactions.extend(self._parse_deposit_transactions(all_tables))

                # Find credit card transaction table if present
                transactions.extend(self._parse_credit_card_transactions(all_tables, pdf))

        except Exception as e:
            logger.error("sinopac_parse_error", error=str(e), path=pdf_path)
            raise

        logger.info("sinopac_parsed", transaction_count=len(transactions))
        return transactions

    def _parse_deposit_transactions(self, tables: list) -> List[Dict]:
        """Parse deposit account transaction tables."""
        transactions = []
        idx = 0

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Look for the deposit transaction header
            header = table[0]
            if not header or not isinstance(header, list):
                continue

            header_text = " ".join(str(c) for c in header if c)
            if "交易日" not in header_text or "摘要" not in header_text:
                continue

            # Parse rows
            for row in table[1:]:
                if not row or len(row) < 5:
                    continue

                # Skip sub-headers (account lines)
                first_cell = str(row[0] or "").strip()
                if first_cell.startswith("帳號:") or not first_cell:
                    continue

                # Validate date format (YYYY/MM/DD)
                date_match = re.match(r"\d{4}/\d{2}/\d{2}", first_cell)
                if not date_match:
                    continue

                txn_date = first_cell
                description = str(row[1] or "").strip()
                expense_str = str(row[2] or "").strip().replace(",", "")
                income_str = str(row[3] or "").strip().replace(",", "")
                memo = str(row[5] or "").strip() if len(row) > 5 else ""

                # Determine amount and type
                if expense_str and expense_str != "":
                    try:
                        amount = float(expense_str)
                        txn_type = "withdrawal"
                    except ValueError:
                        continue
                elif income_str and income_str != "":
                    try:
                        amount = float(income_str)
                        txn_type = "deposit"
                    except ValueError:
                        continue
                else:
                    continue

                # Override type based on description
                txn_type = self.classify_transaction_type(description, amount if txn_type == "withdrawal" else -amount)

                idx += 1
                iso_date = txn_date.replace("/", "-")

                txn = {
                    "transaction_date": iso_date,
                    "posting_date": iso_date,
                    "description": memo if memo else description,
                    "amount": amount,
                    "currency": "TWD",
                    "card_last_four": None,
                    "transaction_type": txn_type,
                    "source_account": self._get_source_account(txn_type),
                    "destination_account": self._get_destination_account(txn_type, description),
                    "external_id": self.generate_external_id("sinopac", iso_date, idx),
                    "raw_data": json.dumps({"row": [str(c) for c in row]}, ensure_ascii=False),
                    "notes": f"永豐銀行對帳單自動匯入",
                }
                transactions.append(txn)

        return transactions

    def _parse_credit_card_transactions(self, tables: list, pdf) -> List[Dict]:
        """Parse credit card transaction tables if present.

        Looks for a dedicated credit card detail section with headers like
        '交易日' / '入帳日' / '卡號末四碼' that distinguish it from the
        deposit account section. The comprehensive (綜合) statement from
        Sinopac typically only shows a credit card summary total, not
        individual transactions — so this method may return an empty list
        for that statement type.
        """
        transactions = []
        idx = 0

        # Try table-based detection first: look for credit card specific headers
        for table in tables:
            if not table or len(table) < 2:
                continue
            header = table[0]
            if not header or not isinstance(header, list):
                continue
            header_text = " ".join(str(c) for c in header if c)
            # Credit card tables typically have columns like 卡號/入帳日
            if not ("卡號" in header_text or "入帳日" in header_text):
                continue

            for row in table[1:]:
                if not row or len(row) < 4:
                    continue
                first_cell = str(row[0] or "").strip()
                if not re.match(r"\d{4}/\d{2}/\d{2}", first_cell):
                    continue

                txn_date = first_cell
                # Column mapping depends on actual header order
                post_date = str(row[1] or "").strip() if len(row) > 1 else txn_date
                description = str(row[2] or "").strip() if len(row) > 2 else ""
                amount_str = str(row[3] or "").strip().replace(",", "") if len(row) > 3 else "0"
                card_last_four = str(row[-1] or "").strip() if len(row) > 4 else None

                try:
                    amount = float(amount_str)
                except ValueError:
                    continue

                txn_type = self.classify_transaction_type(description, amount)
                card_name = self.identify_card(card_last_four) if card_last_four else "永豐信用卡"

                idx += 1
                iso_date = txn_date.replace("/", "-")
                iso_post_date = post_date.replace("/", "-") if re.match(r"\d{4}/\d{2}/\d{2}", post_date) else iso_date

                txn = {
                    "transaction_date": iso_date,
                    "posting_date": iso_post_date,
                    "description": description,
                    "amount": abs(amount),
                    "currency": "TWD",
                    "card_last_four": card_last_four,
                    "transaction_type": txn_type,
                    "source_account": card_name if txn_type == "withdrawal" else self._get_source_account(txn_type),
                    "destination_account": self._get_destination_account(txn_type, description) if txn_type == "withdrawal" else card_name,
                    "external_id": self.generate_external_id("sinopac-cc", iso_date, idx),
                    "raw_data": json.dumps({"row": [str(c) for c in row]}, ensure_ascii=False),
                    "notes": "永豐銀行信用卡對帳單自動匯入",
                }
                transactions.append(txn)

        return transactions

    def _get_source_account(self, txn_type: str) -> str:
        """Get source account name based on transaction type."""
        if txn_type == "withdrawal":
            return "永豐銀行"
        elif txn_type == "deposit":
            return "自動匯入"
        elif txn_type == "transfer":
            return "永豐銀行"
        return "永豐銀行"

    def _get_destination_account(self, txn_type: str, description: str) -> str:
        """Get destination account based on transaction type and description."""
        if txn_type == "withdrawal":
            return get_destination_for_withdrawal(description)
        elif txn_type == "deposit":
            return "永豐銀行"
        elif txn_type == "transfer":
            return "永豐信用卡"
        return "其他"
