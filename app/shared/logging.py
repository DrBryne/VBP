import json
import logging
import os
import sys
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """Formats log records as JSON for Cloud Logging compatibility."""
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Add extra fields if they exist
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        # Capture exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)

def get_logger(name="vbp_workflow"):
    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)

        # Use structured logging if running in a container/cloud,
        # otherwise use a readable format for local dev.
        # You can force JSON by setting LOG_FORMAT=json
        if os.environ.get("LOG_FORMAT") == "json":
            handler.setFormatter(StructuredFormatter())
        else:
            # Custom formatter that appends extra fields to the message for local readability
            class ContextFormatter(logging.Formatter):
                def format(self, record):
                    msg = super().format(record)
                    if hasattr(record, "extra_fields") and record.extra_fields:
                        ctx = ", ".join([f"{k}={v}" for k, v in record.extra_fields.items()])
                        msg = f"{msg} [{ctx}]"
                    return msg

            formatter = ContextFormatter(
                "[%(levelname)s] %(asctime)s - %(name)s - %(message)s"
            )
            handler.setFormatter(formatter)

        logger.addHandler(handler)

        # Set level from environment variable
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logger.setLevel(level)

    return logger

# Convenience class for logging with extra context
class VBPLogger:
    def __init__(self, name="vbp_workflow"):
        self.logger = get_logger(name)

    def _log(self, level, msg, **kwargs):
        extra = {"extra_fields": kwargs}
        self.logger.log(level, msg, extra=extra)

    def debug(self, msg, **kwargs): self._log(logging.DEBUG, msg, **kwargs)
    def info(self, msg, **kwargs): self._log(logging.INFO, msg, **kwargs)
    def warning(self, msg, **kwargs): self._log(logging.WARNING, msg, **kwargs)
    def error(self, msg, **kwargs): self._log(logging.ERROR, msg, **kwargs)
