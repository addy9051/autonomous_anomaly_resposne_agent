"""
FastAPI Application — Agent System REST API.

Provides endpoints for:
- Submitting telemetry events for processing
- Querying incident status and history
- Managing the knowledge base
- Monitoring agent health and metrics
- Triggering pipeline runs
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents.action.tiers import ACTION_TIERS, get_tier_description
from data_pipeline.connectors.synthetic_producer import SyntheticTelemetryProducer
from orchestrator import AgentOrchestrator
from shared.config import get_settings
from shared.schemas import TelemetryEvent
from shared.utils import setup_logging

# ─── Global State ────────────────────────────────────────────────

orchestrator: AgentOrchestrator | None = None
producer = SyntheticTelemetryProducer()


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:  # noqa: ANN401
    """Application lifespan: startup and shutdown."""
    global orchestrator
    setup_logging()
    orchestrator = AgentOrchestrator()
    yield
    orchestrator = None


# ─── App Setup ───────────────────────────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import os

app = FastAPI(
    title="Anomaly Response Agent System",
    description="24/7 Service Reliability & Anomaly-Response Multi-Agent System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ui_dir = os.path.join(os.path.dirname(__file__), "ui")
os.makedirs(ui_dir, exist_ok=True)
app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")


# ─── Request/Response Models ─────────────────────────────────────


class ProcessEventRequest(BaseModel):
    source: str = "payment_gateway"
    service_name: str = "payment-gateway"
    event_type: str = "metric"
    payload: dict[str, Any] = {}

class ApproveActionRequest(BaseModel):
    approved: bool = True

class RunDemoRequest(BaseModel):
    num_events: int = 10
    anomaly_fraction: float = 0.1
    dry_run: bool = True


class StreamRequest(BaseModel):
    duration_seconds: float = 30.0
    events_per_second: float = 2.0
    anomaly_probability: float = 0.05
    dry_run: bool = True


class HealthResponse(BaseModel):
    status: str
    version: str
    agents: dict[str, str]
    active_incidents: int
    resolved_incidents: int


# ─── Endpoints ───────────────────────────────────────────────────


@app.get("/", tags=["Health"])
async def root() -> RedirectResponse:
    """Root endpoint - Redirects to new UI Dashboard."""
    return RedirectResponse(url="/ui/")


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        agents={
            "monitoring": "active",
            "diagnosis": "active",
            "action": "active",
            "feedback": "active",
        },
        active_incidents=len(orchestrator.active_incidents) if orchestrator else 0,
        resolved_incidents=len(orchestrator.resolved_incidents) if orchestrator else 0,
    )


@app.post("/api/v1/events/process", tags=["Events"])
async def process_event(request: ProcessEventRequest) -> dict[str, Any]:
    """Submit a single telemetry event for processing."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    event = TelemetryEvent(
        source=request.source,
        service_name=request.service_name,
        event_type=request.event_type,
        payload=request.payload,
    )

    incident = await orchestrator.process_event(event, dry_run=True)

    if incident:
        return {
            "status": "anomaly_detected",
            "incident": incident.model_dump(mode="json"),
        }
    return {"status": "normal", "message": "No anomaly detected"}


@app.post("/api/v1/demo/run", tags=["Demo"])
async def run_demo(request: RunDemoRequest) -> dict[str, Any]:
    """Run a demo with synthetic telemetry events."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    events = producer.generate_batch(
        count=request.num_events,
        anomaly_fraction=request.anomaly_fraction,
    )

    incidents = await orchestrator.run_batch(events, dry_run=request.dry_run)

    return {
        "total_events": len(events),
        "incidents_detected": len(incidents),
        "incidents": [inc.model_dump(mode="json") for inc in incidents],
    }


@app.post("/api/v1/stream/start", tags=["Streaming"])
async def start_stream(request: StreamRequest) -> dict[str, Any]:
    """Start streaming synthetic telemetry."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    incidents = await orchestrator.run_streaming(
        events_per_second=request.events_per_second,
        anomaly_probability=request.anomaly_probability,
        duration_seconds=request.duration_seconds,
        dry_run=request.dry_run,
    )

    return {
        "duration_seconds": request.duration_seconds,
        "incidents_detected": len(incidents),
        "incidents": [inc.model_dump(mode="json") for inc in incidents],
    }


@app.get("/api/v1/telemetry/recent", tags=["Monitoring"])
async def get_recent_telemetry() -> dict[str, Any]:
    """Get the last 100 telemetry events for real-time pulsing."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return {
        "count": len(orchestrator.telemetry_history),
        "events": [e.model_dump(mode="json") for e in orchestrator.telemetry_history],
    }


@app.get("/api/v1/incidents/active", tags=["Incidents"])
async def list_active_incidents() -> dict[str, Any]:
    """List incidents currently being processed (for state mapping)."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return {
        "total": len(orchestrator.active_incidents),
        "incidents": [inc.model_dump(mode="json") for inc in orchestrator.active_incidents.values()],
    }


