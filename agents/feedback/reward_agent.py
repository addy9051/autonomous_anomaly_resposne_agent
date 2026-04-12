"""
Reward Agent — LLM-as-a-Judge for Reinforcement Learning.

Evaluates incident resolutions to provide intrinsic reward signals.
Ensures that the agent is not just 'fast' but also 'correct' and 'logical'.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from shared.config import get_settings
from shared.schemas import SemanticReward
from shared.utils import get_logger, get_tracer

if TYPE_CHECKING:
    from shared.schemas import IncidentRecord
    from shared.utils import LLMCostTracker

logger = get_logger("reward_agent")
tracer = get_tracer()


class RewardAgent:
    """
    Expert SRE Evaluator that scores incident resolutions.

    Acts as the 'Intrinsic Reward' generator for the Feedback Loop.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        # Use high-fidelity model (GPT-4o) for evaluation to judge smaller models
        self.llm = ChatOpenAI(
            model=self.settings.llm.diagnosis_agent_model,
            temperature=0,
            openai_api_key=self.settings.llm.openai_api_key,
        )

    async def evaluate(self, incident: IncidentRecord, cost_tracker: LLMCostTracker) -> SemanticReward:
        """
        Evaluate the quality of an incident resolution.

        Args:
            incident: The completed incident record.
            cost_tracker: Tracker to record token usage for this evaluation.

        Returns:
            SemanticReward containing multidimensional scores.
        """
        with tracer.start_as_current_span("reward_agent.evaluate") as span:
            prompt = self._build_prompt(incident)

            try:
                response = await self.llm.ainvoke(
                    [
                        SystemMessage(
                            content="You are a Senior Reliability Engineer (SRE) "
                            "evaluating an autonomous response agent's performance."
                        ),
                        HumanMessage(content=prompt),
                    ]
                )

                # Update cost tracking
                if hasattr(response, "usage_metadata"):
                    cost_tracker.track(
                        self.llm.model_name,
                        response.usage_metadata.get("prompt_tokens", 0),
                        response.usage_metadata.get("completion_tokens", 0),
                    )

                # Parse structured output (strip potential markdown artifacts)
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()

                data = json.loads(content)
                reward = SemanticReward(**data)

                span.set_attribute("overall_score", reward.overall_quality_score)
                span.set_attribute("logical_consistency", reward.logical_consistency)

                logger.info(
                    "reward_evaluation_complete",
                    incident_id=incident.incident_id,
                    score=reward.overall_quality_score,
                    justification=reward.justification[:100] + "...",
                )

                return reward

            except Exception as e:
                logger.error("reward_evaluation_failed", error=str(e), incident_id=incident.incident_id)
                # Fail-safe: neutral reward if evaluation crashes
                return SemanticReward(
                    logical_consistency=0.5,
                    action_relevance=0.5,
                    expert_accuracy=0.5,
                    overall_quality_score=0.5,
                    justification=f"Evaluation pipeline error: {str(e)}",
                )

    def _build_prompt(self, incident: IncidentRecord) -> str:
        """Construct the prompt for LLM-as-a-Judge."""
        anomaly = incident.anomaly_event
        diagnosis = incident.diagnosis_result
        actions = incident.action_results

        # Prepare context strings
        metrics_str = json.dumps(anomaly.metrics_snapshot.model_dump(), indent=2) if anomaly else "N/A"
        actions_list = [{"action": a.action_taken, "status": a.execution_status, "tier": a.tier} for a in actions]

        prompt = f"""
Evaluate the following autonomous incident resolution.
Your goal is to determine if the agent made a logically sound diagnosis and took the most effective action.

### 1. ANOMALY CONTEXT
- **Type**: {anomaly.anomaly_type if anomaly else "Unknown"}
- **Severity**: {anomaly.severity if anomaly else "Unknown"}
- **Observed Metrics**:
{metrics_str}

### 2. AGENT DIAGNOSIS (Root Cause Analysis)
- **Claimed Root Cause**: {diagnosis.root_cause if diagnosis else "N/A"}
- **Internal Category**: {diagnosis.root_cause_category if diagnosis else "N/A"}
- **Reasoning Chain**:
{diagnosis.reasoning_chain if diagnosis else "N/A"}

### 3. ACTIONS EXECUTED
- **Remediation Steps**: {json.dumps(actions_list, indent=2)}
- **Agent Confidence**: {diagnosis.confidence if diagnosis else 0.0}
- **Resolution Time**: {incident.time_to_mitigate_seconds} seconds

### EVALUATION REQUIREMENTS
Rate the performance on a scale of 0.0 to 1.0 for these three dimensions:
1. **logical_consistency**: Did the RCA actually follow from the provided metrics?
   (e.g., if latency is high due to CPU, did they blame the DB?)
2. **action_relevance**: Was the executed action the most surgical and effective choice?
3. **expert_accuracy**: Evaluate the depth of reasoning. Was it superficial or truly investigative?

Return your evaluation as a RAW JSON object (no markdown) with this schema:
{{
    "logical_consistency": float,
    "action_relevance": float,
    "expert_accuracy": float,
    "overall_quality_score": float,
    "justification": "detailed string explaining the score"
}}
"""
        return prompt
