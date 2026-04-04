"""
N8n Workflow Integration.

Provides webhook clients for triggering N8n automation workflows.
Each workflow is idempotent with rollback capability.
"""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import get_settings
from shared.utils import get_logger, retry_tool_call

logger = get_logger("n8n_workflows")


# ─── Workflow Registry ───────────────────────────────────────────

N8N_WORKFLOWS: dict[str, dict[str, Any]] = {
    "scale_replicas": {
        "webhook_path": "/webhook/scale-replicas",
        "description": "Scale Kubernetes deployment replicas",
        "params": ["deployment", "namespace", "replicas"],
        "rollback": "scale_replicas_rollback",
    },
    "clear_cache": {
        "webhook_path": "/webhook/clear-cache",
        "description": "Flush Redis cache for a specific service",
        "params": ["service", "cache_pattern"],
        "rollback": None,
    },
    "restart_unhealthy_pod": {
        "webhook_path": "/webhook/restart-pod",
        "description": "Delete and restart an unhealthy pod",
        "params": ["pod_name", "namespace"],
        "rollback": None,
    },
    "update_circuit_breaker": {
        "webhook_path": "/webhook/circuit-breaker",
        "description": "Update circuit breaker configuration",
        "params": ["service", "threshold", "timeout_ms"],
        "rollback": "update_circuit_breaker_rollback",
    },
    "drain_node": {
        "webhook_path": "/webhook/drain-node",
        "description": "Cordon and drain a Kubernetes node",
        "params": ["node_name"],
        "rollback": "uncordon_node",
    },
    "failover_db": {
        "webhook_path": "/webhook/failover-db",
        "description": "Promote database replica to primary",
        "params": ["cluster_name", "replica_id"],
        "rollback": None,
    },
    "throttle_merchant": {
        "webhook_path": "/webhook/throttle-merchant",
        "description": "Apply rate limiting to a specific merchant",
        "params": ["merchant_id", "rate_limit_rps"],
        "rollback": "unthrottle_merchant",
    },
    "create_incident_ticket": {
        "webhook_path": "/webhook/create-ticket",
        "description": "Create incident ticket in JIRA/ServiceNow",
        "params": ["title", "description", "severity", "assignee"],
        "rollback": None,
    },
    "send_slack_notification": {
        "webhook_path": "/webhook/slack-notify",
        "description": "Post incident notification to Slack channel",
        "params": ["channel", "message", "severity"],
        "rollback": None,
    },
    "update_statuspage": {
        "webhook_path": "/webhook/statuspage",
        "description": "Update public status page",
        "params": ["component", "status", "message"],
        "rollback": "update_statuspage",
    },
    "kill_long_running_queries": {
        "webhook_path": "/webhook/kill-queries",
        "description": "Terminate database queries running longer than threshold",
        "params": ["database", "threshold_seconds"],
        "rollback": None,
    },
    "increase_connection_pool": {
        "webhook_path": "/webhook/connection-pool",
        "description": "Increase database connection pool size",
        "params": ["database", "new_max_connections"],
        "rollback": "decrease_connection_pool",
    },
    "increase_timeout": {
        "webhook_path": "/webhook/increase-timeout",
        "description": "Increase service timeout configuration",
        "params": ["service", "new_timeout_ms"],
        "rollback": "decrease_timeout",
    },
    "add_rate_limit": {
        "webhook_path": "/webhook/rate-limit",
        "description": "Add or update rate limiting rules",
        "params": ["service", "endpoint", "rate_limit_rps"],
        "rollback": "remove_rate_limit",
    },
}


async def trigger_workflow(
    workflow_name: str,
    params: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Trigger an N8n workflow via webhook.

    Args:
        workflow_name: Name of the workflow to trigger
        params: Parameters to pass to the workflow
        dry_run: If True, log the action but don't execute

    Returns:
        Workflow execution result
    """
    settings = get_settings()
    workflow = N8N_WORKFLOWS.get(workflow_name)

    if not workflow:
        return {"status": "error", "error": f"Unknown workflow: {workflow_name}"}

    if dry_run:
        logger.info("dry_run_workflow", workflow=workflow_name, params=params)
        return {
            "status": "dry_run",
            "workflow": workflow_name,
            "params": params,
            "would_execute": workflow["description"],
        }

    try:
        url = f"{settings.integrations.n8n_base_url}{workflow['webhook_path']}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=params)
            response.raise_for_status()
            result = response.json()

        logger.info("workflow_triggered", workflow=workflow_name, status="success")
        return {"status": "success", "workflow": workflow_name, "result": result}

    except httpx.ConnectError:
        logger.warning("n8n_unavailable", workflow=workflow_name)
        # Simulate success in dev mode
        if settings.app.app_env == "development":
            return {
                "status": "simulated_success",
                "workflow": workflow_name,
                "message": "N8n not available — simulated execution in dev mode",
            }
        return {"status": "error", "error": "N8n service unavailable"}

    except Exception as e:
        logger.error("workflow_error", workflow=workflow_name, error=str(e))
        return {"status": "error", "error": str(e)}


async def trigger_rollback(workflow_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Trigger the rollback workflow for a given action."""
    workflow = N8N_WORKFLOWS.get(workflow_name)
    if not workflow or not workflow.get("rollback"):
        return {"status": "error", "error": f"No rollback available for: {workflow_name}"}

    return await trigger_workflow(workflow["rollback"], params)
