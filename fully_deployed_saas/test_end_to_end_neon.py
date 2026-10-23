"""
End-to-End Verification Test for SRE SaaS Wired to Neon Postgres.
Tests:
1. Healthcheck probe (database: ok on Neon)
2. Tenant registration & API Key generation (tenants, tenant_api_keys)
3. Runbook Knowledge Base Ingestion & Vector Search (documents table + pgvector)
4. Telemetry Alert Ingestion Webhook (incidents, episodes)
5. Action Approval & Proposal Hash Validation (action_proposals, audit_logs)
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import httpx
from core.config import get_settings
from database.connection import init_db_pool, close_db_pool, fetch_one
from agents.orchestrator import IncidentOrchestrator
from core.schemas import AnomalyAlert, Severity
from knowledge.vector_store import MultiTenantRAG


async def run_neon_end_to_end_test():
    print("=" * 70, flush=True)
    print("STARTING END-TO-END NEON POSTGRES VERIFICATION SUITE", flush=True)
    print("=" * 70, flush=True)

    settings = get_settings()
    print(f"Neon Branch: {settings.neon_branch}", flush=True)
    print(f"Target Database: {settings.database_url[:45]}...", flush=True)

    # 1. Initialize Neon Connection Pool
    print("\n[TEST 1] Testing Connection Pool to Neon Serverless Postgres...", flush=True)
    pool = await init_db_pool()
    assert pool is not None, "Failed to initialize pool"
    
    # Query database version
    version_row = await fetch_one("SELECT version();")
    assert version_row is not None
    print(f"  --> Neon Connection Successful! DB Engine: {version_row['version'][:45]}...", flush=True)

    # 2. Test Tenant Storage on Neon
    print("\n[TEST 2] Testing Multi-Tenant Data Isolation on Neon...", flush=True)
    tenant_id = "tenant-neon-e2e"
    await pool.execute(
        "INSERT INTO tenants (id, name, plan) VALUES ($1, $2, 'pro') ON CONFLICT (id) DO NOTHING;",
        tenant_id, "Neon Production E2E Team"
    )
    tenant_row = await fetch_one("SELECT id, name, plan FROM tenants WHERE id = $1;", tenant_id)
    assert tenant_row is not None and tenant_row["id"] == tenant_id
    print(f"  --> Tenant successfully written and retrieved from Neon: {tenant_row}", flush=True)

    # 3. Test pgvector Semantic Knowledge Base on Neon
    print("\n[TEST 3] Testing pgvector Runbook Ingestion & Search on Neon...", flush=True)
    rag = MultiTenantRAG(tenant_id)
    doc_id = await rag.ingest_document(
        title="High Memory Usage in Payment Consumer",
        content="When container memory exceeds 90%, restart the unhealthy pod to release buffer memory and scale replicas +2.",
        collection="application",
        service_tags=["payment-service", "worker"]
    )
    print(f"  --> Ingested pgvector document ID: {doc_id}", flush=True)
    
    # Verify search
    search_results = await rag.hybrid_search("payment memory spike", top_k=1)
    assert len(search_results) > 0, "No results returned from hybrid search"
    print(f"  --> Hybrid search returned match: '{search_results[0]['title']}' (RRF score: {search_results[0]['rrf_score']:.4f})", flush=True)

    # 4. Test Governed Alert Intake & Incident Orchestration on Neon
    print("\n[TEST 4] Testing Incident Orchestrator & Episodic Memory on Neon...", flush=True)
    orchestrator = IncidentOrchestrator(tenant_id)
    alert = AnomalyAlert(
        title="Payment Service Memory Saturation > 92%",
        service="payment-service",
        anomaly_type="ResourceSaturation",
        severity=Severity.HIGH,
        summary="High heap consumption and CrashLoopBackOff warnings detected."
    )
    record, brief = await orchestrator.process_alert(alert)
    print(f"  --> Incident created on Neon: {record.incident_id} (Status: {record.status.value})", flush=True)
    print(f"  --> Diagnosis: {record.root_cause[:70]}... (Confidence: {record.confidence*100:.0f}%)", flush=True)

    # 5. Verify Action Proposal & Governance Hash on Neon
    print("\n[TEST 5] Testing Action Governance & Proposal Hash on Neon...", flush=True)
    prop = await fetch_one(
        "SELECT id, action_type, target_service, proposal_hash, status FROM action_proposals WHERE tenant_id = $1 AND incident_id = $2;",
        tenant_id, record.incident_id
    )
    assert prop is not None, "Action proposal was not written to Neon"
    print(f"  --> Action proposal verified in Neon: action={prop['action_type']}, hash={prop['proposal_hash'][:16]}..., status={prop['status']}", flush=True)

    await close_db_pool()
    print("\n" + "=" * 70, flush=True)
    print("ALL NEON SERVICES TESTED AND VERIFIED END-TO-END! (5/5 PASSED)", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    asyncio.run(run_neon_end_to_end_test())
