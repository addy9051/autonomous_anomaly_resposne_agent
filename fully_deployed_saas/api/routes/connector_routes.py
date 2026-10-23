"""
Telemetry Connector Management Routes (Encrypted in PostgreSQL).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from connectors.manager import TenantConnectorManager
from core.auth import TenantDep
from core.utils import get_logger

logger = get_logger("connector_routes")
router = APIRouter(prefix="/connectors", tags=["Telemetry Connectors"])


class SaveConnectorRequest(BaseModel):
    vendor: str
    name: str = ""
    credentials: dict[str, Any]


@router.get("")
async def list_connectors(tenant: TenantDep) -> list[dict[str, Any]]:
    """List active telemetry connectors (secrets are never returned)."""
    mgr = TenantConnectorManager(tenant.tenant_id)
    return await mgr.list_connectors()


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_connector(
    req: SaveConnectorRequest,
    tenant: TenantDep,
) -> dict[str, Any]:
    """Save and encrypt vendor credentials in PostgreSQL."""
    mgr = TenantConnectorManager(tenant.tenant_id)
    await mgr.save_connector(
        vendor=req.vendor,
        name=req.name or f"{req.vendor.capitalize()} Connector",
        credentials=req.credentials,
    )
    return {"status": "saved", "vendor": req.vendor}


@router.delete("/{vendor}")
async def delete_connector(
    vendor: str,
    tenant: TenantDep,
) -> dict[str, Any]:
    """Remove a connector configuration."""
    mgr = TenantConnectorManager(tenant.tenant_id)
    success = await mgr.delete_connector(vendor)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    return {"status": "deleted", "vendor": vendor}
