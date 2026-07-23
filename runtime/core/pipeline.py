from __future__ import annotations

import random
import re
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

from runtime.core.email_reader import SimpleEmailReader, EmailData, AttachmentData
from runtime.utils.logging_functions import get_logger

from py_impose import PDFProcessor, PaperTypes, BindingType, PageSize

logger = get_logger("PipeLine", "pipeline.log")


class Pipeline:
    """Reads emails -> Extracts Streams -> Sets Binding by Page Count -> Processes
    directly to SRA3 -> Saves original + imposed PDF to the output directory.

    ARCHITECTURE NOTE:
    This pipeline previously used the `smbprotocol` library to connect to network shares.
    Because older servers requiring SMBv1 (NT1) are incompatible with modern Python SMB 
    libraries, this was refactored. The pipeline now relies on OS-level volume mounts 
    (passed through Docker) and writes directly to `OUTPUT_DIR` using standard, fast 
    Python local file operations.

    Folder Structure:
    Every sender gets their own stable subfolder based on their email prefix. 

    Duplicate & Collision Handling:
    - If a file with the exact same bytes already exists, the upload is skipped entirely.
    - If a file has the same name for today but different content, a random 4-digit 
      suffix is appended to prevent overwriting existing data.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._reader = SimpleEmailReader()
        self._processed_ids: set[str] = set()

    @staticmethod
    def _sender_folder_name(sender: str) -> str:
        local_part = sender.split("@")[0]
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", local_part).strip("_")
        return safe or "unbekannt"

    def _output_dir_for(self, email: EmailData) -> Path:
        folder_name = self._sender_folder_name(email.sender)
        remote_dir = self.output_dir / folder_name
        remote_dir.mkdir(parents=True, exist_ok=True)
        return remote_dir

    def run(self, limit: int = 200, max_age_days: float = 1.0, user_mail: str = "info@straussdruck.at", folder_name: str = "DRUCKAUFTRÄGE") -> list[EmailData]:
        logger.info(f"Starting — limit={limit}, max_age_days={max_age_days}, folder={folder_name}")
        emails = self._reader.get_attachments_from_mailbox(
            user_mail=user_mail, limit=limit, max_age_days=max_age_days, folder_name=folder_name
        )
        logger.info(f"Fetched {len(emails)} emails with attachments")
        processed = self._process_new_emails(emails)
        logger.info(f"Done — {len(processed)} emails processed")
        return processed

    def run_forever(
            self, initial_max_age_days: float = 1.0, initial_limit: int = 200,
            poll_window_hours: float = 2.0, poll_limit: int = 20, sleep_seconds: int = 300,
            user_mail: str = "info@straussdruck.at", folder_name: str = "DRUCKAUFTRÄGE"
    ) -> None:
        logger.info(f"Starting continuous mode — sleep={sleep_seconds}s")
        self.run(limit=initial_limit, max_age_days=initial_max_age_days, user_mail=user_mail, folder_name=folder_name)
        poll_age_days = poll_window_hours / 24.0

        while True:
            logger.info(f"Polling for emails from the last {poll_window_hours} hour(s)...")
            try:
                emails = self._reader.get_attachments_from_mailbox(
                    user_mail=user_mail, limit=poll_limit, max_age_days=poll_age_days, folder_name=folder_name
                )
                new = self._process_new_emails(emails)
                if new:
                    logger.info(f"Processed {len(new)} new email(s)")
                else:
                    time.sleep(sleep_seconds)
            except Exception as e:
                logger.exception(f"Unexpected error during polling: {e}")
                time.sleep(sleep_seconds)

    def _process_new_emails(self, emails: list[EmailData]) -> list[EmailData]:
        processed = []
        for email in emails:
            email_id = f"{email.subject}_{email.received.timestamp()}"
            if email_id in self._processed_ids:
                continue
            try:
                self._process_email(email)
                processed.append(email)
                self._processed_ids.add(email_id)
            except Exception as e:
                logger.exception(f"Failed to process email '{email.subject}': {e}")
        return processed

    def _process_email(self, email: EmailData) -> None:
        logger.info(f"Processing '{email.subject}' from {email.sender}")
        output_dir = self._output_dir_for(email)
        try:
            existing_names = {p.name for p in output_dir.iterdir() if p.is_file()}
        except Exception:
            logger.exception(f"Could not list '{output_dir}'")
            existing_names = set()

        for att in email.attachments:
            self._process_attachment(att, output_dir, existing_names)

    def _process_attachment(self, att: AttachmentData, output_dir: Path, existing_names: set[str]) -> None:
        for name in existing_names:
            if not name.endswith(f"_original_{att.name}"):
                continue
            try:
                with open(output_dir / name, "rb") as f:
                    if f.read() == att.content:
                        logger.info(f"'{att.name}' is byte-identical to existing '{name}' — skipping.")
                        return
            except Exception:
                logger.exception(f"Could not read '{name}' for duplicate check")

        date_prefix = datetime.now().strftime("%d%m%Y")
        original_name = f"{date_prefix}_original_{att.name}"
        imposed_name = f"{date_prefix}_imposed_{att.name}"

        if original_name in existing_names or imposed_name in existing_names:
            stem = PurePosixPath(att.name).stem
            suffix = PurePosixPath(att.name).suffix
            rand = random.randint(1000, 9999)
            original_name = f"{date_prefix}_original_{stem}_{rand}{suffix}"
            imposed_name = f"{date_prefix}_imposed_{stem}_{rand}{suffix}"
            logger.info(f"'{att.name}' would overwrite — using '{original_name}' / '{imposed_name}'")

        original_path = output_dir / original_name
        imposed_path = output_dir / imposed_name

        with open(original_path, "wb") as f:
            f.write(att.content)
        logger.info(f"[Pipeline] Saved original file: '{original_name}'")
        existing_names.add(original_name)

        processor = PDFProcessor(
            input_path=att.stream,
            output_path=str(imposed_path),
            tile_to=PaperTypes.SRA3.value
        )
        processor.load()
        page_amount = len(processor.pages)
        binding_type = BindingType.NORMAL if page_amount <= 2 else BindingType.FLYER
        logger.info(f"File '{att.name}' ({page_amount} pages) -> Binding: {binding_type.name}")

        processor.update_value(
            impose__binding=binding_type,
            bleed__default_bleed=PageSize.mm_to_points(1),
            bleed__scaleForBleed=False,
            tile__inner_spacing=PageSize.mm_to_points(1),
            tile__outer_margin=PageSize.mm_to_points(1),
        )

        logger.info(f"Running py-impose pipeline for '{att.name}'...")
        processor.impose().bleed().tile().export()

        if not imposed_path.exists():
            logger.error(f"py_impose produced no output for '{att.name}'")
            return

        existing_names.add(imposed_name)
        logger.info(f"[Pipeline] Saved '{original_name}' and '{imposed_name}' directly to {output_dir}")