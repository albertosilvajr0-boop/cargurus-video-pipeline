"""Structured logging for the video pipeline.

Provides a configured logger that writes to both console (via Rich) and a
rotating log file.  Every module should use:

    from utils.logger import get_logger
    logger = get_logger(__name__)

Log files are stored in the project's `logs/` directory.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "pipeline.log"

# ---------------------------------------------------------------------------
# Shared formatter for file output (includes timestamp, level, module)
# ---------------------------------------------------------------------------
_FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# One-time root logger configuration
# ---------------------------------------------------------------------------
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger("pipeline")
    root.setLevel(logging.DEBUG)

    # --- File handler (rotating, max 5 MB x 3 backups) ---
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(file_handler)

    # --- Console handler via Rich (INFO+ only to keep console clean) ---
    console_handler = RichHandler(
        level=logging.INFO,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``pipeline`` namespace."""
    _configure_root()
    return logging.getLogger(f"pipeline.{name}")
