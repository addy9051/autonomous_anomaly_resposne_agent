"""
Document Ingestion Pipeline.

Processes runbook documents through:
1. Document loading (Confluence API, local markdown, manual input)
2. Recursive character splitting (512 tokens, 64 overlap)
3. Metadata tagging (source, severity, service_tags)
4. Embedding generation (text-embedding-004 / text-embedding-3-small)
5. Upsert to pgvector in PostgreSQL
"""

from __future__ import annotations

import hashlib
from typing import Any

import asyncpg
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger("ingestion_pipeline")


class RunbookIngestionPipeline:
    """
    Ingests runbook documents into the pgvector knowledge base.

    Supports:
    - Local markdown files
    - Manual text input
    - (Production) Confluence API polling
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings.agent.embedding_chunk_size,
            chunk_overlap=self.settings.agent.embedding_chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=self.settings.llm.openai_api_key,
        )
        logger.info("ingestion_pipeline_initialized")

    async def ingest_document(
        self,
        title: str,
        content: str,
        source: str = "manual",
        service_tags: list[str] | None = None,
        severity_relevance: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Ingest a single document into the knowledge base.

        Args:
            title: Document title
            content: Full document text
            source: Source type (manual, confluence, notion)
            service_tags: Services this document relates to
            severity_relevance: Severity levels this document covers
            metadata: Additional metadata dict

        Returns:
            List of generated document chunk IDs
        """
        # Split into chunks
        chunks = self.splitter.split_text(content)
        logger.info("document_chunked", title=title, num_chunks=len(chunks))

        # Generate embeddings for all chunks
        embeddings = await self.embeddings.aembed_documents(chunks)
        logger.info("embeddings_generated", count=len(embeddings))

        # Upsert to database
        chunk_ids = []
        conn = await asyncpg.connect(self.settings.data.postgres_dsn)
        try:
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=False)):
                doc_id = self._generate_doc_id(title, i)
                chunk_ids.append(doc_id)

                await conn.execute(
                    """
                    INSERT INTO documents (doc_id, title, source, content, chunk_index,
                                           total_chunks, embedding, metadata,
                                           service_tags, severity_relevance)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    """,
                    doc_id,
                    title,
                    source,
                    chunk,
                    i,
                    len(chunks),
                    str(embedding),  # pgvector accepts string representation
                    metadata or {},
                    service_tags or [],
                    severity_relevance or [],
                )

            logger.info("document_ingested", title=title, chunks=len(chunk_ids))
        finally:
            await conn.close()

        return chunk_ids

    async def ingest_sample_runbooks(self) -> int:
        """
        Ingest sample runbooks for development and testing.
        Returns number of documents ingested.
        """
        sample_runbooks = self._get_sample_runbooks()
        total_chunks = 0

        for runbook in sample_runbooks:
            chunk_ids = await self.ingest_document(
                title=runbook["title"],
                content=runbook["content"],
                source="manual",
                service_tags=runbook.get("service_tags", []),
                severity_relevance=runbook.get("severity_relevance", []),
            )
            total_chunks += len(chunk_ids)

        logger.info("sample_runbooks_ingested", total_docs=len(sample_runbooks), total_chunks=total_chunks)
        return total_chunks

    def _generate_doc_id(self, title: str, chunk_index: int) -> str:
        """Generate a deterministic document ID for deduplication."""
        raw = f"{title}::chunk_{chunk_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _get_sample_runbooks(self) -> list[dict[str, Any]]:
        """Sample runbooks for development."""
        return [
            {
                "title": "Payment Gateway Latency Spike Investigation",
                "service_tags": ["payment-gateway", "api-gateway"],
                "severity_relevance": ["critical", "high"],
                "content": """# Payment Gateway Latency Spike Investigation

## Symptoms
- p99 latency exceeds 1000ms (baseline: ~250ms)
- Increased timeout errors from upstream clients
- Customer complaints about slow checkout

## Investigation Steps

### Step 1: Check Current Metrics
Query Prometheus for payment gateway latency:
```
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="payment-gateway"}[5m]))
```

### Step 2: Check Database Connection Pool
- Query: `SELECT count(*) FROM pg_stat_activity WHERE state = 'active';`
- If connections > 80% of max_connections, proceed to Step 3

### Step 3: Identify Slow Queries
- Check `pg_stat_statements` for queries with mean_time > 500ms
- Look for missing indexes on frequently queried columns
- Check for lock contention: `SELECT * FROM pg_locks WHERE NOT granted;`

### Step 4: Check Recent Deployments
- Review deployment history for last 2 hours
- Check for new query patterns introduced by recent code changes
- Verify connection pool configuration hasn't changed

### Step 5: Remediation
- If connection pool exhaustion: Increase `max_connections` and `pool_size`
- If slow queries: Add missing indexes or optimize queries
- If deployment-related: Consider rollback
- If load-induced: Scale horizontally (increase replicas)

## Escalation
If latency doesn't recover within 15 minutes, escalate to Database team and Platform Engineering.
""",
            },
            {
                "title": "Database Connection Pool Exhaustion",
                "service_tags": ["payment-gateway", "fraud-api", "postgres"],
                "severity_relevance": ["critical", "high"],
                "content": """# Database Connection Pool Exhaustion

## Symptoms
- Connection timeout errors in application logs
- `pool exhausted` or `too many connections` errors
- Service latency increases sharply
- Database CPU may appear normal but connections at max

## Investigation Steps

### Step 1: Check Active Connections
```sql
SELECT count(*), state FROM pg_stat_activity GROUP BY state;
SELECT count(*) FROM pg_stat_activity WHERE state = 'active';
```

### Step 2: Identify Connection Leaks
Look for long-running idle connections:
```sql
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC
LIMIT 20;
```

### Step 3: Check for Lock Contention
```sql
SELECT relation::regclass, mode, granted
FROM pg_locks
WHERE NOT granted;
```

### Step 4: Remediation
1. Kill idle connections older than 5 minutes
2. Increase `max_connections` if under-provisioned
3. Add PgBouncer connection pooler if not present
4. Review application code for connection leak patterns
5. Scale read replicas for read-heavy workloads

## Prevention
- Set connection pool idle timeout to 300s
- Use PgBouncer in transaction mode
- Monitor `pg_stat_activity` with alerting at 80% utilization
""",
            },
            {
                "title": "Fraud Signal Drift Response",
                "service_tags": ["fraud-api", "ml-pipeline"],
                "severity_relevance": ["high", "medium"],
                "content": """# Fraud Signal Drift Response

## Symptoms
- Fraud score distribution shifts significantly from baseline
- Unusual increase or decrease in fraud flagged transactions
- Model confidence scores become bimodal
- Increase in false positive or false negative rates

## Investigation Steps

### Step 1: Validate Data Pipeline
- Check Kafka consumer lag on fraud feature topics
- Verify feature store (Redis) has fresh data
- Check for null or missing features in model input

### Step 2: Compare Score Distributions
- Plot current hour vs. 7-day baseline fraud score histogram
- Calculate KL-divergence between distributions
- If KL-divergence > 0.1, confirmed drift

### Step 3: Check Input Features
- Validate each input feature for distribution shift
- Check for new merchant categories or BIN ranges
- Verify currency conversion rates are current

### Step 4: Remediation
1. If input drift: Fix upstream data pipeline
2. If concept drift: Trigger model retraining pipeline
3. If sudden shift: Check for coordinated fraud attack
4. Temporarily increase manual review threshold
5. Alert fraud risk team for pattern analysis

## Escalation
If confirmed fraud signal drift, immediately notify Fraud Risk Team and increase manual review rate
to 100% for flagged transactions.
""",
            },
            {
                "title": "Kubernetes Pod Crash Loop Investigation",
                "service_tags": ["payment-gateway", "fraud-api", "kubernetes"],
                "severity_relevance": ["critical", "high"],
                "content": """# Kubernetes Pod Crash Loop Investigation

## Symptoms
- Pod status shows CrashLoopBackOff
- Service availability drops
- Repeated container restarts in short period

## Investigation Steps

### Step 1: Check Pod Status and Events
```bash
kubectl describe pod <pod-name> -n <namespace>
kubectl get events --sort-by=.metadata.creationTimestamp -n <namespace>
```

### Step 2: Check Container Logs
```bash
kubectl logs <pod-name> -n <namespace> --previous
```

### Step 3: Check Resource Limits
- OOMKilled: Increase memory limits
- CPU throttling: Increase CPU limits
- Check `kubectl top pod` for actual resource usage

### Step 4: Check Readiness/Liveness Probes
- Verify probe endpoints are responding
- Check probe timeout and threshold settings

### Step 5: Remediation
1. OOM: Increase memory limits by 50%
2. Application error: Fix code and redeploy
3. Config error: Check ConfigMaps and Secrets
4. Dependency failure: Check upstream service health
5. If deployment-related: Rollback to previous version

## Escalation
If 3+ pods crash-looping for > 5 minutes, page Platform Engineering on-call.
""",
            },
            {
                "title": "High Error Rate Investigation",
                "service_tags": ["payment-gateway", "api-gateway"],
                "severity_relevance": ["critical", "high"],
                "content": """# High Error Rate Investigation

## Symptoms
- Error rate exceeds 5% (or 2x baseline)
- Increase in 5xx HTTP responses
- Customer-facing payment failures

## Investigation Steps

### Step 1: Classify Errors
- Check error breakdown by HTTP status code
- 502/503: Upstream service issues
- 500: Application errors
- 429: Rate limiting triggered

### Step 2: Check Dependencies
- Verify all downstream services are healthy
- Check circuit breaker states
- Verify database connectivity

### Step 3: Check Recent Changes
- Review deployments in last 30 minutes
- Check for config changes
- Review feature flag changes

### Step 4: Remediation
1. If dependency failure: Enable circuit breaker, fallback to cache
2. If overloaded: Scale up replicas
3. If code bug: Rollback deployment
4. If rate limiting: Adjust limits or scale
5. If external API: Implement retry with backoff

## Escalation
If error rate > 10% for > 5 minutes, trigger P1 incident response.
""",
            },
        ]
