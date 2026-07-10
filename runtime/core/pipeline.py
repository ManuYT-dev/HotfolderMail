from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from runtime.core.email_parser import EmailParser
from runtime.core.email_reader import EmailReader
from runtime.core.models import Email
from runtime.core.pdf_converter import PDFProcessor
from runtime.utils import paper_types

from runtime.utils.logging_functions import get_logger
logger = get_logger("PipeLine", "pipeline.log")


class Pipeline:
    """Verbindet EmailReader → EmailParser → PDFProcessor zu einem Ablauf."""

    def __init__(
        self,
        credentials_path: str | Path,
        output_dir: str | Path,
        tile_to: paper_types.PageSize = paper_types.SRA3,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tile_to = tile_to

        self._reader = EmailReader(credentials_path)
        self._parser = EmailParser()
        self._processed_ids: set[str] = set()

    # ------------------------------------------------------------------ #
    #  Öffentliche API                                                     #
    # ------------------------------------------------------------------ #

    def run(
        self,
        limit: int = 200,
        max_age_days: float = 1.0,
        user_mail: str | None = None,
    ) -> list[Email]:
        """Einmaliger Lauf — verarbeitet alle Emails der letzten max_age_days."""
        logger.info("[Pipeline] Starting — limit=%d, max_age_days=%.1f, user=%s",
                    limit, max_age_days, user_mail or "all")

        emails = self._reader.get_emails(
            limit=limit,
            max_age_days=max_age_days,
            user_mail=user_mail,
        )
        logger.info("[Pipeline] Fetched %d emails", len(emails))

        processed = self._process_new_emails(emails)

        logger.info("[Pipeline] Done — %d/%d emails processed", len(processed), len(emails))
        return processed

    def run_forever(
        self,
        initial_max_age_days: float = 1.0,
        initial_limit: int = 200,
        poll_window_hours: float = 2.0,
        poll_limit=20,
        sleep_seconds: int = 300,
        user_mail: str | None = None,
    ) -> None:
        """Läuft endlos:
        1. Verarbeitet alle Emails des initialen Zeitfensters.
        2. Pollt danach alle poll_window_hours auf neue Emails.
        3. Schläft sleep_seconds wenn keine neuen Emails gefunden wurden.
        """
        logger.info(
            "[Pipeline] Starting continuous mode — poll_window=%.1fh, sleep=%ds",
            poll_window_hours, sleep_seconds,
        )

        # Erster Lauf: komplettes initiales Zeitfenster
        self.run(limit=initial_limit, max_age_days=initial_max_age_days, user_mail=user_mail)

        poll_age_days = poll_window_hours / 24.0

        while True:
            logger.info("[Pipeline] Polling for emails from the last %.1f hour(s)...", poll_window_hours)

            try:
                emails = self._reader.get_emails(
                    limit=poll_limit,
                    max_age_days=poll_age_days,
                    user_mail=user_mail,
                )

                new = self._process_new_emails(emails)

                if new:
                    logger.info("[Pipeline] Processed %d new email(s)", len(new))
                else:
                    logger.info("[Pipeline] No new emails — sleeping %d seconds...", sleep_seconds)
                    time.sleep(sleep_seconds)

            except Exception:
                logger.exception("[Pipeline] Unexpected error during polling — sleeping before retry")
                time.sleep(sleep_seconds)

    # ------------------------------------------------------------------ #
    #  Intern                                                              #
    # ------------------------------------------------------------------ #

    def _process_new_emails(self, emails: list[Email]) -> list[Email]:
        """Filtert bereits verarbeitete Emails heraus und verarbeitet nur neue."""
        processed = []
        for email in emails:
            if email.id in self._processed_ids:
                continue
            try:
                if email.has_attachments:
                    self._process_email(email)
                    processed.append(email)
                # Als gesehen markieren, auch wenn keine Attachments
                self._processed_ids.add(email.id)
            except Exception:
                logger.exception("[Pipeline] Failed to process email '%s'", email.subject)
        return processed

    def _process_email(self, email: Email) -> None:
        logger.info("[Pipeline] Processing '%s' from %s", email.subject, email.Kunde.Email)

        # Schritt 1: KI analysiert die Email
        self._parser.parse(email)

        if not email.Is_Auftrag:
            logger.info("[Pipeline] Not an order — skipping '%s'", email.subject)
            return

        if not email.Auftraege:
            logger.warning("[Pipeline] Marked as order but no Auftraege found in '%s'", email.subject)
            return
        
        logger.info("[Pipeline] Found %d Auftrag(e) in '%s'", len(email.Auftraege), email.subject)

        output_dir = self._output_dir_for(email)
        processors = PDFProcessor.from_email(email, output_dir=output_dir, tile_to=self.tile_to)

        for processor in processors:
            logger.info("[Pipeline] Processing PDF '%s'", processor.input_path)
            processor.run()

        email.mark_as_read()

    def _output_dir_for(self, email: Email) -> Path:
        """Erstellt einen Unterordner pro Absender."""
        folder_name = email.Kunde.Organisation or email.Kunde.Name
        folder_name = "".join(c for c in folder_name if c.isalnum() or c in " _-").strip()
        path = self.output_dir / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path