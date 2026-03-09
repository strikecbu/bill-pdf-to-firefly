import json
import re
from typing import Dict, List, Optional

import pdfplumber
import structlog

from app.parsers.base_parser import BaseParser
from app.utils.account_mapper import get_destination_for_withdrawal

logger = structlog.get_logger()

ROC_OFFSET = 1911


class EsunParser(BaseParser):
    """Parser for 玉山銀行 (E.SUN) credit card statements.

    E.SUN statements use:
    - ROC year in header (115年01月), MM/DD dates in transactions
    - Text-based layout: 消費日 入帳日 消費明細 [消費地 外幣折算日] 幣別 金額
    - Card sections marked by "卡號：XXXX-XXXX-XXXX-XXXX（卡名）"
    - Foreign currency transactions with extra TWD conversion columns
    """

    # Normal transaction: 01/12 01/16 連支＊１２ＭＩＮＩ三重 TWD 165
    # Foreign:           01/18 01/19 Netflix.com SGP Los Gatos 01/19 TWD 560 TWD 560
    # Cashback:          02/15 02/15 ＵＢｅａｒ卡一般消費１％現金回饋 TWD -39
    # Fee:               01/18 01/19 國外交易服務費 TWD 8
    TX_PATTERN = re.compile(
        r"(\d{2}/\d{2})\s+"           # 消費日
        r"(\d{2}/\d{2})\s+"           # 入帳日
        r"(.+?)\s+"                    # 消費明細
        r"TWD\s+"                      # 幣別
        r"(-?[\d,]+)"                  # 金額
        r"(?:\s+TWD\s+(-?[\d,]+))?"   # optional: 繳款幣別 金額
    )

    # Auto-payment (no 入帳日): 01/30 感謝您辦理本行自動轉帳繳款！ TWD -8,673
    TX_PAYMENT_PATTERN = re.compile(
        r"(\d{2}/\d{2})\s+"
        r"(感謝您辦理本行自動轉帳繳款！)\s+"
        r"TWD\s+"
        r"(-?[\d,]+)"
    )

    # Card section header
    CARD_PATTERN = re.compile(r"卡號：\d{4}-\w{4}-\w{4}-(\d{4})（(.+?)）")

    def parse(self, pdf_path: str) -> List[Dict]:
        """Parse E.SUN credit card statement PDF."""
        transactions = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                full_text = ""
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    full_text += text + "\n"

            year, month = self._extract_statement_period(full_text)
            closing_month = self._extract_closing_month(full_text, year, month)
            transactions = self._parse_transactions(full_text, year, closing_month)

        except Exception as e:
            logger.error("esun_parse_error", error=str(e), path=pdf_path)
            raise

        logger.info("esun_parsed", transaction_count=len(transactions))
        return transactions

    def _extract_statement_period(self, text: str) -> tuple:
        """Extract year and month from ROC header."""
        # "115年01月 信用卡帳單"
        match = re.search(r"(\d{2,3})年(\d{2})月\s*信用卡帳單", text)
        if match:
            roc_year = int(match.group(1))
            return roc_year + ROC_OFFSET, int(match.group(2))
        return 2026, 1

    def _extract_closing_month(self, text: str, year: int, stmt_month: int) -> int:
        """Extract closing date month from statement header.

        E.SUN statements show closing date as ROC date (e.g. 115/02/15).
        The billing cycle may extend beyond the statement month.
        Returns the closing month to use for date resolution.
        """
        # Look for closing date pattern: "115/02/15" near the top of the statement
        match = re.search(r"(\d{2,3})/(\d{2})/(\d{2})\s", text[:500])
        if match:
            closing_month = int(match.group(2))
            return closing_month
        return stmt_month

    def _resolve_date(self, mmdd: str, year: int, closing_month: int) -> str:
        """Convert MM/DD to YYYY-MM-DD.

        Uses closing_month (from statement closing date) instead of statement month
        to correctly handle billing cycles that span across months.
        Transactions with month > closing_month are from the previous year.
        """
        parts = mmdd.split("/")
        tx_month = int(parts[0])
        tx_day = int(parts[1])
        tx_year = year
        if tx_month > closing_month:
            tx_year -= 1
        return f"{tx_year}-{tx_month:02d}-{tx_day:02d}"

    def _parse_transactions(self, text: str, year: int, closing_month: int) -> List[Dict]:
        transactions = []
        current_card = None
        current_card_name = None
        idx = 0
        in_detail_section = False

        lines = text.split("\n")
        for line in lines:
            line = line.strip()

            # Detect card section
            card_match = self.CARD_PATTERN.search(line)
            if card_match:
                current_card = card_match.group(1)
                current_card_name = card_match.group(2).strip()
                in_detail_section = True
                continue

            # Skip summary/header lines
            if line.startswith("上期應繳金額") or line.startswith("繳款幣別"):
                continue
            if line.startswith("本期費用明細") or line.startswith("本期消費明細"):
                in_detail_section = True
                continue
            if line.startswith("本期合計") or line.startswith("本期應繳總金額"):
                continue
            if "續下頁" in line:
                continue

            # Auto-payment line
            pay_match = self.TX_PAYMENT_PATTERN.match(line)
            if pay_match:
                pay_date = self._resolve_date(pay_match.group(1), year, closing_month)
                amount_str = pay_match.group(3).replace(",", "")
                try:
                    amount = int(amount_str)
                except ValueError:
                    continue

                idx += 1
                transactions.append({
                    "transaction_date": pay_date,
                    "posting_date": pay_date,
                    "description": "玉山銀行自動轉帳繳款",
                    "amount": abs(amount),
                    "currency": "TWD",
                    "card_last_four": current_card,
                    "transaction_type": "transfer",
                    "source_account": "玉山銀行",
                    "destination_account": self.identify_card(current_card) if current_card else "玉山信用卡",
                    "external_id": self.generate_external_id("esun", pay_date, idx),
                    "raw_data": json.dumps({"line": line}, ensure_ascii=False),
                    "notes": "玉山銀行信用卡對帳單自動匯入",
                })
                continue

            # Normal transaction line
            tx_match = self.TX_PATTERN.match(line)
            if tx_match:
                consume_mmdd = tx_match.group(1)
                posting_mmdd = tx_match.group(2)
                description = tx_match.group(3).strip()
                amount_str = tx_match.group(4).replace(",", "")

                try:
                    amount = int(amount_str)
                except ValueError:
                    continue

                # Skip zero amount
                if amount == 0:
                    continue

                consume_date = self._resolve_date(consume_mmdd, year, closing_month)
                posting_date = self._resolve_date(posting_mmdd, year, closing_month)

                txn_type = self.classify_transaction_type(description, amount)
                card_name = self.identify_card(current_card) if current_card else "玉山信用卡"

                if txn_type == "withdrawal":
                    source, destination = card_name, get_destination_for_withdrawal(description)
                elif txn_type == "deposit":
                    source, destination = get_destination_for_withdrawal(description), card_name
                elif txn_type == "transfer":
                    source, destination = "玉山銀行", card_name
                else:
                    source, destination = card_name, "其他"

                # Check for foreign currency description
                # Pattern: "Netflix.com SGP Los Gatos 01/19" — has a date embedded
                notes = "玉山銀行信用卡對帳單自動匯入"
                fx_in_desc = re.search(r"\s+(\d{2}/\d{2})$", description)
                if fx_in_desc:
                    # Strip the fx date from description
                    description = description[:fx_in_desc.start()].strip()
                    # The 5th group is the TWD conversion amount if present
                    if tx_match.group(5):
                        notes += f" | 外幣折算"

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
                    "external_id": self.generate_external_id("esun", consume_date, idx),
                    "raw_data": json.dumps({"line": line}, ensure_ascii=False),
                    "notes": notes,
                })

        return transactions

    def classify_transaction_type(self, description: str, amount: float) -> str:
        """Override: detect E.SUN-specific patterns."""
        if "自動轉帳繳款" in description:
            return "transfer"
        if "回饋" in description:
            return "deposit"
        return super().classify_transaction_type(description, amount)
