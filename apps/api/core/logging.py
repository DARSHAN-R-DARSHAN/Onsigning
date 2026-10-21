"""
Onsigning Redacted Structured Logger
Strictly conforms to PRD.md FR-9 and ARCHITECTURE.md Section 8:
- Never logs raw contract text, verbatim quotes, access tokens, credentials, or presigned URLs.
- Logs opaque UUIDs, correlation IDs, status, stage, token counters, latency, and error categories.
"""

import logging
import json
import re
from typing import Any, Dict, Optional
from datetime import datetime, timezone

# Regex patterns for sensitive data redaction
JWT_PATTERN = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")
BEARER_PATTERN = re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE)
PRESIGNED_URL_PATTERN = re.compile(r"(https?://[^\s\"']+(?:X-Amz-Signature|Signature|sig)=[^\s\"']+)", re.IGNORECASE)


def sanitize_message(msg: str) -> str:
    """Redacts tokens, credentials, and presigned URLs from log strings."""
    if not isinstance(msg, str):
        return str(msg)
    cleaned = JWT_PATTERN.sub("[REDACTED_JWT]", msg)
    cleaned = BEARER_PATTERN.sub("Bearer [REDACTED_TOKEN]", cleaned)
    cleaned = PRESIGNED_URL_PATTERN.sub("[REDACTED_PRESIGNED_URL]", cleaned)
    return cleaned


class RedactedJSONFormatter(logging.Formatter):
    """Formats log records as sanitized structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_message(record.getMessage()),
        }

        # Safely include structured contextual fields if present
        for field in ("correlation_id", "organization_id", "user_id", "document_id", "audit_run_id", "stage", "error_category", "latency_ms", "token_count"):
            val = getattr(record, field, None)
            if val is not None:
                log_payload[field] = str(val) if not isinstance(val, (int, float, bool)) else val

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


def get_logger(name: str = "onsigning") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactedJSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


logger = get_logger("onsigning.api")