@app.get("/api/v1/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str) -> dict[str, Any]:
    """Get a specific incident by ID."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    # Check active incidents
    if incident_id in orchestrator.active_incidents:
        return orchestrator.active_incidents[incident_id].model_dump(mode="json")

    # Check resolved incidents
    for inc in orchestrator.resolved_incidents:
        if inc.incident_id == incident_id:
            return inc.model_dump(mode="json")

    raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")


@app.get("/api/v1/status", tags=["Monitoring"])
async def get_status() -> dict[str, Any]:
    """Get current system status and agent metrics."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return orchestrator.get_status()


@app.get("/api/v1/feedback/policy", tags=["Feedback"])
async def get_feedback_policy() -> dict[str, Any]:
    """Get current RL policy status and action statistics."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return orchestrator.feedback_agent.get_policy_status()


@app.get("/api/v1/actions/tiers", tags=["Actions"])
async def get_action_tiers() -> dict[str, Any]:
    """Get action tier classification reference."""
    return {
        tier_name: {
            "tier": tier.value,
            "description": get_tier_description(tier),
        }
        for tier_name, tier in ACTION_TIERS.items()
    }


@app.get("/api/v1/feedback/rewards", tags=["Feedback"])
async def get_reward_history(limit: int = 100) -> dict[str, Any]:
    """Get the history of RL rewards for plotting performance curves."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    # Access experience buffer from feedback agent
    buffer = orchestrator.feedback_agent.experience_buffer[-limit:]

    return {
        "total": len(buffer),
        "history": buffer
    }


@app.post("/api/v1/incidents/{incident_id}/approve", tags=["Actions"])
async def approve_tier2_action(incident_id: str, request: ApproveActionRequest) -> dict[str, Any]:
    """Approve a pending Tier 2 action from the dashboard."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    incident = orchestrator.active_incidents.get(incident_id)
    if not incident:
        # Check resolved just to be safe
        from shared.schemas import IncidentStatus
        for inc in orchestrator.resolved_incidents:
            if inc.incident_id == incident_id:
                raise HTTPException(status_code=400, detail="Incident already resolved")
        raise HTTPException(status_code=404, detail="Incident not found")

    from shared.schemas import IncidentStatus
    if incident.status != IncidentStatus.ACTION_PENDING:
        raise HTTPException(status_code=400, detail=f"Incident is in status {incident.status.value}, expected ACTION_PENDING")

    # Move incident to resolved execution status upon approval
    incident.status = IncidentStatus.RESOLVED

    if request.approved and incident.action_results:
        action = incident.action_results[0]
        action.human_approved = True
        action.execution_status = "executed"

    # Move to resolved map
    del orchestrator.active_incidents[incident.incident_id]
    orchestrator.resolved_incidents.append(incident)

    return {"status": "success", "incident_id": incident_id, "approved": request.approved}


@app.get("/api/v1/incidents", tags=["Incidents"])
async def list_incidents(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    """List recent incidents."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    incidents = orchestrator.resolved_incidents[-limit:]

    if status:
        incidents = [i for i in incidents if i.status.value == status]

    return {
        "total": len(incidents),
        "incidents": [inc.model_dump(mode="json") for inc in incidents],
    }


# ─── Knowledge Base Endpoints ────────────────────────────────────


class KBSearchRequest(BaseModel):
    q: str = "latency spike"
    service: str | None = None
    top_k: int = 5


@app.post("/api/v1/knowledge/seed", tags=["Knowledge Base"])
async def seed_knowledge_base() -> dict[str, Any]:
    """Ingest sample runbooks into the pgvector knowledge base."""
    from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline

    pipeline = RunbookIngestionPipeline()
    total_chunks = await pipeline.ingest_sample_runbooks()

    return {
        "status": "success",
        "total_chunks": total_chunks,
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 768,
    }


@app.get("/api/v1/knowledge/search", tags=["Knowledge Base"])
async def search_knowledge_base(q: str = "latency spike", service: str | None = None, top_k: int = 5) -> dict[str, Any]:
    """Search the runbook knowledge base via hybrid RAG."""
    from knowledge_base.retrieval.search import HybridSearchService

    search = HybridSearchService()
    service_tags = [service] if service else None
    results = await search.search(query=q, service_tags=service_tags, top_k=top_k)

    return {
        "query": q,
        "num_results": len(results),
        "results": [ref.model_dump(mode="json") for ref in results],
    }


@app.get("/api/v1/knowledge/health", tags=["Knowledge Base"])
async def knowledge_base_health() -> dict[str, Any]:
    """Check knowledge base connectivity and document count."""
    from knowledge_base.retrieval.search import HybridSearchService

    search = HybridSearchService()
    return await search.healthcheck()


# ─── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api:app",
        host=settings.app.api_host,
        port=settings.app.api_port,
        reload=settings.app.app_env == "development",
    )
