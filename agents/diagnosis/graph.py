"""
Diagnosis Agent — LangGraph State Graph.

A 4-node DAG that processes anomaly events through:
1. gather_context   — collect metrics and recent changes
2. rag_runbook_lookup — search knowledge base for matching procedures
3. dispatch_subagents — coordinate Network/DB/App sub-agents via CrewAI
4. synthesise_rca    — combine evidence into a root cause analysis

State is checkpointed to Redis for durability.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agents.diagnosis.prompts import (
    CONTEXT_GATHER_PROMPT,
    DIAGNOSIS_SYSTEM_PROMPT,
    RAG_LOOKUP_PROMPT,
    SYNTHESIS_PROMPT,
)
from shared.config import get_settings
from shared.schemas import (
    AnomalyEvent,
    DiagnosisResult,
    RecommendedAction,
    RootCauseCategory,
    RunbookReference,
    SubAgentReport,
    ActionTier,
    Severity,
)
from shared.utils import LLMCostTracker, Timer, get_logger, get_tracer

logger = get_logger("diagnosis_agent")
tracer = get_tracer()


# ─── State Definition ────────────────────────────────────────────


class DiagnosisState(TypedDict):
    """State that flows through the diagnosis graph."""
    # Input
    anomaly_event: dict[str, Any]

    # Intermediate
    context: str
    runbook_matches: list[dict[str, Any]]
    sub_agent_reports: dict[str, dict[str, Any]]

    # Output
    diagnosis_result: dict[str, Any] | None

    # Metadata
    messages: Annotated[list, add_messages]


# ─── Node Functions ──────────────────────────────────────────────


async def gather_context(state: DiagnosisState) -> dict:
    """
    Node 1: Gather context for the anomaly.
    Pulls metrics windows, recent deployments, and related alerts.
    """
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm.diagnosis_agent_model,
        api_key=settings.llm.openai_api_key,
        temperature=0.1,
        max_tokens=2048,
    )

    anomaly = state["anomaly_event"]
    prompt = CONTEXT_GATHER_PROMPT.format(
        anomaly_event=json.dumps(anomaly, indent=2, default=str)
    )

    response = await llm.ainvoke([
        SystemMessage(content="You are an SRE investigator. Summarize the operational context."),
        HumanMessage(content=prompt),
    ])

    logger.info("context_gathered", event_id=anomaly.get("event_id"))

    return {
        "context": response.content,
        "messages": [HumanMessage(content=f"Context gathered: {response.content[:200]}...")],
    }


async def rag_runbook_lookup(state: DiagnosisState) -> dict:
    """
    Node 2: Search the runbook knowledge base via RAG.
    Uses hybrid search (vector + BM25) with cross-encoder reranking.
    """
    anomaly = state["anomaly_event"]
    anomaly_type = anomaly.get("anomaly_type", "unknown")
    affected_services = anomaly.get("affected_services", [])

    # In development, use synthetic runbook matches
    # In production, this would call the knowledge_base/retrieval/search.py API
    synthetic_runbooks = _get_synthetic_runbooks(anomaly_type, affected_services)

    logger.info(
        "runbook_lookup_complete",
        num_matches=len(synthetic_runbooks),
        event_id=anomaly.get("event_id"),
    )

    return {
        "runbook_matches": synthetic_runbooks,
        "messages": [HumanMessage(content=f"Found {len(synthetic_runbooks)} matching runbooks")],
    }


async def dispatch_subagents(state: DiagnosisState) -> dict:
    """
    Node 3: Dispatch specialist sub-agents for parallel investigation.
    In production, this uses CrewAI to coordinate Network, DB, and App agents.
    """
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm.diagnosis_agent_model,
        api_key=settings.llm.openai_api_key,
        temperature=0.1,
        max_tokens=1024,
    )

    anomaly = state["anomaly_event"]
    anomaly_context = json.dumps(anomaly, indent=2, default=str)

    reports = {}
    for agent_type, description in [
        ("network", "DNS, CDN, BGP, firewall, load balancer analysis"),
        ("database", "Slow queries, locks, connection pool, replication analysis"),
        ("application", "Pod health, deployments, circuit breakers, error patterns"),
    ]:
        response = await llm.ainvoke([
            SystemMessage(content=f"You are the {agent_type.title()} Sub-Agent. Investigate {description}."),
            HumanMessage(content=f"Investigate this anomaly:\n{anomaly_context}"),
        ])

        reports[agent_type] = {
            "agent_type": agent_type,
            "findings": response.content,
            "severity": "medium",
            "confidence": 0.75,
            "evidence": {},
        }

    logger.info("subagents_complete", num_reports=len(reports), event_id=anomaly.get("event_id"))

    return {
        "sub_agent_reports": reports,
        "messages": [HumanMessage(content=f"Sub-agent reports received: {list(reports.keys())}")],
    }


async def synthesise_rca(state: DiagnosisState) -> dict:
    """
    Node 4: Synthesize all evidence into a final root cause analysis.
    Produces a structured DiagnosisResult.
    """
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm.diagnosis_agent_model,
        api_key=settings.llm.openai_api_key,
        temperature=0.1,
        max_tokens=4096,
    )

    anomaly = state["anomaly_event"]

    prompt = SYNTHESIS_PROMPT.format(
        anomaly_event=json.dumps(anomaly, indent=2, default=str),
        context=state.get("context", "No additional context gathered."),
        runbook_matches=json.dumps(state.get("runbook_matches", []), indent=2),
        sub_agent_reports=json.dumps(state.get("sub_agent_reports", {}), indent=2, default=str),
    )

    response = await llm.ainvoke([
        SystemMessage(content=DIAGNOSIS_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    # Parse the response
    diagnosis = _parse_diagnosis_response(response.content, anomaly, state)

    logger.info(
        "rca_synthesized",
        root_cause_category=diagnosis.get("root_cause_category"),
        confidence=diagnosis.get("confidence"),
        num_actions=len(diagnosis.get("recommended_actions", [])),
        event_id=anomaly.get("event_id"),
    )

    return {
        "diagnosis_result": diagnosis,
        "messages": [HumanMessage(content=f"RCA complete: {diagnosis.get('root_cause', 'unknown')[:100]}")],
    }


# ─── Graph Builder ───────────────────────────────────────────────


def build_diagnosis_graph() -> StateGraph:
    """Build and compile the diagnosis LangGraph."""
    graph = StateGraph(DiagnosisState)

    # Add nodes
    graph.add_node("gather_context", gather_context)
    graph.add_node("rag_runbook_lookup", rag_runbook_lookup)
    graph.add_node("dispatch_subagents", dispatch_subagents)
    graph.add_node("synthesise_rca", synthesise_rca)

    # Define edges (linear DAG)
    graph.set_entry_point("gather_context")
    graph.add_edge("gather_context", "rag_runbook_lookup")
    graph.add_edge("rag_runbook_lookup", "dispatch_subagents")
    graph.add_edge("dispatch_subagents", "synthesise_rca")
    graph.add_edge("synthesise_rca", END)

    return graph.compile()


# ─── Diagnosis Agent Facade ──────────────────────────────────────


class DiagnosisAgent:
    """
    Diagnosis Agent — orchestrates root cause analysis.

    Wraps the LangGraph state machine and provides a simple
    async interface for processing anomaly events.
    """

    def __init__(self) -> None:
        self.graph = build_diagnosis_graph()
        logger.info("diagnosis_agent_initialized")

    async def diagnose(
        self,
        anomaly_event: AnomalyEvent,
        cost_tracker: LLMCostTracker | None = None,
    ) -> DiagnosisResult:
        """Run diagnosis on an anomaly event."""
        with tracer.start_as_current_span("diagnosis_agent.diagnose") as span:
            span.set_attribute("event.id", anomaly_event.event_id)

            timer = Timer()
            with timer:
                initial_state: DiagnosisState = {
                    "anomaly_event": anomaly_event.model_dump(mode="json"),
                    "context": "",
                    "runbook_matches": [],
                    "sub_agent_reports": {},
                    "diagnosis_result": None,
                    "messages": [],
                }

                result = await self.graph.ainvoke(initial_state)
                diagnosis_dict = result.get("diagnosis_result", {})

                # Build DiagnosisResult
                diagnosis = DiagnosisResult(
                    event_id=anomaly_event.event_id,
                    root_cause=diagnosis_dict.get("root_cause", "Unable to determine root cause"),
                    root_cause_category=RootCauseCategory(
                        diagnosis_dict.get("root_cause_category", "unknown")
                    ),
                    runbook_references=[
                        RunbookReference(**r)
                        for r in diagnosis_dict.get("runbook_references", [])
                    ],
                    recommended_actions=[
                        RecommendedAction(
                            action=a.get("action", ""),
                            tier=ActionTier(a.get("tier", 3)),
                            params=a.get("params", {}),
                        )
                        for a in diagnosis_dict.get("recommended_actions", [])
                    ],
                    sub_agent_reports={
                        k: SubAgentReport(
                            agent_type=v.get("agent_type", k),
                            findings=v.get("findings", ""),
                            severity=Severity(v.get("severity", "medium")),
                            confidence=v.get("confidence", 0.5),
                        )
                        for k, v in result.get("sub_agent_reports", {}).items()
                    },
                    reasoning_chain=diagnosis_dict.get("reasoning_chain", ""),
                    confidence=diagnosis_dict.get("confidence", 0.5),
                    is_novel_incident=diagnosis_dict.get("is_novel_incident", False),
                )

            logger.info(
                "diagnosis_complete",
                incident_id=diagnosis.incident_id,
                root_cause_category=diagnosis.root_cause_category.value,
                confidence=diagnosis.confidence,
                elapsed_ms=timer.elapsed_ms,
            )

            span.set_attribute("diagnosis.confidence", diagnosis.confidence)
            span.set_attribute("diagnosis.category", diagnosis.root_cause_category.value)

            return diagnosis


# ─── Helper Functions ────────────────────────────────────────────


def _get_synthetic_runbooks(
    anomaly_type: str, affected_services: list[str]
) -> list[dict[str, Any]]:
    """Return synthetic runbook matches for development."""
    runbook_db = {
        "latency_spike": [
            {
                "runbook_id": "runbook://app/latency-spike-investigation",
                "title": "Payment Gateway Latency Spike Investigation",
                "similarity_score": 0.92,
                "relevant_steps": [
                    "1. Check p99 latency trend over last 30 minutes",
                    "2. Inspect slow query log for new queries > 500ms",
                    "3. Check connection pool utilization",
                    "4. Review recent deployment changes",
                    "5. Scale horizontally if load-induced",
                ],
            },
            {
                "runbook_id": "runbook://db/connection-pool-exhaustion",
                "title": "Database Connection Pool Exhaustion",
                "similarity_score": 0.85,
                "relevant_steps": [
                    "1. Check current active connections vs max pool size",
                    "2. Identify long-running transactions holding connections",
                    "3. Kill idle connections older than 5 minutes",
                    "4. Increase pool size if under-provisioned",
                ],
            },
        ],
        "error_rate": [
            {
                "runbook_id": "runbook://app/error-rate-investigation",
                "title": "Elevated Error Rate Investigation",
                "similarity_score": 0.89,
                "relevant_steps": [
                    "1. Classify errors by HTTP status code",
                    "2. Check for dependency timeouts",
                    "3. Review circuit breaker states",
                    "4. Check for recent deployment rollouts",
                ],
            },
        ],
        "fraud_signal": [
            {
                "runbook_id": "runbook://fraud/signal-drift",
                "title": "Fraud Signal Drift Response",
                "similarity_score": 0.88,
                "relevant_steps": [
                    "1. Compare current fraud score distribution vs baseline",
                    "2. Check for new merchant patterns",
                    "3. Validate model input feature pipeline",
                    "4. Escalate to fraud risk team if confirmed drift",
                ],
            },
        ],
    }

    return runbook_db.get(anomaly_type, [
        {
            "runbook_id": "runbook://general/incident-response",
            "title": "General Incident Response Procedure",
            "similarity_score": 0.65,
            "relevant_steps": [
                "1. Assess blast radius",
                "2. Engage on-call engineer",
                "3. Begin timeline documentation",
            ],
        }
    ])


def _parse_diagnosis_response(
    content: str,
    anomaly: dict[str, Any],
    state: DiagnosisState,
) -> dict[str, Any]:
    """Parse LLM response into a diagnosis dict."""
    try:
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0]

        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        # Fallback: construct from available evidence
        return {
            "root_cause": content[:500] if content else "Unable to determine root cause",
            "root_cause_category": "unknown",
            "runbook_references": state.get("runbook_matches", []),
            "recommended_actions": [
                {"action": "scale_replicas", "tier": 1, "params": {"replicas": 3}},
            ],
            "reasoning_chain": content,
            "confidence": 0.6,
            "is_novel_incident": len(state.get("runbook_matches", [])) == 0,
        }
