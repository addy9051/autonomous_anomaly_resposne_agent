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

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.action.pagerduty import resolve_pagerduty_incident
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

    # Background Heartbeat: Keep the dashboard pulse alive
    async def pulse_heartbeat() -> None:
        while orchestrator is not None:
            try:
                # Generate a normal metric event to populate charts
                event = producer.generate_normal_metrics()
                await orchestrator.process_event(event, dry_run=True)
                await asyncio.sleep(2.0)  # Pulse every 2s
            except Exception:
                await asyncio.sleep(5.0)

    heartbeat_task = asyncio.create_task(pulse_heartbeat())

    yield

    heartbeat_task.cancel()
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
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
        "http://[::1]:3000",
    ],
    allow_credentials=True,
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


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_auth(api_key: str | None = Security(api_key_header)) -> str | None:
    """
    Enforce route authentication on sensitive action endpoints when configured.
    Fails closed if API_AUTH_TOKEN is defined and the key is missing or invalid.
    """
    settings = get_settings()
    auth_token = getattr(settings.app, "api_auth_token", "") or os.environ.get("API_AUTH_TOKEN", "")
    if auth_token:
        if not api_key or api_key != auth_token:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing X-API-Key header")
    return api_key


class ApproveActionRequest(BaseModel):
    approved: bool = True
    approver: str = "sre-engineer"
    action_name: str | None = None
    params: dict[str, Any] | None = None
    dry_run: bool = False
    reason: str | None = None


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


@app.get("/api/v1/health/detailed", tags=["Health"])
async def detailed_health() -> dict[str, Any]:
    """Detailed health for UI diagnostics."""
    if not orchestrator:
        return {"status": "uninitialized", "error": "Orchestrator not found"}

    return {
        "status": "healthy",
        "orchestrator": {
            "active_count": len(orchestrator.active_incidents),
            "resolved_count": len(orchestrator.resolved_incidents),
            "telemetry_buffer_size": len(orchestrator.telemetry_history),
        },
        "agents": {
            "monitoring": "connected",
            "diagnosis": "connected",
            "action": "connected",
            "feedback": "connected",
        },
    }


@app.post("/api/v1/events/process", tags=["Events"])
async def process_event(request: ProcessEventRequest) -> dict[str, Any]:
    """Submit a single telemetry event for processing."""
    print(f"DEBUG: Processing inbound UI event from {request.source}")
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    event = TelemetryEvent.model_validate({
        "source": request.source,
        "service_name": request.service_name,
        "event_type": request.event_type,
        "payload": request.payload,
    })

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


@app.get("/api/v1/demo/scenarios", tags=["Demo"])
async def list_scenarios() -> dict[str, Any]:
    """List available story-based incident simulation scenarios."""
    from data_pipeline.connectors.story_simulation import StorySimulationEngine

    return {"scenarios": StorySimulationEngine.list_scenarios()}


@app.post("/api/v1/demo/scenarios/{scenario_name}/run", tags=["Demo"])
async def run_scenario(scenario_name: str, dry_run: bool = True) -> dict[str, Any]:
    """Execute a story-based incident narrative scenario."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    from data_pipeline.connectors.story_simulation import StorySimulationEngine

    engine = StorySimulationEngine()
    try:
        events = engine.get_scenario_events(scenario_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    incidents = await orchestrator.run_batch(events, dry_run=dry_run)

    return {
        "scenario": scenario_name,
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
    """List incidents currently in-flight or awaiting approval."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    active = orchestrator.store.list_active()
    return {
        "total": len(active),
        "incidents": [inc.model_dump(mode="json") for inc in active],
    }


