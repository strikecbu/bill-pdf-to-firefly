import base64
import json

import structlog
from fastapi import APIRouter, Request, BackgroundTasks

from app.services.import_service import process_notification

logger = structlog.get_logger()
router = APIRouter(tags=["webhook"])


@router.post("/webhook/gmail")
async def gmail_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Gmail Pub/Sub push notification."""
    body = await request.json()
    logger.info("webhook_received", body_keys=list(body.keys()))

    message = body.get("message", {})
    data = message.get("data", "")

    if data:
        decoded = json.loads(base64.b64decode(data).decode("utf-8"))
        email_address = decoded.get("emailAddress", "")
        history_id = decoded.get("historyId", "")
        logger.info("gmail_notification", email=email_address, history_id=history_id)
        background_tasks.add_task(process_notification, history_id)

    return {"status": "ok"}
