"""
Knowledge Base & Runbook Ingestion Routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from core.auth import TenantDep
from core.utils import get_logger
from knowledge.ingestion import seed_tenant_default_runbooks
from knowledge.vector_store import MultiTenantRAG

logger = get_logger("runbook_routes")
router = APIRouter(prefix="/runbooks", tags=["Knowledge Base & Runbooks"])


class IngestRunbookRequest(BaseModel):
    title: str
    content: str
    collection: str = "application"
    service_tags: list[str] = []


class SearchRunbookRequest(BaseModel):
    query: str
    collection: str | None = None
    top_k: int = 5


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_runbook(
    req: IngestRunbookRequest,
    tenant: TenantDep,
) -> dict[str, Any]:
    """Upload and embed a custom SRE runbook for the tenant."""
    rag = MultiTenantRAG(tenant.tenant_id)
    doc_id = await rag.ingest_document(
        title=req.title,
        content=req.content,
        collection=req.collection,
        service_tags=req.service_tags,
    )
    return {"status": "ingested", "document_id": doc_id, "title": req.title}


@router.post("/search")
async def search_runbooks(
    req: SearchRunbookRequest,
    tenant: TenantDep,
) -> list[dict[str, Any]]:
    """Hybrid search across the tenant's runbooks."""
    rag = MultiTenantRAG(tenant.tenant_id)
    return await rag.hybrid_search(
        query=req.query,
        collection=req.collection,
        top_k=req.top_k,
    )


@router.post("/seed-defaults", status_code=status.HTTP_201_CREATED)
async def seed_defaults(tenant: TenantDep) -> dict[str, Any]:
    """Seed standard industry SRE runbooks into the tenant's vector store."""
    count = await seed_tenant_default_runbooks(tenant.tenant_id)
    return {"status": "seeded", "runbooks_added": count}
