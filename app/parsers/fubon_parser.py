import json
import re
from typing import Dict, List

import pdfplumber
import structlog

from app.parsers.base_parser import BaseParser
from app.utils.account_mapper import get_destination_for_withdrawal

logger = structlog.get_logger()

ROC_OFFSET = 1911


class FubonParser(BaseParser):
    """Parser for 富邦銀行 (Fubon) credit card statements.

    Fubon statements use:
    - Full ROC dates (YYY/MM/DD) for both consume and posting dates
    - Text-based layout: 消費日期 消費說明 入帳日期 [外幣折算日/幣別 外幣金額/消費地] 台幣金額
    - Card sections marked by "末４碼XXXX"
    - Statement period header: 帳單年月 115/02
    """

    # Normal transaction: 115/01/17 好市多新莊店加油站 115/01/19 TWD 615
    TX_PATTERN = re.compile(
        r"(\d{2,3}/\d{2}/\d{2})\s+"     # 消費日期 (ROC)
        r"(.+?)\s+"                       # 消費說明
        r"(\d{2,3}/\d{2}/\d{2})\s+"      # 入帳日期 (ROC)
        r"(?:(\w{3})\s+)?"               # optional: 外幣幣別
        r"(-?[\d,]+)$"                    # 台幣金額
    )

    # Auto-payment: 115/02/04 自動扣繳 115/02/05 -10,421
    TX_PAYMENT_PATTERN = re.compile(
        r"(\d{2,3}/\d{2}/\d{2})\s+"
        r"(自動扣繳)\s+"
        r"(\d{2,3}/\d{2}/\d{2})\s+"
        r"(-?[\d,]+)$"
    )

    # Card section header: MASTER鈦金正卡末４碼1186 or various patterns with 末４碼
    CARD_PATTERN = re.compile(r"末４碼(\d{4})")

    # Summary lines to skip
    SUMMARY_PATTERN = re.compile(
        r"^(前期應繳總額|本期應繳金額|本期應繳總額|本期新增|"
        r"帳單年月|信用額度|前期|繳款截止日|循環信用)"
    )

    def parse(self, pdf_path: str) -> List[Dict]:
        """Parse Fubon credit card statement PDF."""
        transactions = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                full_text = ""
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    full_text += text + "\n"

            transactions = self._parse_transactions(full_text)

        except Exception as e:
            logger.error("fubon_parse_error", error=str(e), path=pdf_path)
            raise

        logger.info("fubon_parsed", transaction_count=len(transactions))
        return transactions

    def _roc_to_iso(self, roc_date: str) -> str:
        """Convert ROC date (YYY/MM/DD) to ISO format (YYYY-MM-DD)."""
        parts = roc_date.split("/")
        year = int(parts[0]) + ROC_OFFSET
        month = int(parts[1])
        day = int(parts[2])
        return f"{year}-{month:02d}-{day:02d}"

    def _parse_transactions(self, text: str) -> List[Dict]:
        transactions = []
        current_card = None
        idx = 0

        lines = text.split("\n")
        for line in lines:
            line = line.strip()

            # Skip empty lines
            if not line:
                continue

            # Detect card section
            card_match = self.CARD_PATTERN.search(line)
            if card_match:
                current_card = card_match.group(1)
                continue

            # Skip summary/header lines
            if self.SUMMARY_PATTERN.match(line):
                continue

            # Skip page headers and non-transaction lines
            if line.startswith("第") and "頁" in line:
                continue
            if "好多金" in line or "回饋金" in line or "數位生活卡" in line:
                continue

            # Auto-payment line
            pay_match = self.TX_PAYMENT_PATTERN.match(line)
            if pay_match:
                consume_date = self._roc_to_iso(pay_match.group(1))
                posting_date = self._roc_to_iso(pay_match.group(3))
                amount_str = pay_match.group(4).replace(",", "")
                try:
                    amount = int(amount_str)
                except ValueError:
                    continue

                idx += 1
                card_name = self.identify_card(current_card) if current_card else "富邦信用卡"
                transactions.append({
                    "transaction_date": consume_date,
                    "posting_date": posting_date,
                    "description": "富邦銀行自動扣繳",
                    "amount": abs(amount),
                    "currency": "TWD",
                    "card_last_four": current_card,
                    "transaction_type": "transfer",
                    "source_account": "富邦銀行",
                    "destination_account": card_name,
                    "external_id": self.generate_external_id("fubon", consume_date, idx),
                    "raw_data": json.dumps({"line": line}, ensure_ascii=False),
                    "notes": "富邦銀行信用卡對帳單自動匯入",
                })
                continue

            # Normal transaction line
            tx_match = self.TX_PATTERN.match(line)
            if tx_match:
                consume_date = self._roc_to_iso(tx_match.group(1))
                description = tx_match.group(2).strip()
                posting_date = self._roc_to_iso(tx_match.group(3))
                foreign_currency = tx_match.group(4)
                amount_str = tx_match.group(5).replace(",", "")

                try:
                    amount = int(amount_str)
                except ValueError:
                    continue

                # Skip zero amount
                if amount == 0:
                    continue

                txn_type = self.classify_transaction_type(description, amount)
                card_name = self.identify_card(current_card) if current_card else "富邦信用卡"

                if txn_type == "withdrawal":
                    source, destination = card_name, get_destination_for_withdrawal(description)
                elif txn_type == "deposit":
                    source, destination = get_destination_for_withdrawal(description), card_name
                elif txn_type == "transfer":
                    source, destination = "富邦銀行", card_name
                else:
                    source, destination = card_name, "其他"

                notes = "富邦銀行信用卡對帳單自動匯入"
                if foreign_currency and foreign_currency != "TWD":
                    notes += f" | 外幣: {foreign_currency}"

                idx += 1
                transactions.append({
                    "transaction_date": consume_date,
                    "posting_date": posting_date,
                    "description": description,
                    "amount": abs(amount),
                    "currency": "TWD",
                    "card_last_four": current_card,
                    "transaction_type": txn_type,
                    "source_account": source,
                    "destination_account": destination,
                    "external_id": self.generate_external_id("fubon", consume_date, idx),
                    "raw_data": json.dumps({"line": line}, ensure_ascii=False),
                    "notes": notes,
                })

        return transactions

    def classify_transaction_type(self, description: str, amount: float) -> str:
        """Override: detect Fubon-specific patterns."""
        if "自動扣繳" in description:
            return "transfer"
        if "回饋" in description:
            return "deposit"
        return super().classify_transaction_type(description, amount)
