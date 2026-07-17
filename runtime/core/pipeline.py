from __future__ import annotations

import os
import random
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

import smbclient
from smbclient import register_session

from runtime.core.email_reader import SimpleEmailReader, EmailData, AttachmentData
from runtime.utils.logging_functions import get_logger

from py_impose import PDFProcessor, PaperTypes, BindingType, PageSize

logger = get_logger("PipeLine", "pipeline.log")


class Pipeline:
    """Reads emails -> Extracts Streams -> Sets Binding by Page Count -> Processes
    directly to SRA3 -> Uploads original + imposed PDF to an SMB share.

    Jeder Absender bekommt einen eigenen, stabilen Unterordner (wird
    wiederverwendet, sobald er einmal existiert). Dateien werden als
    "ddmmyyyy_original_<name>" / "ddmmyyyy_imposed_<name>" abgelegt.

    Duplikat-Erkennung: existiert im Ordner bereits eine "*_original_<name>"
    Datei (egal welches Datum) mit exakt demselben Inhalt wie der neue
    Anhang, wird nichts hochgeladen. Ist der Inhalt unterschiedlich, aber der
    für heute berechnete Dateiname würde eine bestehende Datei überschreiben,
    wird eine kurze Zufallszahl an den Dateinamen angehängt — ansonsten
    bleiben die Dateinamen so aufgeräumt wie möglich.

    py_impose exportiert intern über pymupdf.save(), das nur echte lokale
    Dateipfade versteht — kein SMB. Deshalb wird lokal in ein Temp-Verzeichnis
    exportiert und die fertige Datei danach per smbclient auf die Freigabe
    hochgeladen.
    """

    def __init__(self, output_dir: str | Path):
        self.server = os.getenv("SMB_SERVER")
        self.share = os.getenv("SMB_SHARE")
        username = os.getenv("SMB_USER")
        password = os.getenv("SMB_PASSWORD")

        if not all([self.server, self.share, username, password]):
            raise ValueError("Missing SMB_SERVER / SMB_SHARE / SMB_USER / SMB_PASSWORD in the environment.")

        logger.info(f"Registering SMB session to {self.server}...")
        register_session(self.server, username=username, password=password)

        self.output_dir = str(output_dir).strip("/\\")  # Pfad *innerhalb* der Freigabe
        root = self._remote_path(self.output_dir)
        if not smbclient.path.exists(root):
            smbclient.makedirs(root)

        self._reader = SimpleEmailReader()
        self._processed_ids: set[str] = set()

    # ------------------------------------------------------------------ #
    #  SMB helpers                                                       #
    # ------------------------------------------------------------------ #

    def _remote_path(self, *parts: str) -> str:
        clean_parts = [p.strip("/\\") for p in parts if p]
        return "\\\\" + "\\".join([self.server, self.share, *clean_parts])

    @staticmethod
    def _sender_folder_name(sender: str) -> str:
        """Gleicher Absender -> immer derselbe, stabile Ordnername."""
        local_part = sender.split("@")[0]
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", local_part).strip("_")
        return safe or "unbekannt"

    def _output_dir_for(self, email: EmailData) -> str:
        folder_name = self._sender_folder_name(email.sender)
        remote_dir = self._remote_path(self.output_dir, folder_name)
        if not smbclient.path.exists(remote_dir):
            smbclient.makedirs(remote_dir)
        return remote_dir

    # ------------------------------------------------------------------ #
    #  Public entry points                                               #
    # ------------------------------------------------------------------ #

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

        try:
            existing_entries = list(smbclient.scandir(output_dir))
        except Exception:
            logger.exception(f"Could not list '{output_dir}'")
            existing_entries = []
        existing_names = {e.name for e in existing_entries if not e.is_dir()}

        for att in email.attachments:
            self._process_attachment(att, output_dir, existing_names)

    def _process_attachment(self, att: AttachmentData, output_dir: str, existing_names: set[str]) -> None:
        # 1. Duplikat-Check: gibt es bereits eine "*_original_<name>" Datei
        #    (egal welches Datum) mit identischem Inhalt?
        for name in existing_names:
            if not name.endswith(f"_original_{att.name}"):
                continue
            try:
                with smbclient.open_file(f"{output_dir}\\{name}", mode="rb") as f:
                    if f.read() == att.content:
                        logger.info(f"'{att.name}' is byte-identical to existing '{name}' — skipping.")
                        return
            except Exception:
                logger.exception(f"Could not read '{name}' for duplicate check")

        # 2. Zieldateinamen für heute bestimmen, Kollision nur mit Zufallszahl
        #    auflösen, ansonsten aufgeräumte Namen.
        date_prefix = datetime.now().strftime("%d%m%Y")
        original_name = f"{date_prefix}_original_{att.name}"
        imposed_name = f"{date_prefix}_imposed_{att.name}"

        if original_name in existing_names or imposed_name in existing_names:
            stem = PurePosixPath(att.name).stem
            suffix = PurePosixPath(att.name).suffix
            rand = random.randint(1000, 9999)
            original_name = f"{date_prefix}_original_{stem}_{rand}{suffix}"
            imposed_name = f"{date_prefix}_imposed_{stem}_{rand}{suffix}"
            logger.info(f"'{att.name}' would overwrite an existing file from today with different content — using '{original_name}' / '{imposed_name}' instead.")

        original_path = f"{output_dir}\\{original_name}"
        imposed_path = f"{output_dir}\\{imposed_name}"

        # 3. Original hochladen
        with smbclient.open_file(original_path, mode="wb") as f:
            f.write(att.content)
        logger.info(f"[Pipeline] Saved original file: '{original_name}'")
        existing_names.add(original_name)

        # 4. Imposition lokal durchführen (py_impose kann nicht direkt auf SMB
        #    schreiben) und danach das fertige PDF hochladen.
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_output = Path(tmp_dir) / imposed_name

            processor = PDFProcessor(
                input_path=att.stream,
                output_path=str(local_output),
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

            if not local_output.exists():
                logger.error(f"py_impose produced no output for '{att.name}' — imposed file not uploaded.")
                return

            with open(local_output, "rb") as src, smbclient.open_file(imposed_path, mode="wb") as dst:
                dst.write(src.read())

        existing_names.add(imposed_name)
        logger.info(f"[Pipeline] Uploaded '{original_name}' and '{imposed_name}' to {output_dir}")