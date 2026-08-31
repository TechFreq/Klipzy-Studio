"""
Shared logging setup for Klipzy Studio.

Two-owner design to avoid duplicate lines:
  * Running standalone (`python main.py` / `python -m server.api.server`):
    this module creates the file+console handlers itself.
  * Running inside the Electron app: Electron captures the server's own
    stdout/stderr into the same `logs/` folder and sets
    KLIPZY_ELECTRON_CAPTURE=1, so this module only configures the console
    stream. Logs always land in `logs/` either way.

Files (rotated at 1 MB, keeping the last 5):
  logs/server.log          - everything
  logs/server-error.log    - warnings + errors only
"""

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path


def _logs_dir() -> Path:
    if os.environ.get("KLIPZY_LOG_DIR"):
        return Path(os.environ["KLIPZY_LOG_DIR"])
    # server/logging_setup.py -> server/ -> repo root/logs
    return Path(__file__).resolve().parent.parent / "logs"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("klipzy")
    if logger.handlers:
        return logger
    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # When Electron is capturing stdout/stderr it writes the log files itself,
    # so the Python side must NOT also write them (no duplicate lines).
    if os.environ.get("KLIPZY_ELECTRON_CAPTURE") != "1":
        log_dir = _logs_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            info_handler = logging.handlers.RotatingFileHandler(
                log_dir / "server.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
            )
            info_handler.setLevel(logging.INFO)
            info_handler.setFormatter(formatter)
            logger.addHandler(info_handler)

            error_handler = logging.handlers.RotatingFileHandler(
                log_dir / "server-error.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
            )
            error_handler.setLevel(logging.WARNING)
            error_handler.addFilter(logging.Filter("klipzy"))
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)
        except OSError as exc:
            logger.warning("Could not create log files in %s: %s", log_dir, exc)

    logger.info("=" * 60)
    logger.info(
        "Klipzy Studio server started at %s (log dir: %s)",
        datetime.now().isoformat(timespec="seconds"),
        _logs_dir(),
    )
    return logger


def get_logger(name: str) -> logging.Logger:
    """Child logger sharing the configured handlers (klipzy.<name>)."""
    return logging.getLogger(f"klipzy.{name}")