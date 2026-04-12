"""
PII Masking Middleware — sanitizes sensitive data before LLM calls.

Applies regex-based tokenization to prevent leaking PII
(card numbers, emails, IPs, merchant identifiers) into LLM prompts.
All masking is one-way — original values are NOT recoverable.

Architecture Reference: Phase 02 — Cross-Cutting: Security
"""

from __future__ import annotations

import re
from typing import Any

# ─── Regex Patterns ──────────────────────────────────────────────

# Credit/debit card numbers: 13–19 digit sequences (with optional separators)
_CARD_NUMBER_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

# PAN-style masking: keep first 6 and last 4 digits visible
_CARD_STRICT_RE = re.compile(r"\b(\d{4})[- ]?(\d{2})\d{2}[- ]?\d{4}[- ]?(\d{4})\b")

# Email addresses
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

# IPv4 addresses
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# IPv6 addresses (simplified — catches most common formats)
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")

# SSN-like patterns (US format: XXX-XX-XXXX)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Phone numbers (various formats)
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b")

# Merchant IDs (common alphanumeric patterns, e.g., MID-12345, MERCH_ABC123)
_MERCHANT_ID_RE = re.compile(
    r"\b(?:MID|MERCH|merchant_id|merchant)[_\-:]?\s*[A-Za-z0-9_-]{6,20}\b",
    re.IGNORECASE,
)

# API keys / tokens (long hex or base64 strings that look like secrets)
_API_KEY_RE = re.compile(
    r"\b(?:sk|pk|api[_-]?key|token|secret)[_\-:]?\s*[A-Za-z0-9_\-]{20,}\b",
    re.IGNORECASE,
)


# ─── Core Sanitization ──────────────────────────────────────────


def sanitize_for_llm(text: str) -> str:
    """
    Sanitize a text string by masking PII before sending to an LLM.

    Applies the following transformations (in order):
      1. Credit card numbers → [CARD_****XXXX]
      2. SSN-like patterns → [SSN_REDACTED]
      3. Email addresses → [EMAIL_REDACTED]
      4. IPv4/IPv6 addresses → [IP_REDACTED]
      5. Phone numbers → [PHONE_REDACTED]
      6. Merchant IDs → [MERCHANT_ID_REDACTED]
      7. API keys/tokens → [SECRET_REDACTED]

    Args:
        text: Raw string that may contain PII.

    Returns:
        Sanitized string with PII tokens replaced.
    """
    if not text:
        return text

    # 1. Card numbers — keep last 4 digits for context
    result = _CARD_STRICT_RE.sub(r"[CARD_\1XX_XXXX_\3]", text)
    # Catch remaining loose digit sequences that look like cards
    result = _CARD_NUMBER_RE.sub("[CARD_REDACTED]", result)

    # 2. SSN
    result = _SSN_RE.sub("[SSN_REDACTED]", result)

    # 3. Email
    result = _EMAIL_RE.sub("[EMAIL_REDACTED]", result)

    # 4. IP addresses
    result = _IPV4_RE.sub("[IP_REDACTED]", result)
    result = _IPV6_RE.sub("[IP_REDACTED]", result)

    # 5. Phone numbers
    result = _PHONE_RE.sub("[PHONE_REDACTED]", result)

    # 6. Merchant IDs
    result = _MERCHANT_ID_RE.sub("[MERCHANT_ID_REDACTED]", result)

    # 7. API keys / secrets
    result = _API_KEY_RE.sub("[SECRET_REDACTED]", result)

    return result


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively sanitize all string values in a dictionary.

    Useful for sanitizing entire telemetry event payloads before
    serializing them into LLM prompts.
    """
    sanitized = {}
    for key, value in data.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_for_llm(value)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_dict(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_dict(item)
                if isinstance(item, dict)
                else sanitize_for_llm(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized
