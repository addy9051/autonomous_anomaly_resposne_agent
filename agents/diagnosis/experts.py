"""
Expert Agent logic for domain-specific investigations.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.diagnosis.prompts import (
    APPLICATION_EXPERT_PROMPT,
    DATABASE_EXPERT_PROMPT,
    NETWORK_EXPERT_PROMPT,
    SECURITY_AUDITOR_PROMPT,
)
from shared.config import get_settings
from shared.llm import get_chat_model


class ExpertAgent:
    def __init__(self, expert_type: str, prompt_template: str) -> None:
        self.expert_type = expert_type
        self.prompt_template = prompt_template
        settings = get_settings()
        self.llm = get_chat_model(
            model_name=settings.llm.diagnosis_agent_model,
            temperature=0.1,
            max_tokens=1024,
        )

    async def investigate(self, anomaly_context: dict[str, Any]) -> dict[str, Any]:
        """Run domain-specific investigation."""
        prompt = self.prompt_template.format(anomaly_context=json.dumps(anomaly_context, indent=2, default=str))

        response = await self.llm.ainvoke(
            [
                SystemMessage(content=f"You are the {self.expert_type.title()} Expert."),
                HumanMessage(content=prompt),
            ]
        )

        # Extract observable telemetry signals from context for provenance
        metrics = anomaly_context.get("metrics_snapshot", {})
        observed_signals = {k: v for k, v in metrics.items() if v is not None} if isinstance(metrics, dict) else {}

        return {
            "agent_type": self.expert_type,
            "findings": response.content,
            "severity": anomaly_context.get("severity", "medium"),
            "confidence": 0.75,  # Calibrated qualitative estimate for generated analysis
            "evidence": {
                "observed_signals": observed_signals,
                "domain": self.expert_type,
                "analysis_type": "generated_expert_assessment",
                "is_verified_evidence": False,
            },
        }


class DatabaseExpert(ExpertAgent):
    def __init__(self) -> None:
        super().__init__("database", DATABASE_EXPERT_PROMPT)


class NetworkExpert(ExpertAgent):
    def __init__(self) -> None:
        super().__init__("network", NETWORK_EXPERT_PROMPT)


class SecurityExpert(ExpertAgent):
    def __init__(self) -> None:
        super().__init__("security", SECURITY_AUDITOR_PROMPT)


class ApplicationExpert(ExpertAgent):
    def __init__(self) -> None:
        super().__init__("application", APPLICATION_EXPERT_PROMPT)
