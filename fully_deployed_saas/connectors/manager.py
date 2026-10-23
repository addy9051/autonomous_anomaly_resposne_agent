"""
Multi-Tenant Encrypted Telemetry Connector Store.
Persists credentials (Datadog API keys, Prometheus tokens) encrypted in Postgres.
"""

from __future__ import annotations

from typing import Any

from core.security import decrypt_secret_json, encrypt_secret
from core.utils import get_logger
from database.connection import execute, fetch_all, fetch_one

logger = get_logger("connector_manager")


class TenantConnectorManager:
    """Manages encrypted vendor credentials per tenant."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    async def save_connector(
        self,
        vendor: str,
        name: str,
        credentials: dict[str, Any],
    ) -> None:
        """Encrypt and save vendor credentials in PostgreSQL."""
        vendor = vendor.lower().strip()
        encrypted = encrypt_secret(credentials)

        query = """
            INSERT INTO tenant_connectors (
                tenant_id, vendor, name, config_encrypted, is_active, updated_at
            ) VALUES (
                $1, $2, $3, $4, TRUE, NOW()
            ) ON CONFLICT (tenant_id, vendor) DO UPDATE SET
                name = EXCLUDED.name,
                config_encrypted = EXCLUDED.config_encrypted,
                is_active = TRUE,
                updated_at = NOW();
        """
        await execute(query, self.tenant_id, vendor, name, encrypted)
        logger.info("connector_saved", tenant_id=self.tenant_id, vendor=vendor)

    async def get_connector_credentials(self, vendor: str) -> dict[str, Any] | None:
        """Fetch and decrypt connector credentials."""
        vendor = vendor.lower().strip()
        query = """
            SELECT config_encrypted FROM tenant_connectors
            WHERE tenant_id = $1 AND vendor = $2 AND is_active = TRUE
        """
        row = await fetch_one(query, self.tenant_id, vendor)
        if not row:
            return None
        try:
            return decrypt_secret_json(row["config_encrypted"])
        except Exception as exc:
            logger.error("connector_decrypt_failed", tenant_id=self.tenant_id, vendor=vendor, error=str(exc))
            return None

    async def list_connectors(self) -> list[dict[str, Any]]:
        """List active connectors without exposing secret values."""
        query = """
            SELECT vendor, name, is_active, updated_at
            FROM tenant_connectors
            WHERE tenant_id = $1
            ORDER BY vendor ASC
        """
        rows = await fetch_all(query, self.tenant_id)
        return [
            {
                "vendor": r["vendor"],
                "name": r["name"],
                "is_active": r["is_active"],
                "is_configured": True,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]

    async def delete_connector(self, vendor: str) -> bool:
        """Delete a connector configuration."""
        vendor = vendor.lower().strip()
        res = await execute(
            "DELETE FROM tenant_connectors WHERE tenant_id = $1 AND vendor = $2",
            self.tenant_id,
            vendor,
        )
        return "DELETE" in res
