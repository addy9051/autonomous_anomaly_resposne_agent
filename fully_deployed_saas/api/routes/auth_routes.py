"""
Tenant Registration & API Key Management Routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core.auth import TenantDep
from core.security import generate_tenant_api_key
from core.utils import get_logger
from database.connection import execute, fetch_all, fetch_one
from knowledge.ingestion import seed_tenant_default_runbooks

logger = get_logger("auth_routes")
router = APIRouter(prefix="/auth", tags=["Authentication & Tenants"])


class RegisterTenantRequest(BaseModel):
    organization_name: str
    plan: str = "starter"


class CreateKeyRequest(BaseModel):
    name: str = "Live Webhook Key"


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_tenant(req: RegisterTenantRequest) -> dict[str, Any]:
    """Register a new SaaS organization and receive the initial API Key."""
    import re
    # Create clean tenant id from name
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", req.organization_name.strip().lower()).strip("-")
    tenant_id = f"tenant-{slug[:24]}"

    # Insert tenant
    query = """
        INSERT INTO tenants (id, name, plan)
        VALUES ($1, $2, $3)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
        RETURNING id, name, plan
    """
    row = await fetch_one(query, tenant_id, req.organization_name, req.plan)
    assert row is not None

    # Generate initial API Key
    raw_key, key_hash, key_prefix = generate_tenant_api_key(prefix="sre_live")
    await execute(
        """
        INSERT INTO tenant_api_keys (tenant_id, key_hash, key_prefix, name)
        VALUES ($1, $2, $3, 'Initial Admin Key')
        """,
        tenant_id,
        key_hash,
        key_prefix,
    )

    # Automatically seed industry standard runbooks for the new tenant
    await seed_tenant_default_runbooks(tenant_id)

    return {
        "tenant_id": row["id"],
        "organization_name": row["name"],
        "plan": row["plan"],
        "api_key": raw_key,
        "message": "Save this API key immediately! It will not be shown again.",
    }


@router.get("/keys")
async def list_api_keys(tenant: TenantDep) -> list[dict[str, Any]]:
    """List API keys for the current tenant (raw secrets are never returned)."""
    query = """
        SELECT id, name, key_prefix, is_revoked, last_used_at, created_at
        FROM tenant_api_keys
        WHERE tenant_id = $1
        ORDER BY created_at DESC
    """
    rows = await fetch_all(query, tenant.tenant_id)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "key_prefix": f"{r['key_prefix']}****",
            "is_revoked": r["is_revoked"],
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(req: CreateKeyRequest, tenant: TenantDep) -> dict[str, Any]:
    """Generate an additional API key for CI/CD, webhooks, or agents."""
    raw_key, key_hash, key_prefix = generate_tenant_api_key(prefix="sre_live")
    await execute(
        """
        INSERT INTO tenant_api_keys (tenant_id, key_hash, key_prefix, name)
        VALUES ($1, $2, $3, $4)
        """,
        tenant.tenant_id,
        key_hash,
        key_prefix,
        req.name,
    )
    return {
        "name": req.name,
        "key_prefix": key_prefix,
        "api_key": raw_key,
        "message": "Copy this key now. It will not be visible again.",
    }
