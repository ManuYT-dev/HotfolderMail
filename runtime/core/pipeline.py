from __future__ import annotations

import time
from pathlib import Path

from runtime.core.email_reader import SimpleEmailReader, EmailData
from runtime.utils.logging_functions import get_logger

from py_impose import PDFProcessor, PaperTypes, BindingType, PageSize

logger = get_logger("PipeLine", "pipeline.log")


class Pipeline:
    """Reads emails -> Extracts Streams -> Sets Binding by Page Count -> Processes directly to SRA3"""

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._reader = SimpleEmailReader()
        self._processed_ids: set[str] = set()

    def run(self, limit: int = 200, max_age_days: float = 1.0, user_mail: str = "info@straussdruck.at", folder_name: str = "DRUCKAUFTRÄGE") -> list[EmailData]:
        logger.info(f"Starting — limit={limit}, max_age_days={max_age_days}, folder={folder_name}")

        emails = self._reader.get_attachments_from_mailbox(
            user_mail=user_mail,
            limit=limit,
            max_age_days=max_age_days,
            folder_name=folder_name
        )

        logger.info(f"Fetched {len(emails)} emails with attachments")

        processed = self._process_new_emails(emails)

        logger.info(f"Done — {len(processed)} emails processed")
        return processed

    def run_forever(
            self,
            initial_max_age_days: float = 1.0,
            initial_limit: int = 200,
            poll_window_hours: float = 2.0,
            poll_limit: int = 20,
            sleep_seconds: int = 300,
            user_mail: str = "info@straussdruck.at",
            folder_name: str = "DRUCKAUFTRÄGE"
    ) -> None:

        logger.info(f"Starting continuous mode — sleep={sleep_seconds}s")
        self.run(limit=initial_limit, max_age_days=initial_max_age_days, user_mail=user_mail, folder_name=folder_name)

        poll_age_days = poll_window_hours / 24.0

        while True:
            logger.info(f"Polling for emails from the last {poll_window_hours} hour(s)...")

            try:
                emails = self._reader.get_attachments_from_mailbox(
                    user_mail=user_mail,
                    limit=poll_limit,
                    max_age_days=poll_age_days,
                    folder_name=folder_name
                )

                new = self._process_new_emails(emails)

                if new:
                    logger.info(f"Processed {len(new)} new email(s)")
                else:
                    time.sleep(sleep_seconds)

            except Exception as e:
                logger.exception(f"Unexpected error during polling: {e}")
                time.sleep(sleep_seconds)

    # ------------------------------------------------------------------ #
    #  Internal Processing                                               #
    # ------------------------------------------------------------------ #

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

        for att in email.attachments:
            # Define final output path for this specific attachment
            original_file = output_dir / f"original_{att.name}"
            original_file.write_bytes(att.content)
            logger.info(f"[Pipeline] Saved original file: '{original_file.name}'")

            output_file = output_dir / f"imposed_{att.name}"

            # 1. Initialize PDFProcessor with streams and target format
            processor = PDFProcessor(
                input_path=att.stream,
                output_path=str(output_file),
                tile_to=PaperTypes.SRA3.value
            )

            # 2. Load the file first so py-impose parses the document
            processor.load()

            # 3. Determine binding type based on the actual loaded pages
            page_amount = len(processor.pages)
            binding_type = BindingType.NORMAL if page_amount <= 2 else BindingType.FLYER

            logger.info(f"File '{att.name}' ({page_amount} pages) -> Binding: {binding_type.name}")

            # 4. Update imposition configuration dynamically
            processor.update_value(
                impose__binding=binding_type,
                bleed__default_bleed=PageSize.mm_to_points(1),
                bleed__scaleForBleed=False,
                tile__inner_spacing=PageSize.mm_to_points(1),
                tile__outer_margin=PageSize.mm_to_points(1),
            )

            # 5. Chain the remaining execution (skipping .load() since it's already done, and skipping .resize())
            logger.info(f"Running py-impose pipeline for '{att.name}'...")
            processor.impose().bleed().tile().export()

    def _output_dir_for(self, email: EmailData) -> Path:
        folder_name = email.sender.split('@')[0]
        folder_name = "".join(c for c in folder_name if c.isalnum() or c in " _-").strip()
        path = self.output_dir / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path