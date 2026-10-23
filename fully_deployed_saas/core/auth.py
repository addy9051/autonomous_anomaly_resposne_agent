"""
Multi-Tenant Authentication & Authorization Middleware.
Supports:
1. Tenant API Keys (for telemetry webhooks, CLI, and monitoring integrations)
2. Bearer Tokens / JWTs (for dashboard sessions)
3. Development mode fallback
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from core.config import get_settings
from core.schemas import TenantContext
from core.security import hash_api_key
from core.utils import get_logger
from database.connection import execute, fetch_one

logger = get_logger("auth")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_auth = HTTPBearer(auto_error=False)


async def get_current_tenant(
    request: Request,
    api_key: str | None = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_auth),
) -> TenantContext:
    """
    Validate the incoming request's organization/tenant identity.
    Checks:
    1. 'X-API-Key' header
    2. 'Authorization: Bearer <key>'
    3. In development mode with no keys: falls back to default 'tenant-demo'
    """
    settings = get_settings()
    raw_token = api_key or (bearer.credentials if bearer else None)

    # 1. Authenticate with API Key
    if raw_token:
        key_hash = hash_api_key(raw_token)
        query = """
            SELECT k.tenant_id, k.name, t.status, t.plan
            FROM tenant_api_keys k
            JOIN tenants t ON t.id = k.tenant_id
            WHERE k.key_hash = $1 AND k.is_revoked = FALSE
        """
        row = await fetch_one(query, key_hash)
        if row:
            if row["status"] != "active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Organization account is suspended or inactive",
                )
            # Async record last used timestamp
            try:
                await execute("UPDATE tenant_api_keys SET last_used_at = NOW() WHERE key_hash = $1", key_hash)
            except Exception:
                pass

            ctx = TenantContext(
                tenant_id=row["tenant_id"],
                user_id=f"key:{row['name']}",
                role="sre",
                key_name=row["name"],
            )
            request.state.tenant = ctx
            return ctx

        # If token was provided but not found, reject
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # 2. Local Development Fallback
    if settings.app_env == "development":
        ctx = TenantContext(
            tenant_id="tenant-demo",
            user_id="dev-sre",
            role="admin",
            key_name="Development Mode",
        )
        request.state.tenant = ctx
        return ctx

    # 3. Production Missing Credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication credentials (provide X-API-Key or Authorization Bearer header)",
        headers={"WWW-Authenticate": "ApiKey"},
    )


TenantDep = Annotated[TenantContext, Depends(get_current_tenant)]
