import json
import re
from typing import Dict, List, Optional

import pdfplumber
import structlog

from app.parsers.base_parser import BaseParser
from app.utils.account_mapper import get_destination_for_withdrawal

logger = structlog.get_logger()


class SinopacParser(BaseParser):
    """Parser for 永豐銀行 (Sinopac) statements.

    Supports two statement types:
    1. 綜合對帳單 (comprehensive) — deposit account tables with 交易日/摘要/支出/存入/餘額
    2. 信用卡電子帳單 (credit card) — text-based with 消費日/入帳起息日/卡號末四碼/帳單說明/金額
       Dates are MM/DD format, year inferred from statement header.
       Foreign currency transactions have extra fields.
    """

    # Credit card transaction line patterns:
    # Format: MM/DD MM/DD [card4] description amount [fx_date currency fx_amount]

    # Normal line: 01/13 01/16 1300 M- 麥當勞點點卡線上儲值 300
    # Auto-payment: 01/29 01/29 永豐自扣已入帳，謝謝！ -6,592
    # Cashback:     02/02 02/02 1300 大戶消費回饋入帳戶＿任務 94 元 0
    # Fee line:     02/09 02/11 1300 CLAUDE.AI SUBSCRIPTION 國外交易服務費 9
    CC_TX_PATTERN = re.compile(
        r"(\d{2}/\d{2})\s+"           # 消費日 MM/DD
        r"(\d{2}/\d{2})\s+"           # 入帳起息日 MM/DD
        r"(?:(\d{4})\s+)?"            # 卡號末四碼 (optional)
        r"(.+?)\s+"                    # 帳單說明
        r"(-?[\d,]+)$"                # 臺幣金額 (end of line)
    )

    # Foreign currency line: "02/09 02/11 1300 633 02/07 USD20.000"
    # or with country code:  "02/09 02/11 1300 633 02/07 US USD20.000"
    CC_FX_PATTERN = re.compile(
        r"(\d{2}/\d{2})\s+"           # 消費日
        r"(\d{2}/\d{2})\s+"           # 入帳起息日
        r"(\d{4})\s+"                  # 卡號末四碼
        r"(-?[\d,]+)\s+"              # 臺幣金額
        r"(\d{2}/\d{2})\s+"           # 外幣折算日
        r"(?:[A-Z]{2}\s+)?"           # 消費地 (optional, e.g. US)
        r"([A-Z]{3})"                  # 幣別
        r"([\d,.]+)"                   # 外幣金額
    )

    def parse(self, pdf_path: str) -> List[Dict]:
        """Parse Sinopac statement PDF (auto-detects type)."""
        transactions = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                full_text = ""
                all_tables = []
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    full_text += text + "\n"
                    for table in page.extract_tables():
                        all_tables.append(table)

                # Detect statement type
                if "信用卡電子帳單" in full_text or "信用卡帳單" in full_text:
                    year, month = self._extract_statement_period(full_text)
                    transactions.extend(self._parse_cc_transactions(full_text, year, month))
                else:
                    # Comprehensive statement — parse deposit tables
                    transactions.extend(self._parse_deposit_transactions(all_tables))

        except Exception as e:
            logger.error("sinopac_parse_error", error=str(e), path=pdf_path)
            raise

        logger.info("sinopac_parsed", transaction_count=len(transactions))
        return transactions

    def _extract_statement_period(self, text: str) -> tuple:
        """Extract year and month from statement header."""
        # "2026年2月 信用卡電子帳單" or "結帳日 2026/02/22"
        match = re.search(r"(\d{4})年(\d{1,2})月\s*信用卡", text)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.search(r"結帳日\s*(\d{4})/(\d{2})/", text)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 2026, 1  # fallback

    def _resolve_date(self, mmdd: str, year: int, month: int) -> str:
        """Convert MM/DD to YYYY-MM-DD, inferring year from statement period.

        If transaction month > statement month, it's from previous year.
        """
        parts = mmdd.split("/")
        tx_month = int(parts[0])
        tx_day = int(parts[1])
        tx_year = year
        if tx_month > month:
            tx_year -= 1
        return f"{tx_year}-{tx_month:02d}-{tx_day:02d}"

    def _parse_cc_transactions(self, text: str, year: int, month: int) -> List[Dict]:
        """Parse credit card statement transactions from text."""
        transactions = []
        idx = 0
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip lines that don't start with a date pattern
            if not re.match(r"\d{2}/\d{2}\s+\d{2}/\d{2}", line):
                # Check if this line is a description for a foreign tx on the next line
                # Pattern: "CLAUDE.AI SUBSCRIPTION\n02/09 02/11 1300 633 02/07 USD20.000"
                if i + 1 < len(lines) and line and not line.startswith("【"):
                    next_line = lines[i + 1].strip()
                    fx_match = self.CC_FX_PATTERN.match(next_line)
                    if fx_match:
                        # Gather description from this line + any continuation after fx line
                        desc_before = line
                        desc_after = ""
                        j = i + 2
                        if j < len(lines):
                            cont = lines[j].strip()
                            if cont and not re.match(r"\d{2}/\d{2}", cont) and not cont.startswith("您的") and not cont.startswith("【"):
                                desc_after = cont
                                j += 1

                        full_desc = desc_before + " " + desc_after if desc_after else desc_before
                        txn = self._build_fx_transaction(fx_match, full_desc, year, month, idx)
                        if txn:
                            idx += 1
                            transactions.append(txn)
                        i = j
                        continue
                i += 1
                continue

            # Try foreign currency line first
            fx_match = self.CC_FX_PATTERN.match(line)
            if fx_match:
                txn = self._build_fx_transaction(fx_match, None, year, month, idx)
                if txn:
                    idx += 1
                    transactions.append(txn)
                i += 1
                continue

            # Normal transaction line
            m = self.CC_TX_PATTERN.match(line)
            if m:
                txn = self._build_normal_cc_transaction(m, year, month, idx)
                if txn:
                    idx += 1
                    transactions.append(txn)
            i += 1

        return transactions

    def _build_normal_cc_transaction(self, m, year: int, month: int, idx: int) -> Optional[Dict]:
        """Build transaction dict from a normal credit card line."""
        consume_mmdd = m.group(1)
        posting_mmdd = m.group(2)
        card_last_four = m.group(3)
        description = m.group(4).strip()
        amount_str = m.group(5).replace(",", "")

        try:
            amount_twd = int(amount_str)
        except ValueError:
            return None

        # Skip zero-amount cashback notification lines
        if amount_twd == 0:
            return None

        consume_date = self._resolve_date(consume_mmdd, year, month)
        posting_date = self._resolve_date(posting_mmdd, year, month)

        txn_type = self.classify_transaction_type(description, amount_twd)
        card_name = self.identify_card(card_last_four) if card_last_four else "永豐信用卡"

        if txn_type == "withdrawal":
            source, destination = card_name, get_destination_for_withdrawal(description)
        elif txn_type == "deposit":
            source, destination = get_destination_for_withdrawal(description), card_name
        elif txn_type == "transfer":
            source, destination = "永豐銀行", card_name
        else:
            source, destination = card_name, "其他"

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
            "external_id": self.generate_external_id("sinopac-cc", consume_date, idx + 1),
            "raw_data": json.dumps({"line": m.group(0)}, ensure_ascii=False),
            "notes": "永豐銀行信用卡對帳單自動匯入",
        }

    def _build_fx_transaction(self, m, desc_override: Optional[str], year: int, month: int, idx: int) -> Optional[Dict]:
        """Build transaction dict from a foreign currency line."""
        consume_mmdd = m.group(1)
        posting_mmdd = m.group(2)
        card_last_four = m.group(3)
        amount_str = m.group(4).replace(",", "")
        # group(5) = fx_date, group(6) = currency, group(7) = fx_amount
        foreign_currency = m.group(6)
        try:
            foreign_amount = float(m.group(7).replace(",", ""))
        except (ValueError, TypeError):
            foreign_amount = None

        try:
            amount_twd = int(amount_str)
        except ValueError:
            return None

        consume_date = self._resolve_date(consume_mmdd, year, month)
        posting_date = self._resolve_date(posting_mmdd, year, month)

        description = desc_override or f"外幣交易 {foreign_currency} {foreign_amount}"
        txn_type = self.classify_transaction_type(description, amount_twd)
        card_name = self.identify_card(card_last_four) if card_last_four else "永豐信用卡"

        if txn_type == "withdrawal":
            source, destination = card_name, get_destination_for_withdrawal(description)
        elif txn_type == "deposit":
            source, destination = get_destination_for_withdrawal(description), card_name
        else:
            source, destination = card_name, "其他"

        notes_parts = ["永豐銀行信用卡對帳單自動匯入"]
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
            "external_id": self.generate_external_id("sinopac-cc", consume_date, idx + 1),
            "raw_data": json.dumps(
                {"line": m.group(0), "foreign_currency": foreign_currency, "foreign_amount": foreign_amount},
                ensure_ascii=False,
            ),
            "notes": " | ".join(notes_parts),
        }

    def classify_transaction_type(self, description: str, amount: float) -> str:
        """Override: detect Sinopac-specific patterns."""
        if "自扣已入帳" in description or "自動扣繳" in description:
            return "transfer"
        if "回饋入帳" in description:
            return "deposit"
        return super().classify_transaction_type(description, amount)

    # --- Comprehensive statement (綜合對帳單) parsing ---

    def _parse_deposit_transactions(self, tables: list) -> List[Dict]:
        """Parse deposit account transaction tables."""
        transactions = []
        idx = 0

        for table in tables:
            if not table or len(table) < 2:
                continue

            header = table[0]
            if not header or not isinstance(header, list):
                continue

            header_text = " ".join(str(c) for c in header if c)
            if "交易日" not in header_text or "摘要" not in header_text:
                continue

            for row in table[1:]:
                if not row or len(row) < 5:
                    continue

                first_cell = str(row[0] or "").strip()
                if first_cell.startswith("帳號:") or not first_cell:
                    continue

                if not re.match(r"\d{4}/\d{2}/\d{2}", first_cell):
                    continue

                txn_date = first_cell
                description = str(row[1] or "").strip()
                expense_str = str(row[2] or "").strip().replace(",", "")
                income_str = str(row[3] or "").strip().replace(",", "")
                memo = str(row[5] or "").strip() if len(row) > 5 else ""

                if expense_str:
                    try:
                        amount = float(expense_str)
                        txn_type = "withdrawal"
                    except ValueError:
                        continue
                elif income_str:
                    try:
                        amount = float(income_str)
                        txn_type = "deposit"
                    except ValueError:
                        continue
                else:
                    continue

                txn_type = super().classify_transaction_type(
                    description, amount if txn_type == "withdrawal" else -amount
                )

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
                    "source_account": "永豐銀行" if txn_type == "withdrawal" else "自動匯入",
                    "destination_account": "永豐銀行" if txn_type == "deposit" else get_destination_for_withdrawal(description),
                    "external_id": self.generate_external_id("sinopac", iso_date, idx),
                    "raw_data": json.dumps({"row": [str(c) for c in row]}, ensure_ascii=False),
                    "notes": "永豐銀行對帳單自動匯入",
                }
                transactions.append(txn)

        return transactions
