import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(log_path: str = "logs/bot.log", level: int = logging.INFO) -> logging.Logger:
    """Console + rotating file logger shared by the whole bot."""
    logger = logging.getLogger("capital_bot")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_path:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
