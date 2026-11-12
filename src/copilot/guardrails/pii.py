"""PII redaction used for logs, traces and the audit trail.

Pattern-based on purpose: it is deterministic, cheap and good enough to keep obvious identifiers
(email addresses, phone numbers, IBANs) out of telemetry. It is not a substitute for a managed
PII-detection service on free text that may contain names or addresses.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"), "[REDACTED_IBAN]"),
)
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d ()-]{7,}\d(?![\w-])")
_MIN_PHONE_DIGITS = 9  # keeps ISO dates (8 digits) and short identifiers out of the phone rule


def _redact_phone(match: re.Match[str]) -> str:
    digits = sum(character.isdigit() for character in match.group(0))
    return "[REDACTED_PHONE]" if digits >= _MIN_PHONE_DIGITS else match.group(0)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _PHONE.sub(_redact_phone, text)


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside JSON-like structures."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value
