"""Central logging configuration shared by the CLI and GUI entry points.

There is deliberately no ``logging.conf`` and no third-party logging library:
the entry points call :func:`configure_logging` exactly once, before any module
logger emits a record, and every module elsewhere uses the uniform
``logger = logging.getLogger(__name__)`` convention.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "app.log"


def configure_logging() -> None:
    """Configure the root logger once for the whole process.

    In a normal (``__debug__``) run, DEBUG output goes to the console only. In
    an optimized (``python -O``) run, DEBUG output goes to a timed rotating
    file instead, keeping seven hourly backups.
    """
    if __debug__:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT)
    else:
        handler = TimedRotatingFileHandler(
            LOG_FILE,
            when="h",
            backupCount=7,
            encoding="utf-8",
        )
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
