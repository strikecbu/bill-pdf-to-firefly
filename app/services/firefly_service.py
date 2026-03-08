import time
from typing import Optional, List

import httpx
import structlog

from app.config import FireflyConfig
from app.models.database import Transaction

logger = structlog.get_logger()


class FireflyService:
    def __init__(self, config: FireflyConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {config.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.api+json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """Make an API request with retry logic."""
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    response = client.request(
                        method, url, headers=self.headers, **kwargs
                    )
                    if response.status_code in (200, 201):
                        return response.json()
                    elif response.status_code == 422:
                        # Validation error - likely duplicate
                        logger.warning(
                            "firefly_validation_error",
                            endpoint=endpoint,
                            detail=response.text,
                        )
                        return None
                    else:
                        logger.warning(
                            "firefly_api_error",
                            status=response.status_code,
                            attempt=attempt + 1,
                            detail=response.text,
                        )
            except httpx.TimeoutException:
                logger.warning("firefly_timeout", attempt=attempt + 1, endpoint=endpoint)
            except Exception as e:
                logger.error("firefly_request_error", error=str(e), attempt=attempt + 1)

            if attempt < self.config.max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)

        logger.error("firefly_max_retries_exceeded", endpoint=endpoint)
        return None

    def check_duplicate(self, txn: Transaction) -> bool:
        """Check if transaction already exists in Firefly III."""
        if txn.external_id:
            result = self._request(
                "GET",
                "/api/v1/transactions",
                params={"external_id": txn.external_id},
            )
            if result and result.get("data"):
                return True
        return False

    def create_transaction(self, txn: Transaction) -> Optional[int]:
        """Create a single transaction in Firefly III. Returns firefly transaction ID."""
        if self.check_duplicate(txn):
            logger.info("transaction_duplicate_skipped", external_id=txn.external_id)
            return None

        payload = {
            "transactions": [
                {
                    "type": txn.transaction_type,
                    "date": txn.transaction_date,
                    "amount": str(abs(txn.amount)),
                    "description": txn.description,
                    "source_name": txn.source_account,
                    "destination_name": txn.destination_account,
                    "currency_code": txn.currency,
                    "external_id": txn.external_id,
                    "notes": txn.notes or "",
                }
            ]
        }

        result = self._request("POST", "/api/v1/transactions", json=payload)
        if result:
            txn_id = result.get("data", {}).get("id")
            logger.info(
                "transaction_created",
                firefly_id=txn_id,
                description=txn.description,
            )
            return int(txn_id) if txn_id else None
        return None

    def batch_create_transactions(self, transactions: List[Transaction]) -> dict:
        """Create multiple transactions. Returns summary report."""
        report = {"total": len(transactions), "imported": 0, "failed": 0, "skipped": 0, "errors": []}

        for txn in transactions:
            try:
                if self.check_duplicate(txn):
                    report["skipped"] += 1
                    continue

                result = self.create_transaction(txn)
                if result:
                    txn.firefly_id = result
                    txn.import_status = "imported"
                    report["imported"] += 1
                else:
                    txn.import_status = "failed"
                    report["failed"] += 1
            except Exception as e:
                txn.import_status = "failed"
                report["failed"] += 1
                report["errors"].append(f"{txn.description}: {str(e)}")
                logger.error("transaction_import_error", description=txn.description, error=str(e))

        return report

    def get_accounts(self) -> list:
        """Get list of accounts from Firefly III."""
        result = self._request("GET", "/api/v1/accounts", params={"type": "all"})
        if result:
            return result.get("data", [])
        return []
