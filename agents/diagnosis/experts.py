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
from shared.utils import get_logger

logger = get_logger("expert_agent")


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

    async def _retrieve(self, anomaly_context: dict[str, Any]) -> list[Any]:
        """Per-expert RAG: own collection plus the shared policy/SLA collection. Failure = no references."""
        try:
            from knowledge_base.retrieval.search import HybridSearchService

            services = anomaly_context.get("affected_services") or []
            query = (
                f"{anomaly_context.get('anomaly_type', '')} {' '.join(services)} "
                f"{str(anomaly_context.get('reasoning', ''))[:200]}"
            )
            return await HybridSearchService().search(
                query=query, service_tags=services or None, collections=[self.expert_type, "policy"]
            )
        except Exception as exc:
            logger.warning("expert_rag_unavailable", expert=self.expert_type, error=str(exc))
            return []

    async def investigate(self, anomaly_context: dict[str, Any], skills_text: str = "") -> dict[str, Any]:
        """Run domain-specific investigation using only this expert's knowledge collection + policy/SLA docs."""
        prompt = self.prompt_template.format(anomaly_context=json.dumps(anomaly_context, indent=2, default=str))
        references = await self._retrieve(anomaly_context)
        if references:
            prompt += "\n\n## Reference material (internal runbooks / policies / SLAs)\n" + "\n".join(
                f"- {r.title}: {' '.join(r.relevant_steps[:4])}" for r in references
            )
        else:
            prompt += "\n\n## Reference material\nNone found for this domain; say so rather than guessing policy."
        if skills_text:
            prompt += f"\n\n## Procedural skills (follow these)\n{skills_text}"

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
