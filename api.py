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
from main import AgentOrchestrator
from shared.config import get_settings
from shared.schemas import IncidentRecord, TelemetryEvent
from shared.utils import setup_logging

# ─── Global State ────────────────────────────────────────────────

orchestrator: AgentOrchestrator | None = None
producer = SyntheticTelemetryProducer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    global orchestrator
    setup_logging()
    orchestrator = AgentOrchestrator()
    yield
    orchestrator = None


# ─── App Setup ───────────────────────────────────────────────────

app = FastAPI(
    title="Anomaly Response Agent System",
    description="24/7 Service Reliability & Anomaly-Response Multi-Agent System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request/Response Models ─────────────────────────────────────


class ProcessEventRequest(BaseModel):
    source: str = "payment_gateway"
    service_name: str = "payment-gateway"
    event_type: str = "metric"
    payload: dict[str, Any] = {}


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
async def root():
    """Root endpoint."""
    return {
        "service": "Anomaly Response Agent System",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
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
async def process_event(request: ProcessEventRequest):
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
async def run_demo(request: RunDemoRequest):
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
async def start_stream(request: StreamRequest):
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


@app.get("/api/v1/incidents", tags=["Incidents"])
async def list_incidents(limit: int = 50, status: str | None = None):
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


@app.get("/api/v1/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str):
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
async def get_status():
    """Get current system status and agent metrics."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return orchestrator.get_status()


@app.get("/api/v1/feedback/policy", tags=["Feedback"])
async def get_feedback_policy():
    """Get current RL policy status and action statistics."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return orchestrator.feedback_agent.get_policy_status()


@app.get("/api/v1/actions/tiers", tags=["Actions"])
async def get_action_tiers():
    """Get action tier classification reference."""
    return {
        tier_name: {
            "tier": tier.value,
            "description": get_tier_description(tier),
        }
        for tier_name, tier in ACTION_TIERS.items()
    }


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
