import json
import re
from typing import Dict, List, Optional

import pdfplumber
import structlog

from app.parsers.base_parser import BaseParser
from app.utils.account_mapper import get_destination_for_withdrawal

logger = structlog.get_logger()

# ROC year offset (民國年 + 1911 = 西元年)
ROC_OFFSET = 1911


def roc_to_iso(roc_date: str) -> str:
    """Convert ROC date (115/01/14) to ISO date (2026-01-14)."""
    match = re.match(r"(\d{2,3})/(\d{2})/(\d{2})", roc_date)
    if not match:
        return roc_date
    year = int(match.group(1)) + ROC_OFFSET
    return f"{year}-{match.group(2)}-{match.group(3)}"


class TaishinParser(BaseParser):
    """Parser for 台新銀行 (Taishin) credit card statements.

    Taishin statements use:
    - ROC calendar dates (民國年): 115/01/14 = 2026/01/14
    - Text-based layout (single-column table rows)
    - Card sections marked by "卡號末四碼:XXXX"
    - Foreign currency suffix: "0205 US USD 100.00"
    """

    # Regex for a transaction line:
    # 消費日 入帳起息日 消費明細 新臺幣金額 [外幣折算日 消費地 幣別 外幣金額]
    TX_PATTERN = re.compile(
        r"(\d{2,3}/\d{2}/\d{2})\s+"   # 消費日
        r"(\d{2,3}/\d{2}/\d{2})\s+"   # 入帳起息日
        r"(.+?)\s+"                     # 消費明細
        r"(-?[\d,]+)\s*"               # 新臺幣金額
        r"(?:(\d{4})\s+(\w{2})\s+(\w{3})\s+([\d,.]+))?"  # optional: 外幣折算日 消費地 幣別 外幣金額
    )

    # Multiline payment pattern (description wraps to next line)
    TX_MULTILINE_PATTERN = re.compile(
        r"(\d{2,3}/\d{2}/\d{2})\s+"
        r"(\d{2,3}/\d{2}/\d{2})\s+"
        r"(-?[\d,]+)"
    )

    # Card section header
    CARD_PATTERN = re.compile(r"卡號末四碼[:：](\d{4})")

    def parse(self, pdf_path: str) -> List[Dict]:
        """Parse Taishin credit card statement PDF."""
        transactions = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                full_text = ""
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    full_text += text + "\n"

            transactions = self._parse_transactions(full_text)

        except Exception as e:
            logger.error("taishin_parse_error", error=str(e), path=pdf_path)
            raise

        logger.info("taishin_parsed", transaction_count=len(transactions))
        return transactions

    def _parse_transactions(self, text: str) -> List[Dict]:
        transactions = []
        current_card = None
        idx = 0
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Detect card section
            card_match = self.CARD_PATTERN.search(line)
            if card_match:
                current_card = card_match.group(1)
                i += 1
                continue

            # Try to match a transaction line
            tx_match = self.TX_PATTERN.match(line)
            if tx_match:
                txn = self._build_transaction(tx_match, current_card, idx)
                if txn:
                    idx += 1
                    transactions.append(txn)
                i += 1
                continue

            # Try multiline: description wraps across lines.
            # Pattern A: "desc...\n115/01/27 115/01/27 -4,575\ncont..."
            #   → description on prev line, dates+amount on next line, possible continuation
            # Pattern B: normal 2-line join
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                # Pattern A: current line is description, next line starts with dates + amount
                multiline_match = self.TX_MULTILINE_PATTERN.match(next_line)
                if multiline_match and not re.match(r"\d{2,3}/", line):
                    # Gather continuation lines after the amount line
                    desc_parts = [line]
                    j = i + 2
                    while j < len(lines):
                        cont = lines[j].strip()
                        # Stop if next line looks like a date, card header, or empty
                        if not cont or re.match(r"\d{2,3}/", cont) or self.CARD_PATTERN.search(cont):
                            break
                        desc_parts.append(cont)
                        j += 1

                    full_desc = "".join(desc_parts)
                    consume_date_roc = multiline_match.group(1)
                    posting_date_roc = multiline_match.group(2)
                    amount_str = multiline_match.group(3).replace(",", "")

                    try:
                        amount_twd = int(amount_str)
                    except ValueError:
                        i += 1
                        continue

                    consume_date = roc_to_iso(consume_date_roc)
                    posting_date = roc_to_iso(posting_date_roc)
                    txn_type = self.classify_transaction_type(full_desc, amount_twd)
                    card_name = self.identify_card(current_card) if current_card else "台新信用卡"

                    if txn_type == "withdrawal":
                        source, destination = card_name, get_destination_for_withdrawal(full_desc)
                    elif txn_type == "deposit":
                        source, destination = get_destination_for_withdrawal(full_desc), card_name
                    elif txn_type == "transfer":
                        source, destination = "台新銀行", card_name
                    else:
                        source, destination = card_name, "其他"

                    idx += 1
                    transactions.append({
                        "transaction_date": consume_date,
                        "posting_date": posting_date,
                        "description": full_desc,
                        "amount": abs(amount_twd),
                        "currency": "TWD",
                        "card_last_four": current_card,
                        "transaction_type": txn_type,
                        "source_account": source,
                        "destination_account": destination,
                        "external_id": self.generate_external_id("taishin", consume_date, idx),
                        "raw_data": json.dumps({"lines": desc_parts + [next_line]}, ensure_ascii=False),
                        "notes": "台新銀行信用卡對帳單自動匯入",
                    })
                    i = j
                    continue

                # Pattern B: join two lines and try normal regex
                joined = line + " " + next_line
                tx_match = self.TX_PATTERN.match(joined)
                if tx_match:
                    txn = self._build_transaction(tx_match, current_card, idx)
                    if txn:
                        idx += 1
                        transactions.append(txn)
                    i += 2
                    continue

            i += 1

        return transactions

    def _build_transaction(self, match, card_last_four: Optional[str], idx: int) -> Optional[Dict]:
        """Build a transaction dict from a regex match."""
        consume_date_roc = match.group(1)
        posting_date_roc = match.group(2)
        description = match.group(3).strip()
        amount_str = match.group(4).replace(",", "")

        try:
            amount_twd = int(amount_str)
        except ValueError:
            return None

        # Foreign currency info (optional)
        foreign_currency = None
        foreign_amount = None
        if match.lastindex and match.lastindex >= 7:
            foreign_currency = match.group(7)  # e.g. USD
            try:
                foreign_amount = float(match.group(8).replace(",", ""))
            except (ValueError, TypeError):
                pass

        consume_date = roc_to_iso(consume_date_roc)
        posting_date = roc_to_iso(posting_date_roc)

        # Determine transaction type
        txn_type = self.classify_transaction_type(description, amount_twd)

        # Get card name
        card_name = self.identify_card(card_last_four) if card_last_four else "台新信用卡"

        # Build source/destination based on type
        if txn_type == "withdrawal":
            source = card_name
            destination = get_destination_for_withdrawal(description)
        elif txn_type == "deposit":
            source = get_destination_for_withdrawal(description)
            destination = card_name
        elif txn_type == "transfer":
            source = "台新銀行"
            destination = card_name
        else:
            source = card_name
            destination = "其他"

        # Build notes
        notes_parts = [f"台新銀行信用卡對帳單自動匯入"]
        if foreign_currency and foreign_amount:
            notes_parts.append(f"外幣: {foreign_currency} {foreign_amount}")

        return {
            "transaction_date": consume_date,
            "posting_date": posting_date,
            "description": description,
            "amount": abs(amount_twd),
            "currency": "TWD",
            "card_last_four": card_last_four,
            "transaction_type": txn_type,
            "source_account": source,
            "destination_account": destination,
            "external_id": self.generate_external_id("taishin", consume_date, idx + 1),
            "raw_data": json.dumps(
                {
                    "line": match.group(0),
                    "foreign_currency": foreign_currency,
                    "foreign_amount": foreign_amount,
                },
                ensure_ascii=False,
            ),
            "notes": " | ".join(notes_parts),
        }

    def classify_transaction_type(self, description: str, amount: float) -> str:
        """Override: also detect Taishin-specific patterns."""
        # Auto-payment
        if "自動轉帳扣繳" in description or "繳卡款" in description:
            return "transfer"

        # Annual fee waiver / rebate
        if "年費減免" in description or "回饋" in description:
            return "deposit"

        # Use base class logic
        return super().classify_transaction_type(description, amount)
