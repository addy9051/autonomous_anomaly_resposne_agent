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
        prompt = self.prompt_template.format(
            anomaly_context=json.dumps(anomaly_context, indent=2, default=str)
        )

        response = await self.llm.ainvoke([
            SystemMessage(content=f"You are the {self.expert_type.title()} Expert."),
            HumanMessage(content=prompt),
        ])

        return {
            "agent_type": self.expert_type,
            "findings": response.content,
            "severity": "high",  # Could be parsed from response
            "confidence": 0.9,
            "evidence": {}
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