@app.get("/api/v1/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str) -> dict[str, Any]:
    """Get a specific incident by ID."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    # Check in-memory active incidents
    if incident_id in orchestrator.active_incidents:
        return orchestrator.active_incidents[incident_id].model_dump(mode="json")

    # Check durable store
    incident = orchestrator.store.get(incident_id)
    if incident:
        return incident.model_dump(mode="json")

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

    return {"total": len(buffer), "history": buffer}


@app.post("/api/v1/incidents/{incident_id}/approve", tags=["Actions"])
async def approve_tier2_action(
    incident_id: str,
    request: ApproveActionRequest,
    _auth: str | None = Depends(verify_auth),
) -> dict[str, Any]:
    """
    Approve or reject a pending action for an incident.
    
    Upon approval:
      1. Executes the exact approved action with bounded parameters.
      2. Transitions incident: awaiting_approval → executing → verifying.
      3. Performs independent post-action recovery verification.
      4. Only resolves if recovery is independently verified.
    Upon rejection:
      1. Records rejection without resolving the incident.
      2. Transitions to escalated for human response.
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    # Find incident in active map or durable store
    incident = orchestrator.active_incidents.get(incident_id) or orchestrator.store.get(incident_id)
    if not incident:
        # Check if already resolved
        resolved_match = next((i for i in orchestrator.resolved_incidents if i.incident_id == incident_id), None)
        if resolved_match:
            raise HTTPException(status_code=400, detail="Incident already resolved")
        raise HTTPException(status_code=404, detail="Incident not found")

    from shared.schemas import IncidentStatus, VerificationOutcome

    valid_pending_statuses = {
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.ACTION_PENDING,
        IncidentStatus.RECOMMENDATION_READY,
    }
    if incident.status not in valid_pending_statuses:
        detail = f"Incident is in status '{incident.status.value}', expected one of {[s.value for s in valid_pending_statuses]}"
        raise HTTPException(status_code=400, detail=detail)

    # ── Handle Rejection ──
    if not request.approved:
        incident.status = IncidentStatus.ESCALATED
        incident.human_overrode = True
        incident.human_feedback = f"Action rejected by {request.approver}. Reason: {request.reason or 'None provided'}"
        
        if incident.action_results:
            incident.action_results[0].human_approved = False
            incident.action_results[0].execution_status = "rejected"

        orchestrator.active_incidents[incident.incident_id] = incident
        orchestrator.store.save(incident)
        orchestrator.store.record_audit(
            incident.incident_id,
            request.approver,
            "action_rejected",
            {"reason": request.reason, "status": incident.status.value},
        )

        return {
            "status": "rejected",
            "incident_id": incident_id,
            "approved": False,
            "incident_status": incident.status.value,
        }

    # ── Handle Approval & Execution ──
    # Resolve action name and parameters
    action_name = request.action_name
    params = request.params

    if not action_name:
        if incident.action_results:
            action_name = incident.action_results[0].action_taken
            params = params or incident.action_results[0].output.get("recommended_params", {})
        elif incident.diagnosis_result and incident.diagnosis_result.recommended_actions:
            action_name = incident.diagnosis_result.recommended_actions[0].action
            params = params or incident.diagnosis_result.recommended_actions[0].params

    if not action_name:
        raise HTTPException(status_code=400, detail="No action specified or recommended for this incident")

    params = params or {}

    # Execute approved action and perform independent verification
    action_result = await orchestrator.action_agent.execute_approved_action(
        action_name=action_name,
        params=params,
        incident=incident,
        approver=request.approver,
        dry_run=request.dry_run,
    )

    if incident.status in (IncidentStatus.VERIFIED_RECOVERED, IncidentStatus.RESOLVED):
        if incident.incident_id in orchestrator.active_incidents:
            del orchestrator.active_incidents[incident.incident_id]
        orchestrator.resolved_incidents.append(incident)
        await resolve_pagerduty_incident(incident.incident_id)
    else:
        orchestrator.active_incidents[incident.incident_id] = incident

    orchestrator.store.save(incident)
    orchestrator.store.record_audit(
        incident.incident_id,
        request.approver,
        "action_approved_and_verified",
        {
            "action": action_name,
            "execution_status": action_result.execution_status,
            "incident_status": incident.status.value,
            "verification": incident.verification_result.model_dump(mode="json") if incident.verification_result else None,
        },
    )

    return {
        "status": "executed",
        "incident_id": incident_id,
        "approved": True,
        "action_taken": action_name,
        "execution_status": action_result.execution_status,
        "incident_status": incident.status.value,
        "verification": incident.verification_result.model_dump(mode="json") if incident.verification_result else None,
    }


@app.get("/api/v1/incidents", tags=["Incidents"])
async def list_incidents(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    """List recent incidents from durable store."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    incidents = orchestrator.store.list_resolved(limit=limit, status=status)

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
