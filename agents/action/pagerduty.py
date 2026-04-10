"""
PagerDuty Incident Integration.

Handles triggering PagerDuty incidents for Tier 3 actions and high-severity anomalies.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from shared.config import get_settings
from shared.schemas import DiagnosisResult, RecommendedAction
from shared.utils import get_logger

logger = get_logger("pagerduty")


async def trigger_pagerduty_incident(
    action: RecommendedAction,
    diagnosis: DiagnosisResult,
) -> dict[str, Any]:
    """
    Triggers a PagerDuty incident using the REST API.

    Args:
        action: The recommended Tier 3 action.
        diagnosis: The diagnosis result containing context.

    Returns:
        dict containing the outcome and response.
    """
    settings = get_settings()
    api_key = settings.integrations.pagerduty_api_key
    service_id = settings.integrations.pagerduty_service_id

    if not api_key or not service_id:
        logger.info(
            "pagerduty_mocked",
            reason="Missing PAGERDUTY_API_KEY or PAGERDUTY_SERVICE_ID",
            action=action.action,
            incident_id=diagnosis.incident_id,
        )
        return {
            "status": "simulated",
            "message": "PagerDuty credentials not configured. Simulated escalation.",
        }

    url = "https://api.pagerduty.com/incidents"
    headers = {
        "Authorization": f"Token token={api_key}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
        "From": "autonomous-agent@system.local",  # Required by PD API
    }

    # Format the incident payload
    details = {
        "Action Required": action.action,
        "Category": diagnosis.root_cause_category.value,
        "Execution Parameters": action.params,
        "System Confidence": f"{diagnosis.confidence:.2f}",
        "Rollback Strategy": action.rollback_steps,
        "Reasoning": diagnosis.reasoning_chain[:500] + "..." if len(diagnosis.reasoning_chain) > 500 else diagnosis.reasoning_chain
    }

    payload = {
        "incident": {
            "type": "incident",
            "title": f"[SRE Auto-Escalation] Tier 3 Action Required: {action.action}",
            "service": {
                "id": service_id,
                "type": "service_reference"
            },
            "urgency": "high",
            "incident_key": diagnosis.incident_id,  # Deduplication key
            "body": {
                "type": "incident_body",
                "details": json.dumps(details, indent=2)
            }
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            
            logger.info("pagerduty_incident_created", incident_url=result.get("incident", {}).get("html_url"))
            return {
                "status": "success",
                "incident_id": result.get("incident", {}).get("id"),
                "incident_url": result.get("incident", {}).get("html_url"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error("pagerduty_api_error", status_code=e.response.status_code, text=e.response.text)
        return {"status": "error", "message": f"API Error: {e.response.status_code}"}
    except Exception as e:
        logger.error("pagerduty_connection_error", error=str(e))
        return {"status": "error", "message": str(e)}
