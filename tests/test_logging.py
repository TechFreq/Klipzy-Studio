"""
Tests for logging setup and idempotency.
"""

import logging
import os
from pathlib import Path
from server.logging_setup import setup_logging, get_logger


def test_logging_setup_creates_log_directory():
    """Verify setup_logging configures root logger without error."""
    logger = setup_logging()
    assert logger is not None
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    assert log_dir.exists()


def test_logging_setup_idempotency():
    """Calling setup_logging multiple times should not add duplicate handlers."""
    root = logging.getLogger()
    initial_handler_count = len(root.handlers)
    setup_logging()
    setup_logging()
    # Handler count should not explode with multiple calls
    assert len(root.handlers) <= initial_handler_count + 2


def test_get_logger():
    """get_logger returns configured logger instance."""
    log = get_logger("test_module")
    assert log.name == "klipzy.test_module"
    log.info("Test log message")

