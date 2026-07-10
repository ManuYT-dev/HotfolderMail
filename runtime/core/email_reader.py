from __future__ import annotations
import os
import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass
from O365 import Account


@dataclass
class AttachmentData:
    name: str
    content_type: str
    size_bytes: int
    content: bytes
    stream: BytesIO


@dataclass
class EmailData:
    subject: str
    sender: str
    received: datetime
    attachments: list[AttachmentData]


class SimpleEmailReader:
    def __init__(self):
        client_id = os.getenv("AZURE_CLIENT_ID")
        client_secret = os.getenv("AZURE_CLIENT_SECRET")
        tenant_id = os.getenv("AZURE_TENANT_ID")

        if not all([client_id, client_secret, tenant_id]):
            raise ValueError("Missing Azure credentials. Check your .env file.")

        self._account = Account(
            (client_id, client_secret),
            auth_flow_type="credentials",
            tenant_id=tenant_id,
        )

        if not self._account.authenticate():
            raise RuntimeError("Authentication failed — check your Azure permissions.")

    def get_attachments_from_mailbox(
            self,
            user_mail: str,
            folder_name: str = "DRUCKAUFTRÄGE",
            limit: int = 50,
            max_age_days: float = 1.0
    ) -> list[EmailData]:
        """
        Logs into a user's mailbox, fetches emails within the max age, and extracts
        attachments along with their byte content and streams.
        """
        mailbox = self._account.mailbox(resource=user_mail)

        if folder_name:
            folder = mailbox.get_folder(folder_name=folder_name)
            if not folder:
                print(f"Warning: Folder '{folder_name}' not found. Falling back to Inbox.")
                folder = mailbox.inbox_folder()
        else:
            folder = mailbox.inbox_folder()

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)

        messages = folder.get_messages(limit=limit, order_by="receivedDateTime desc")
        processed_emails = []

        for msg in messages:
            if msg.received < cutoff:
                break

            if not msg.has_attachments:
                continue

            msg.attachments.download_attachments()
            parsed_attachments: list[AttachmentData] = []

            for att in msg.attachments:
                if att.is_inline:
                    continue

                raw_content = base64.b64decode(att.content) if isinstance(att.content, str) else att.content
                name = str(att.name).strip()
                suffix = Path(name).suffix.lower()

                stream = BytesIO(raw_content)

                parsed_attachments.append(
                    AttachmentData(
                        name=name,
                        content_type=suffix,
                        size_bytes=len(raw_content),
                        content=raw_content,
                        stream=stream,
                    )
                )

            if parsed_attachments:
                processed_emails.append(
                    EmailData(
                        subject=msg.subject,
                        sender=msg.sender.address,
                        received=msg.received,
                        attachments=parsed_attachments
                    )
                )

        return processed_emails


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    reader = SimpleEmailReader()

    emails = reader.get_attachments_from_mailbox(
        user_mail="info@straussdruck.at",
        limit=10,
        max_age_days=200.0
    )

    for email in emails:
        print(f"\nEmail: {email.subject} (From: {email.sender})")
        print(f"Received: {email.received}")
        for att in email.attachments:
            print(f"  -> File: {att.name} ({att.size_bytes} bytes)")