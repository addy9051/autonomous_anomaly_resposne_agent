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
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from agents.action.pagerduty import resolve_pagerduty_incident
from agents.action.tiers import ACTION_TIERS, get_tier_description
from data_pipeline.connectors.adapters import get_active_modes, get_telemetry_adapter, validate_modes
from data_pipeline.connectors.alertmanager_intake import parse_alertmanager_payload
from data_pipeline.connectors.connector_store import connector_store
from data_pipeline.connectors.ingestion import TelemetryIngestionService
from data_pipeline.connectors.synthetic_producer import SyntheticTelemetryProducer
from orchestrator import AgentOrchestrator
from shared.config import get_settings
from shared.schemas import ActionPolicyDecision, TelemetryEvent
from shared.utils import LLMCostTracker, get_logger, setup_logging

# ─── Global State ────────────────────────────────────────────────

orchestrator: AgentOrchestrator | None = None
ingestion: TelemetryIngestionService | None = None
producer = SyntheticTelemetryProducer()
logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:  # noqa: ANN401
    """Application lifespan: startup and shutdown."""
    global orchestrator
    setup_logging()
    connector_store.load()
    orchestrator = AgentOrchestrator()

    settings = get_settings()
    from shared.egress import egress_self_test

    egress_self_test()
    heartbeat_enabled = (
        settings.app.enable_synthetic_heartbeat
        or settings.app.app_env.lower() == "development"
    )

    async def pulse_heartbeat() -> None:
        while orchestrator is not None:
            try:
                # Generate a normal metric event to populate charts
                event = producer.generate_normal_metrics()
                await orchestrator.process_event(event, dry_run=True)
                await asyncio.sleep(2.0)  # Pulse every 2s
            except Exception:
                await asyncio.sleep(5.0)

    heartbeat_task = asyncio.create_task(pulse_heartbeat()) if heartbeat_enabled else None

    global ingestion
    ingestion = None
    if settings.integration.enable_live_ingestion:
        ingestion = _build_ingestion_service(orchestrator)
        if ingestion is not None:
            ingestion.start()

    yield

    if ingestion is not None:
        await ingestion.stop()
        ingestion = None
    if heartbeat_task is not None:
        heartbeat_task.cancel()
    orchestrator = None


def _build_ingestion_service(orch: AgentOrchestrator) -> TelemetryIngestionService | None:
    cfg = get_settings().integration
    services = [s.strip() for s in cfg.ingestion_services.split(",") if s.strip()]
    if not services:
        logger.warning("live_ingestion_disabled_no_services", hint="set INGESTION_SERVICES")
        return None
    modes = get_active_modes()
    try:
        validate_modes(modes)
    except ValueError as exc:
        logger.warning("live_ingestion_disabled_invalid_connectors", reason=str(exc))
        return None
    adapters = {m: get_telemetry_adapter(m) for m in modes}
    return TelemetryIngestionService(
        orch,
        adapters,
        services,
        poll_interval_seconds=cfg.ingestion_poll_interval_seconds,
        z_threshold=cfg.ingestion_zscore_threshold,
        min_samples=cfg.ingestion_min_samples,
        dry_run=cfg.ingestion_dry_run,
        cooldown_seconds=cfg.ingestion_escalation_cooldown_seconds,
    )


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


def _strip_api_gateway_stage_prefix(path: str, prefix: str) -> str:
    normalized_prefix = prefix.rstrip("/")
    if not normalized_prefix:
        return path
    if path == normalized_prefix:
        return "/"
    if path.startswith(f"{normalized_prefix}/"):
        return path[len(normalized_prefix) :]
    return path


@app.middleware("http")
async def strip_api_gateway_stage_prefix(request: Request, call_next: Any) -> Any:
    prefix = get_settings().app.api_gateway_stage_prefix
    path = request.scope["path"]
    stripped_path = _strip_api_gateway_stage_prefix(path, prefix)
    if stripped_path != path:
        request.scope["path"] = stripped_path
        raw_path = request.scope.get("raw_path")
        if raw_path is not None:
            prefix_length = len(prefix.rstrip("/").encode())
            request.scope["raw_path"] = raw_path[prefix_length:] or b"/"
    return await call_next(request)


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


