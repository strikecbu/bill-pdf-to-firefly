from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from uuid import uuid4


class BaseParser(ABC):
    """Base class for all bank statement parsers."""

    def __init__(self, bank_config):
        self.bank_config = bank_config

    @abstractmethod
    def parse(self, pdf_path: str) -> List[Dict]:
        """
        Parse a PDF statement and return a list of transaction dicts.
        Each dict should contain keys matching Transaction model fields:
        - transaction_date, posting_date, description, amount, currency,
        - card_last_four, transaction_type, source_account, destination_account,
        - external_id, raw_data, notes
        """
        pass

    def identify_card(self, card_last_four: str) -> str:
        """Map card last four digits to card account name."""
        if hasattr(self.bank_config, 'cards'):
            cards = self.bank_config.cards
        elif isinstance(self.bank_config, dict):
            cards = self.bank_config.get("cards", {})
        else:
            cards = {}
        return cards.get(card_last_four, f"未知卡片 {card_last_four}")

    def classify_transaction_type(self, description: str, amount: float) -> str:
        """
        Determine transaction type based on description and amount.
        Returns: withdrawal, deposit, or transfer
        """
        # Refund keywords
        refund_keywords = ["退款", "退貨", "沖正", "退回"]
        for kw in refund_keywords:
            if kw in description:
                return "deposit"

        # Payment keywords
        payment_keywords = ["繳款", "還款", "自動扣繳", "繳費"]
        for kw in payment_keywords:
            if kw in description:
                return "transfer"

        # Negative amount usually means refund
        if amount < 0:
            return "deposit"

        # Default: normal purchase
        return "withdrawal"

    def generate_external_id(self, bank_code: str, date: str, index: int) -> str:
        """Generate a unique external_id for deduplication."""
        return f"stmt-{bank_code}-{date}-{index:03d}"
