"""
Security & Cryptography Utilities for Multi-Tenant Secrets & Action Governance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet

from core.config import get_settings


def get_fernet() -> Fernet:
    """Return Fernet cipher initialized with the master key."""
    settings = get_settings()
    key = settings.encryption_master_key.encode("utf-8")
    # Validate base64 length (Fernet keys are 32 url-safe base64 encoded bytes = 44 chars)
    if len(key) != 44:
        # Normalize or derive 32-byte key
        digest = hashlib.sha256(key).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plain_text: str | dict[str, Any]) -> str:
    """Encrypt a secret string or dictionary into an encrypted string."""
    if isinstance(plain_text, dict):
        plain_text = json.dumps(plain_text)
    cipher = get_fernet()
    return cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher_text: str) -> str:
    """Decrypt an encrypted ciphertext into its plaintext string."""
    cipher = get_fernet()
    return cipher.decrypt(cipher_text.encode("utf-8")).decode("utf-8")


def decrypt_secret_json(cipher_text: str) -> dict[str, Any]:
    """Decrypt an encrypted ciphertext directly into a JSON dictionary."""
    decrypted = decrypt_secret(cipher_text)
    return json.loads(decrypted)


def hash_api_key(raw_key: str) -> str:
    """Hash an API key using SHA-256 for secure database lookup."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def generate_tenant_api_key(prefix: str = "sre_live") -> tuple[str, str, str]:
    """
    Generate a new high-entropy API key.
    Returns: (raw_key, key_hash, key_prefix)
    Only the raw_key is shown to the user once.
    """
    token = secrets.token_urlsafe(32)
    raw_key = f"{prefix}_{token}"
    key_hash = hash_api_key(raw_key)
    return raw_key, key_hash, f"{prefix}_{token[:4]}"


def compute_action_proposal_hash(
    incident_id: str,
    action_type: str,
    target_service: str,
    parameters: dict[str, Any],
) -> str:
    """
    Compute a deterministic SHA-256 hash of the proposed action parameters.
    Ensures that when an SRE approves an action in UI/Slack, the exact parameters
    cannot be altered or forged.
    """
    canonical_payload = {
        "incident_id": incident_id,
        "action_type": action_type,
        "target_service": target_service,
        "parameters": parameters,
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
