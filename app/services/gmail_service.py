import base64
import os
from pathlib import Path
from typing import List, Optional, Tuple

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config import get_settings

logger = structlog.get_logger()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailService:
    def __init__(self):
        self.settings = get_settings()
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Gmail API using OAuth 2.0."""
        creds = None
        token_file = self.settings.gmail.token_file
        credentials_file = self.settings.gmail.credentials_file

        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(credentials_file):
                    logger.error("gmail_credentials_missing", file=credentials_file)
                    return
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_file, "w") as token:
                token.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("gmail_authenticated")

    def setup_watch(self) -> dict:
        """Register Gmail push notification via Pub/Sub."""
        if not self.service:
            return {}
        request_body = {
            "labelIds": [self.settings.gmail.label_filter],
            "topicName": self.settings.gmail.pubsub_topic,
        }
        result = self.service.users().watch(userId="me", body=request_body).execute()
        logger.info("gmail_watch_registered", result=result)
        return result

    def get_messages_since(self, history_id: str) -> List[dict]:
        """Get new messages since given history ID."""
        if not self.service:
            return []
        try:
            results = (
                self.service.users()
                .history()
                .list(userId="me", startHistoryId=history_id, historyTypes=["messageAdded"])
                .execute()
            )
            messages = []
            for history in results.get("history", []):
                for msg_added in history.get("messagesAdded", []):
                    messages.append(msg_added["message"])
            return messages
        except Exception as e:
            logger.error("gmail_history_error", error=str(e))
            return []

    def get_message(self, message_id: str) -> Optional[dict]:
        """Get full message content."""
        if not self.service:
            return None
        try:
            return (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception as e:
            logger.error("gmail_get_message_error", message_id=message_id, error=str(e))
            return None

    def get_message_headers(self, message: dict) -> dict:
        """Extract headers (From, Subject) from message."""
        headers = {}
        for header in message.get("payload", {}).get("headers", []):
            name = header["name"].lower()
            if name in ("from", "subject", "date"):
                headers[name] = header["value"]
        return headers

    def get_attachments(self, message: dict) -> List[Tuple[str, str]]:
        """Get list of (attachment_id, filename) for PDF attachments."""
        attachments = []
        parts = message.get("payload", {}).get("parts", [])
        for part in parts:
            filename = part.get("filename", "")
            if filename.lower().endswith(".pdf"):
                att_id = part.get("body", {}).get("attachmentId")
                if att_id:
                    attachments.append((att_id, filename))
        return attachments

    def download_attachment(self, message_id: str, attachment_id: str, save_path: str) -> str:
        """Download attachment and save to file."""
        if not self.service:
            return ""
        try:
            att = (
                self.service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            data = base64.urlsafe_b64decode(att["data"])
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
            logger.info("attachment_downloaded", path=save_path)
            return save_path
        except Exception as e:
            logger.error("attachment_download_error", error=str(e))
            return ""
