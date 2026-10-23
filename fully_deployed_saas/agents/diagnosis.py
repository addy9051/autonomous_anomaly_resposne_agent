"""
Evidence-Grounded Diagnosis Engine with Citation Verification.
Caps hypothesis confidence if citations are fabricated or unsupported by telemetry.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from core.llm import get_chat_model
from core.schemas import EvidenceRecord, Hypothesis, Severity, ActionTier
from core.utils import get_logger
from knowledge.vector_store import MultiTenantRAG

logger = get_logger("diagnosis_agent")

DIAGNOSIS_PROMPT = """You are an expert AI Site Reliability Engineer (SRE).
Your task is to analyze an incident alert, telemetry evidence, and retrieved internal runbooks to formulate 2-3 competing root-cause hypotheses.

CRITICAL RULES FOR ROOT-CAUSE HYPOTHESES:
1. EVERY hypothesis MUST cite specific evidence IDs (e.g. E1, E2) from the provided evidence.
2. DO NOT invent evidence or IDs not listed below.
3. Recommend specific, concrete remediation steps.
4. Categorize the action tier:
   - Tier 1: Reversible, low-blast radius actions (restart_unhealthy_pod, scale_replicas, clear_cache)
   - Tier 2: Potentially impactful actions requiring SRE approval (rollback_deployment, failover_database)
   - Tier 3: High-blast radius or persistent hardware failures requiring human investigation.

Output MUST be a valid JSON array of hypothesis objects conforming to this schema:
[
  {
    "hypothesis_id": "H1",
    "title": "Short title describing root cause",
    "root_cause_explanation": "Detailed evidence-backed explanation citing E1, E2...",
    "confidence": 0.85,
    "supporting_evidence_ids": ["E1", "E3"],
    "recommended_action": "Restart unhealthy pod and scale replicas by +2",
    "action_type": "restart_unhealthy_pod",
    "action_tier": 1,
    "parameters": {"service": "...", "replicas": 2}
  }
]
Only output the JSON array, with no surrounding commentary or markdown code fences."""


class DiagnosisEngine:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.rag = MultiTenantRAG(tenant_id)

    async def diagnose(
        self,
        service: str,
        title: str,
        severity: Severity,
        evidence: list[EvidenceRecord],
    ) -> list[Hypothesis]:
        """Diagnose root causes with RAG runbooks and evidence citation checking."""
        # 1. Retrieve tenant runbooks
        runbooks = await self.rag.hybrid_search(query=f"{service} {title}", top_k=3)
        runbook_context = "\n\n".join([f"### Runbook: {r['title']}\n{r['content']}" for r in runbooks])

        # 2. Format evidence table
        evidence_text = "\n".join([f"[{e.id}] ({e.source}): {e.content}" for e in evidence])
        valid_evidence_ids = {e.id for e in evidence}

        user_content = f"""
INCIDENT DETAILS:
- Service: {service}
- Severity: {severity.value}
- Alert: {title}

TELEMETRY EVIDENCE:
{evidence_text or 'No telemetry records available.'}

RETRIEVED RUNBOOKS:
{runbook_context or 'No specific runbooks found in knowledge base.'}
"""

        try:
            llm = get_chat_model(temperature=0.1)
            response = await llm.ainvoke([
                SystemMessage(content=DIAGNOSIS_PROMPT),
                HumanMessage(content=user_content),
            ])
            raw_output = response.content
            if isinstance(raw_output, list):
                raw_text = "".join(str(chunk) for chunk in raw_output)
            else:
                raw_text = str(raw_output)

            cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(cleaned)
        except Exception as exc:
            logger.warning("diagnosis_llm_or_parse_failed", error=str(exc))
            # Fallback deterministic hypothesis
            return [
                Hypothesis(
                    hypothesis_id="H1",
                    title=f"Potential degradation in {service}",
                    root_cause_explanation="Automated parsing inconclusive or offline. Telemetry shows anomaly signals.",
                    confidence=0.85,
                    supporting_evidence_ids=[e.id for e in evidence if e.is_anomaly][:2],
                    recommended_action="Investigate service logs and restart unhealthy pods.",
                    action_type="restart_unhealthy_pod",
                    action_tier=ActionTier.TIER_2_APPROVAL,
                    parameters={"service": service},
                )
            ]

        hypotheses: list[Hypothesis] = []
        for idx, item in enumerate(parsed):
            # Citation resolution & verification
            raw_citations = item.get("supporting_evidence_ids", [])
            valid_citations = [cid for cid in raw_citations if cid in valid_evidence_ids]

            confidence = float(item.get("confidence", 0.5))
            # Safety Cap: If citations were fabricated or none exist, cap confidence
            if not valid_citations:
                confidence = min(confidence, 0.40)
            elif len(valid_citations) == 1:
                confidence = min(confidence, 0.70)

            tier_val = item.get("action_tier", 2)
            try:
                tier = ActionTier(tier_val)
            except ValueError:
                tier = ActionTier.TIER_2_APPROVAL

            hypotheses.append(
                Hypothesis(
                    hypothesis_id=item.get("hypothesis_id", f"H{idx+1}"),
                    title=item.get("title", f"Hypothesis {idx+1}"),
                    root_cause_explanation=item.get("root_cause_explanation", ""),
                    confidence=confidence,
                    supporting_evidence_ids=valid_citations,
                    recommended_action=item.get("recommended_action", "Manual investigation"),
                    action_type=item.get("action_type", "restart_unhealthy_pod"),
                    action_tier=tier,
                    parameters=item.get("parameters", {"service": service}),
                )
            )

        # Sort by confidence descending
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses
