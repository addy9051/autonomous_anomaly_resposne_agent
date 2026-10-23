"""
Runbook Ingestion & Default Template Seeding for Tenants.
"""

from __future__ import annotations

from core.utils import get_logger
from knowledge.vector_store import MultiTenantRAG

logger = get_logger("runbook_ingestion")

DEFAULT_SRE_RUNBOOKS = [
    {
        "title": "High Memory & Connection Saturation (Database)",
        "collection": "database",
        "service_tags": ["postgres", "mysql", "redis", "database"],
        "content": """
### Database Connection Saturation Runbook
- **Symptoms:** Active connections reach >90% of max_connections, query latency spikes >500ms, connection timeout errors.
- **Root Cause Hypotheses:**
  1. Connection pool leakage in application pods after recent deployment.
  2. Long-running analytical query holding table locks without transaction timeout.
  3. Slow queries causing thread queueing and memory exhaustion.
- **Recommended Remediation:**
  - Tier 1: Restart leaking client deployment pods to drop hung connections.
  - Tier 2: Terminate idle-in-transaction sessions older than 5 minutes.
  - Tier 3: Escalate to DBA if persistent hardware or IOPS exhaustion.
"""
    },
    {
        "title": "Pod CrashLoopBackOff & OOMKilled Response",
        "collection": "application",
        "service_tags": ["kubernetes", "payment-service", "api", "worker"],
        "content": """
### OOMKilled Application Container Runbook
- **Symptoms:** Kubernetes event OOMKilled, Pod status CrashLoopBackOff, container exit code 137.
- **Root Cause Hypotheses:**
  1. Memory leak in JVM / Node / Python process under sudden traffic burst.
  2. Container memory request/limit configured too low for load.
  3. Large unindexed payload unmarshalled in memory.
- **Recommended Remediation:**
  - Tier 1: Restart unhealthy pod to release buffer memory.
  - Tier 1: Scale deployment replicas +2 to distribute concurrency across nodes.
  - Tier 2: Temporarily bump memory limit by 25% via patch deployment.
"""
    },
    {
        "title": "High HTTP 5xx Error Rate & Upstream Gateway Timeout",
        "collection": "network",
        "service_tags": ["ingress", "nginx", "api-gateway", "envoy"],
        "content": """
### Ingress & Network Timeout Runbook
- **Symptoms:** Ingress 502 Bad Gateway or 504 Gateway Timeout rate >5%, p99 latency >2s.
- **Root Cause Hypotheses:**
  1. Upstream backend service pods failing health checks or thread-locked.
  2. DNS resolution failure between ingress controller and service mesh.
  3. Network policy misconfiguration blocking egress.
- **Recommended Remediation:**
  - Tier 1: Clear application redis cache if upstream cache miss storm.
  - Tier 1: Scale upstream service replicas.
  - Tier 2: Rollback latest deployment if correlated with recent release sha.
"""
    },
]


async def seed_tenant_default_runbooks(tenant_id: str) -> int:
    """Seed tenant's knowledge base with standard industry SRE runbooks."""
    rag = MultiTenantRAG(tenant_id)
    count = 0
    for r in DEFAULT_SRE_RUNBOOKS:
        await rag.ingest_document(
            title=r["title"],
            content=r["content"],
            collection=r["collection"],
            service_tags=r["service_tags"],
            metadata={"source": "system_template"},
        )
        count += 1
    logger.info("seeded_tenant_runbooks", tenant_id=tenant_id, count=count)
    return count
