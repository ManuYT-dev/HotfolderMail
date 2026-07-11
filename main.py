import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

log_dir = os.getenv("LOG_DIR", "logs")
Path(log_dir).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,  # Change this to logging.DEBUG if you want deeper logs
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"{log_dir}/py_impose.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr)
    ]
)

from runtime.core.pipeline import Pipeline
from runtime.utils.logging_functions import get_logger

logger = get_logger("Main", "main.log")


def main():
    logger.info("Starting HotfolderMail Application...")

    output_dir_path = os.getenv("OUTPUT_DIR", "data/output")
    output_directory = Path(output_dir_path)

    try:
        # Initialize the pipeline
        pipeline = Pipeline(output_dir=output_directory)

        # ---------------------------------------------------------
        # OPTION 1: One-time run (Uncomment for quick testing)
        # ---------------------------------------------------------
        # logger.info("Executing one-time fetch...")
        # pipeline.run(
        #     limit=20,
        #     max_age_days=500.0,
        #     user_mail="info@straussdruck.at",
        #     folder_name="DRUCKAUFTRÄGE"
        # )

        # ---------------------------------------------------------
        # OPTION 2: Continuous Polling (Production Mode)
        # ---------------------------------------------------------
        logger.info("Starting continuous polling mode...")
        pipeline.run_forever(
            initial_max_age_days=20.0,  # Fetch the last 20 days on the very first run
            initial_limit=100,  # Max emails on startup
            poll_window_hours=24.0,  # Look back 24 hours on every subsequent poll
            poll_limit=20,  # Max emails per poll
            sleep_seconds=300,  # Wait 5 minutes (300s) between checks
            user_mail="info@straussdruck.at",
            folder_name="DRUCKAUFTRÄGE"
        )

    except KeyboardInterrupt:
        logger.info("Application stopped manually by user (Ctrl+C).")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal application error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()