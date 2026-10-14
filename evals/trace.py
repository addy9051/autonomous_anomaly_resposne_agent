"""Framework-neutral representation of one agent run, plus an adapter from IncidentRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shared.schemas import IncidentRecord

MODE_RECOMMEND_ONLY = "recommend_only"
MODE_AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProposedAction:
    name: str
    tier: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTrace:
    case_id: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    executed_actions: list[str] = field(default_factory=list)
    root_cause: str = ""
    reasoning: str = ""
    summary: str = ""
    available_evidence_ids: set[str] = field(default_factory=set)
    cited_evidence_ids: set[str] = field(default_factory=set)
    mode: str = MODE_RECOMMEND_ONLY

    def text(self) -> str:
        """All free text the agent produced, for string/regex scanning."""
        parts = [self.root_cause, self.reasoning, self.summary]
        for a in self.proposed_actions:
            parts.append(a.name)
            parts.extend(str(v) for v in a.params.values())
        return "\n".join(p for p in parts if p)


@dataclass(frozen=True)
class CaseSpec:
    """Per-case expectations for deterministic checks."""

    allowed_tools: frozenset[str]
    required_tools: frozenset[str] = frozenset()
    allowed_actions: frozenset[str] | None = None
    forbidden_actions: frozenset[str] = frozenset()
    max_tool_calls: int = 12

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaseSpec:
        allowed_actions = d.get("allowed_actions")
        return cls(
            allowed_tools=frozenset(d.get("allowed_tools", [])),
            required_tools=frozenset(d.get("required_tools", [])),
            allowed_actions=None if allowed_actions is None else frozenset(allowed_actions),
            forbidden_actions=frozenset(d.get("forbidden_actions", [])),
            max_tool_calls=int(d.get("max_tool_calls", 12)),
        )


def trace_from_dict(d: dict[str, Any], case_id: str) -> AgentTrace:
    return AgentTrace(
        case_id=case_id,
        tool_calls=[ToolCall(t["name"], t.get("args", {})) for t in d.get("tool_calls", [])],
        proposed_actions=[
            ProposedAction(a["name"], a.get("tier"), a.get("params", {})) for a in d.get("proposed_actions", [])
        ],
        executed_actions=list(d.get("executed_actions", [])),
        root_cause=d.get("root_cause", ""),
        reasoning=d.get("reasoning", ""),
        summary=d.get("summary", ""),
        available_evidence_ids=set(d.get("available_evidence_ids", [])),
        cited_evidence_ids=set(d.get("cited_evidence_ids", [])),
        mode=d.get("mode", MODE_RECOMMEND_ONLY),
    )


def trace_from_incident(incident: IncidentRecord, tool_calls: list[ToolCall] | None = None) -> AgentTrace:
    """Build a trace from a real incident so production runs can be evaluated the same way."""
    diagnosis = incident.diagnosis_result
    proposed = (
        [ProposedAction(a.action, int(a.tier), a.params) for a in diagnosis.recommended_actions] if diagnosis else []
    )
    executed = [r.action_taken for r in incident.action_results if r.execution_status == "success"]
    return AgentTrace(
        case_id=incident.incident_id,
        tool_calls=tool_calls or [],
        proposed_actions=proposed,
        executed_actions=executed,
        root_cause=diagnosis.root_cause if diagnosis else "",
        reasoning=diagnosis.reasoning_chain if diagnosis else "",
        summary=diagnosis.decision_summary if diagnosis else "",
        mode=MODE_AUTONOMOUS if executed else MODE_RECOMMEND_ONLY,
    )
