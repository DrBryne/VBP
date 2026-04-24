import logging
import os


def get_logger(name="vbp_workflow"):
    """
    Returns a standard python logger.
    The actual formatting and Cloud syncing is handled by OpenTelemetry
    in app/app_utils/telemetry.py via the LoggingHandler.
    """
    logger = logging.getLogger(name)

    # We do not add a StreamHandler here. OpenTelemetry's LoggingHandler
    # captures these logs and forwards them to GCP or Console.

    # Set level from environment variable
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    return logger

class VBPLogger:
    """
    Convenience wrapper to maintain backward compatibility with existing codebase
    that passes kwargs (like uri=...) to logger methods.
    OpenTelemetry handles these extra context fields natively if configured.
    """
    def __init__(self, name="vbp_workflow"):
        self.logger = get_logger(name)

    def _log(self, level, msg, **kwargs):
        # We append kwargs to the string so they are visible in simple log streams
        if kwargs:
            ctx = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            msg = f"{msg} [{ctx}]"

        # --- SAFE TRUNCATION ---
        # Cloud Logging has a 256KB limit per entry.
        # Large prompts/responses can easily exceed this and cause overhead.
        # We truncate at 100k characters to be safe.
        limit = 100000
        if len(msg) > limit:
            msg = msg[:limit] + "... [TRUNCATED]"

        self.logger.log(level, msg)

    def debug(self, msg, **kwargs): self._log(logging.DEBUG, msg, **kwargs)
    def info(self, msg, **kwargs): self._log(logging.INFO, msg, **kwargs)
    def warning(self, msg, **kwargs): self._log(logging.WARNING, msg, **kwargs)
    def error(self, msg, **kwargs): self._log(logging.ERROR, msg, **kwargs)
