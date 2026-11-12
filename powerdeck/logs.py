"""File logging to ~/.local/state/powerdeck/logs/powerdeck.log (best effort)."""
import logging
from logging.handlers import RotatingFileHandler

from . import paths


def setup_logging() -> None:
    logger = logging.getLogger("powerdeck")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(paths.logs_dir() / "powerdeck.log", maxBytes=500_000, backupCount=3)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
    except OSError:
        logger.addHandler(logging.NullHandler())  # never crash because of logging