async def verify_auth(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    """
    Resolve caller identity from the trusted gateway, or a development-only API key.
    """
    settings = get_settings()
    config = settings.app
    environment = config.app_env.lower()
    identity = request.headers.get(config.trusted_identity_header)
    if identity and identity.strip():
        return identity.strip()

    if environment != "development" or config.require_gateway_auth:
        raise HTTPException(
            status_code=401,
            detail="Missing trusted gateway identity; requests must use authenticated API Gateway ingress.",
        )

    auth_token = config.api_auth_token or os.environ.get("API_AUTH_TOKEN", "")
    if auth_token:
        if not api_key or not hmac.compare_digest(api_key, auth_token):
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing X-API-Key header")
        return "dev-api-key-user"

    logger.warning("auth_bypassed_development", tip="Configure API_AUTH_TOKEN or require gateway authentication")
    return "anonymous-dev"


class ApproveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
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
async def process_event(request: ProcessEventRequest, _auth: str = Depends(verify_auth)) -> dict[str, Any]:
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


@app.post("/api/v1/alerts/alertmanager", tags=["Events"])
async def alertmanager_webhook(
    payload: dict[str, Any],
    dry_run: bool = True,
    _auth: str = Depends(verify_auth),
) -> dict[str, Any]:
    """Alertmanager webhook: each firing alert starts a scoped investigation."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    anomalies = parse_alertmanager_payload(payload)
    incident_ids: list[str] = []
    suppressed = 0
    for anomaly in anomalies:
        cost_tracker = LLMCostTracker(incident_id="pending")
        incident = await orchestrator.process_anomaly(anomaly, cost_tracker, dry_run=dry_run)
        if incident:
            incident_ids.append(incident.incident_id)
        else:
            suppressed += 1

    return {
        "status": "accepted",
        "alerts_received": len(payload.get("alerts", []) or []),
        "investigations_started": len(incident_ids),
        "suppressed_or_duplicate": suppressed,
        "incident_ids": incident_ids,
    }


@app.post("/api/v1/demo/run", tags=["Demo"])
async def run_demo(request: RunDemoRequest, _auth: str = Depends(verify_auth)) -> dict[str, Any]:
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
async def run_scenario(
    scenario_name: str,
    dry_run: bool = True,
    _auth: str = Depends(verify_auth),
) -> dict[str, Any]:
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
async def start_stream(request: StreamRequest, _auth: str = Depends(verify_auth)) -> dict[str, Any]:
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


@app.get("/api/v1/ingestion/status", tags=["Monitoring"])
async def ingestion_status() -> dict[str, Any]:
    """Per-vendor live ingestion health (last success, errors, backoff state)."""
    if ingestion is None:
        return {"enabled": False}
    return {"enabled": True, **ingestion.snapshot()}


class ConnectorSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectors: list[str]


class ConnectorCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = {}


class IngestionActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: str
    dry_run: bool = True
    poll_interval_seconds: float = 15.0


def _connector_view() -> dict[str, Any]:
    view = connector_store.view()
    view["active"] = ingestion is not None
    if ingestion is not None:
        view["status"] = ingestion.snapshot()
    return view


async def _restart_ingestion() -> None:
    global ingestion
    if ingestion is not None:
        await ingestion.stop()
        ingestion = None
    if orchestrator is None or not get_settings().integration.enable_live_ingestion:
        return
    modes = get_active_modes()
    if modes:
        orchestrator.telemetry_adapter = get_telemetry_adapter()
    ingestion = _build_ingestion_service(orchestrator)
    if ingestion is not None:
        ingestion.start()


@app.get("/api/v1/connectors", tags=["Connectors"])
async def get_connectors(_auth: str = Depends(verify_auth)) -> dict[str, Any]:
    """Connector catalog with the SRE's selection; secrets are reported only as is_set."""
    return _connector_view()


@app.put("/api/v1/connectors/selection", tags=["Connectors"])
async def set_connector_selection(
    request: ConnectorSelectionRequest, _auth: str = Depends(verify_auth)
) -> dict[str, Any]:
    try:
        connector_store.set_selection(request.connectors)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _connector_view()


@app.put("/api/v1/connectors/{vendor}/credentials", tags=["Connectors"])
async def set_connector_credentials(
    vendor: str, request: ConnectorCredentialsRequest, _auth: str = Depends(verify_auth)
) -> dict[str, Any]:
    try:
        connector_store.set_credentials(vendor, request.values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _connector_view()


@app.post("/api/v1/connectors/{vendor}/test", tags=["Connectors"])
async def test_connector(
    vendor: str, request: ConnectorCredentialsRequest, _auth: str = Depends(verify_auth)
) -> dict[str, Any]:
    """Health-check a connector using unsaved form values merged over stored ones."""
    try:
        return await connector_store.test_connection(vendor, request.values)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/connectors/activate", tags=["Connectors"])
async def activate_ingestion(
    request: IngestionActivationRequest, _auth: str = Depends(verify_auth)
) -> dict[str, Any]:
    """Build CONNECTOR_MODES from the selection and (re)start combined live ingestion."""
    try:
        connector_store.set_ingestion(
            services=request.services,
            dry_run=request.dry_run,
            poll_interval_seconds=request.poll_interval_seconds,
            enabled=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    problems = connector_store.activation_problems()
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    await _restart_ingestion()
    return _connector_view()


@app.post("/api/v1/connectors/deactivate", tags=["Connectors"])
async def deactivate_ingestion(_auth: str = Depends(verify_auth)) -> dict[str, Any]:
    ing = connector_store.state["ingestion"]
    connector_store.set_ingestion(
        services=ing.get("services", ""),
        dry_run=bool(ing.get("dry_run", True)),
        poll_interval_seconds=float(ing.get("poll_interval_seconds", 15.0)),
        enabled=False,
    )
    await _restart_ingestion()
    return _connector_view()


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


@app.get("/api/v1/episodes/{episode_id}", tags=["Memory"])
async def get_episode(episode_id: str) -> dict[str, Any]:
    """Episodic memory: the episode, its SRE brief and the per-stage timeline of how it was handled."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    episode = await orchestrator.memory.get(episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {
        "episode": episode.model_dump(mode="json"),
        "timeline": await orchestrator.memory.timeline(episode_id),
    }


@app.get("/api/v1/incidents/{incident_id}/brief", tags=["Incidents"])
async def get_incident_brief(incident_id: str) -> dict[str, Any]:
    """SRE-facing brief: issue, root cause, fix applied or recommended steps, gaps, duplicate linkage."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    incident = orchestrator.active_incidents.get(incident_id) or orchestrator.store.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"incident_id": incident_id, "episode_id": incident.episode_id, "brief": incident.brief}


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


@app.post("/api/v1/incidents/{incident_id}/approve", tags=["Actions"])
async def approve_tier2_action(
    incident_id: str,
    request: ApproveActionRequest,
    approver: str = Depends(verify_auth),
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

    from shared.schemas import IncidentStatus

    valid_pending_statuses = {
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.ACTION_PENDING,
    }
    if incident.status not in valid_pending_statuses:
        expected = [status.value for status in valid_pending_statuses]
        detail = f"Incident is in status '{incident.status.value}', expected one of {expected}"
        raise HTTPException(status_code=400, detail=detail)

    pending_actions = [
        action
        for action in incident.action_results
        if action.execution_status == "pending_approval"
    ]
    if len(pending_actions) != 1:
        raise HTTPException(status_code=409, detail="Incident must have exactly one pending action proposal.")
    proposal = pending_actions[0]
    action_name = proposal.action_taken
    params = proposal.output.get("recommended_params")
    if (
        not isinstance(params, dict)
        or not proposal.proposal_hash
        or proposal.policy_decision != ActionPolicyDecision.APPROVAL_REQUIRED
        or not proposal.proposed_service
        or not proposal.proposed_environment
    ):
        raise HTTPException(status_code=409, detail="Stored action proposal is incomplete and cannot be approved.")

    from agents.action.policy import compute_proposal_hash

    try:
        expected_proposal_hash = compute_proposal_hash(
            action_name,
            params,
            service=proposal.proposed_service,
            environment=proposal.proposed_environment,
            dry_run=proposal.is_simulated,
            policy_decision=proposal.policy_decision.value if proposal.policy_decision else None,
            verification_criteria=proposal.verification_criteria,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Stored proposal cannot be validated.") from exc
    if not hmac.compare_digest(expected_proposal_hash, proposal.proposal_hash):
        raise HTTPException(status_code=409, detail="Stored proposal integrity check failed.")

    claimed_incident = orchestrator.store.claim_pending_approval(
        incident_id,
        proposal.proposal_hash,
        approved=request.approved,
        actor=approver,
        reason=request.reason,
    )
    if claimed_incident is None:
        raise HTTPException(status_code=409, detail="Proposal is no longer pending or has already been consumed.")
    incident = claimed_incident
    orchestrator.active_incidents[incident.incident_id] = incident

    if not request.approved:
        orchestrator.store.record_audit(
            incident.incident_id,
            approver,
            "action_rejected",
            {"reason": request.reason, "status": incident.status.value},
        )
        return {
            "status": "rejected",
            "incident_id": incident_id,
            "approved": False,
            "incident_status": incident.status.value,
        }

    try:
        action_result = await orchestrator.action_agent.execute_approved_action(
            action_name=action_name,
            params=params,
            incident=incident,
            approver=approver,
            proposal_hash=proposal.proposal_hash,
            dry_run=proposal.is_simulated,
        )
    except ValueError as exc:
        incident.status = IncidentStatus.ESCALATED
        incident.human_feedback = f"Approved proposal was blocked before execution: {exc}"
        for action in incident.action_results:
            if action.execution_status == "pending_approval" and action.proposal_hash == proposal.proposal_hash:
                action.execution_status = "policy_denied"
                action.error = str(exc)
        orchestrator.active_incidents[incident.incident_id] = incident
        orchestrator.store.save(incident)
        orchestrator.store.record_audit(
            incident.incident_id,
            approver,
            "approved_action_blocked",
            {"reason": str(exc), "status": incident.status.value},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
        approver,
        "action_approval_processed",
        {
            "action": action_name,
            "execution_status": action_result.execution_status,
            "incident_status": incident.status.value,
            "verification": (
                incident.verification_result.model_dump(mode="json")
                if incident.verification_result
                else None
            ),
        },
    )

    return {
        "status": action_result.execution_status,
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
async def seed_knowledge_base(_auth: str = Depends(verify_auth)) -> dict[str, Any]:
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
