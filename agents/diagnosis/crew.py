"""
CrewAI Crew for Diagnosis Sub-agents.

Coordinates 3 specialist sub-agents for parallel investigation:
- Network Sub-Agent: DNS, CDN, BGP, firewalls
- Database Sub-Agent: queries, locks, pools, replication
- Application Sub-Agent: pods, deployments, circuit breakers
"""

from __future__ import annotations

from crewai import Agent, Crew, Process, Task

from agents.diagnosis.prompts import (
    APPLICATION_SUBAGENT_PROMPT,
    DATABASE_SUBAGENT_PROMPT,
    NETWORK_SUBAGENT_PROMPT,
)
from shared.config import get_settings
from shared.schemas import Severity, SubAgentReport
from shared.utils import get_logger

logger = get_logger("diagnosis_crew")


def create_diagnosis_crew(anomaly_context: str) -> Crew:
    """
    Create a CrewAI crew with 3 specialist diagnosis sub-agents.

    Args:
        anomaly_context: JSON string describing the anomaly to investigate.

    Returns:
        A configured Crew ready to execute.
    """
    settings = get_settings()

    # ─── Define Agents ────────────────────────────────────────

    network_agent = Agent(
        role="Network Infrastructure Specialist",
        goal="Investigate network-layer issues that could cause payment processing anomalies",
        backstory=(
            "You are an expert network engineer specializing in financial services infrastructure. "
            "You understand DNS, CDN, BGP routing, firewall rules, and load balancer configurations. "
            "You can quickly identify when network issues are the root cause of service degradation."
        ),
        verbose=True,
        allow_delegation=False,
        llm=f"openai/{settings.llm.diagnosis_agent_model}",
    )

    database_agent = Agent(
        role="Database Performance Specialist",
        goal="Investigate database-layer issues affecting payment transaction processing",
        backstory=(
            "You are a senior DBA with deep expertise in PostgreSQL, connection pooling, "
            "replication, and query optimization. You can analyze slow query patterns, "
            "identify lock contention, and diagnose connection pool exhaustion."
        ),
        verbose=True,
        allow_delegation=False,
        llm=f"openai/{settings.llm.diagnosis_agent_model}",
    )

    application_agent = Agent(
        role="Application Reliability Specialist",
        goal="Investigate application-layer issues in payment processing services",
        backstory=(
            "You are a senior SRE specializing in Kubernetes-based microservices. "
            "You understand pod lifecycle, deployment strategies, circuit breakers, "
            "thread pool management, and memory leak patterns."
        ),
        verbose=True,
        allow_delegation=False,
        llm=f"openai/{settings.llm.diagnosis_agent_model}",
    )

    # ─── Define Tasks ─────────────────────────────────────────

    network_task = Task(
        description=NETWORK_SUBAGENT_PROMPT.format(anomaly_context=anomaly_context),
        expected_output=(
            "A JSON report with: agent_type, findings (detailed text), "
            "severity (critical/high/medium/low), confidence (0-1), evidence dict"
        ),
        agent=network_agent,
    )

    database_task = Task(
        description=DATABASE_SUBAGENT_PROMPT.format(anomaly_context=anomaly_context),
        expected_output=(
            "A JSON report with: agent_type, findings (detailed text), "
            "severity (critical/high/medium/low), confidence (0-1), evidence dict"
        ),
        agent=database_agent,
    )

    application_task = Task(
        description=APPLICATION_SUBAGENT_PROMPT.format(anomaly_context=anomaly_context),
        expected_output=(
            "A JSON report with: agent_type, findings (detailed text), "
            "severity (critical/high/medium/low), confidence (0-1), evidence dict"
        ),
        agent=application_agent,
    )

    # ─── Create Crew ──────────────────────────────────────────

    crew = Crew(
        agents=[network_agent, database_agent, application_agent],
        tasks=[network_task, database_task, application_task],
        process=Process.sequential,  # Can be changed to parallel when supported
        verbose=True,
    )

    return crew


async def run_diagnosis_crew(anomaly_context: str) -> dict[str, SubAgentReport]:
    """
    Execute the diagnosis crew and return sub-agent reports.

    Args:
        anomaly_context: JSON description of the anomaly.

    Returns:
        Dictionary mapping agent type to SubAgentReport.
    """
    try:
        crew = create_diagnosis_crew(anomaly_context)
        result = crew.kickoff()

        # Parse crew output into SubAgentReport objects
        reports = {}
        for task_output in result.tasks_output:
            agent_type = _infer_agent_type(task_output.description)
            reports[agent_type] = SubAgentReport(
                agent_type=agent_type,
                findings=task_output.raw,
                severity=Severity.MEDIUM,
                confidence=0.75,
                evidence={},
            )

        logger.info("crew_execution_complete", num_reports=len(reports))
        return reports

    except Exception as e:
        logger.error("crew_execution_failed", error=str(e))
        # Return empty reports on failure
        return {
            agent_type: SubAgentReport(
                agent_type=agent_type,
                findings=f"Sub-agent execution failed: {str(e)}",
                severity=Severity.LOW,
                confidence=0.0,
            )
            for agent_type in ["network", "database", "application"]
        }


def _infer_agent_type(description: str) -> str:
    """Infer agent type from task description."""
    desc_lower = description.lower()
    if "network" in desc_lower or "dns" in desc_lower or "bgp" in desc_lower:
        return "network"
    elif "database" in desc_lower or "query" in desc_lower or "replication" in desc_lower:
        return "database"
    elif "application" in desc_lower or "pod" in desc_lower or "deployment" in desc_lower:
        return "application"
    return "unknown"
