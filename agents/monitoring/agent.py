"""
Monitoring Agent — LangChain ReAct agent for anomaly detection.

Consumes telemetry events (from Kafka or in-memory bus), processes each
through a tool-calling ReAct loop, and outputs AnomalyEvent when an
anomaly is detected with confidence > threshold.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse.callback import CallbackHandler

from shared.llm import get_chat_model

from agents.monitoring.prompts import MONITORING_HUMAN_PROMPT, MONITORING_SYSTEM_PROMPT
from agents.monitoring.tools.monitoring_tools import ALL_MONITORING_TOOLS
from shared.config import get_settings
from shared.schemas import AnomalyEvent, AnomalyType, MetricsSnapshot, Severity, TelemetryEvent
from shared.utils import LLMCostTracker, Timer, get_logger, get_tracer

logger = get_logger("monitoring_agent")
tracer = get_tracer()


class MonitoringAgent:
    """
    Monitoring Agent — the first line of defense.

    Processes telemetry events through a LangChain agent with ReAct
    reasoning and custom tools. Detects anomalies and escalates to
    the Diagnosis Agent when confidence exceeds threshold.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.confidence_threshold = self.settings.agent.anomaly_confidence_threshold

        # Initialize LLM with tool binding using factory
        self.llm = get_chat_model(
            model_name=self.settings.llm.monitoring_agent_model,
            temperature=0.1,
            max_tokens=4096,
        )
        self.llm_with_tools = self.llm.bind_tools(ALL_MONITORING_TOOLS)

        logger.info(
            "monitoring_agent_initialized",
            model=self.settings.llm.monitoring_agent_model,
            confidence_threshold=self.confidence_threshold,
            num_tools=len(ALL_MONITORING_TOOLS),
        )

    async def process_event(
        self,
        event: TelemetryEvent,
        cost_tracker: LLMCostTracker | None = None,
    ) -> AnomalyEvent | None:
        """
        Process a single telemetry event through the monitoring pipeline.

        Returns an AnomalyEvent if an anomaly is detected, None otherwise.
        """
        with tracer.start_as_current_span("monitoring_agent.process_event") as span:
            span.set_attribute("event.id", event.event_id)
            span.set_attribute("event.source", event.source)
            span.set_attribute("event.service", event.service_name)

            # Only initialize Langfuse if credentials are provided
            handler = None
            if self.settings.observability.langfuse_public_key:
                try:
                    handler = CallbackHandler(
                        public_key=self.settings.observability.langfuse_public_key,
                        secret_key=self.settings.observability.langfuse_secret_key,
                        host=self.settings.observability.langfuse_host,
                        session_id=event.event_id,
                        user_id="sre-system",
                        tags=["agent:monitoring", f"env:{self.settings.app.app_env}"],
                        metadata={
                            "agent_version": "1.0.0",
                            "prompt_version": "mon-react-v1",
                            "affected_service": event.service_name
                        }
                    )
                except Exception as e:
                    logger.warning("langfuse_init_failed", error=str(e))
                    handler = None

            timer = Timer()
            with timer:
                try:
                    result = await self._run_react_loop(event, cost_tracker, handler)

                    if result and result.confidence >= self.confidence_threshold:
                        logger.info(
                            "anomaly_detected",
                            event_id=event.event_id,
                            severity=result.severity,
                            confidence=result.confidence,
                            anomaly_type=result.anomaly_type,
                            elapsed_ms=timer.elapsed_ms,
                        )
                        span.set_attribute("anomaly.detected", True)
                        span.set_attribute("anomaly.confidence", result.confidence)
                        return result
                    else:
                        logger.debug(
                            "no_anomaly",
                            event_id=event.event_id,
                            confidence=result.confidence if result else 0.0,
                            elapsed_ms=timer.elapsed_ms,
                        )
                        span.set_attribute("anomaly.detected", False)
                        return None

                except Exception as e:
                    logger.error("monitoring_agent_error", error=str(e), event_id=event.event_id)
                    span.record_exception(e)
                    raise

    async def _run_react_loop(
        self,
        event: TelemetryEvent,
        cost_tracker: LLMCostTracker | None = None,
        handler: CallbackHandler | None = None,
    ) -> AnomalyEvent | None:
        """Run the ReAct reasoning loop with tool calls."""

        # Build the prompt
        human_msg = MONITORING_HUMAN_PROMPT.format(
            telemetry_event=json.dumps(event.model_dump(mode="json"), indent=2, default=str)
        )

        messages = [
            SystemMessage(content=MONITORING_SYSTEM_PROMPT),
            HumanMessage(content=human_msg),
        ]

        # Iterative tool-calling loop (max 5 iterations)
        for iteration in range(5):
            config = {"callbacks": [handler]} if handler else None
            response = await self.llm_with_tools.ainvoke(messages, config=config)

            # Track cost
            if cost_tracker and hasattr(response, "usage_metadata") and response.usage_metadata:
                cost_tracker.track(
                    model=self.settings.llm.monitoring_agent_model,
                    input_tokens=response.usage_metadata.get("input_tokens", 0),
                    output_tokens=response.usage_metadata.get("output_tokens", 0),
                )

            # Check if agent wants to call tools
            if response.tool_calls:
                messages.append(response)
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    logger.debug("tool_call", tool=tool_name, args=tool_args, iteration=iteration)

                    # Execute the tool
                    tool_fn = self._get_tool(tool_name)
                    if tool_fn:
                        result = await tool_fn.ainvoke(tool_args)
                        from langchain_core.messages import ToolMessage
                        messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))
                    else:
                        from langchain_core.messages import ToolMessage
                        messages.append(ToolMessage(
                            content=f"Error: Unknown tool '{tool_name}'",
                            tool_call_id=tool_call["id"],
                        ))
            else:
                # Agent is done reasoning — parse the output
                return self._parse_response(response.content, event)

        # Max iterations reached
        logger.warning("max_iterations_reached", event_id=event.event_id)
        return None

    def _get_tool(self, name: str) -> Any | None:  # noqa: ANN401
        """Look up a tool by name."""
        for tool in ALL_MONITORING_TOOLS:
            if tool.name == name:
                return tool
        return None

    def _parse_response(self, content: str, event: TelemetryEvent) -> AnomalyEvent | None:
        """Parse the LLM response into an AnomalyEvent."""
        try:
            # Try to extract JSON from the response
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0]

            data = json.loads(json_str)

            # Lenient parsing for AnomalyType
            anomaly_type_str = str(data.get("anomaly_type", "latency_spike")).lower()
            
            # Handle delimited strings like "latency_spike|error_rate"
            if "|" in anomaly_type_str:
                anomaly_type_str = anomaly_type_str.split("|")[0].strip()
            
            # Handle "none" or empty strings
            if anomaly_type_str in ["none", "", "null"]:
                anomaly_type_str = "latency_spike"

            # Validate against enum
            try:
                anomaly_type = AnomalyType(anomaly_type_str)
            except ValueError:
                logger.warning("invalid_anomaly_type", received=anomaly_type_str)
                anomaly_type = AnomalyType.LATENCY_SPIKE

            return AnomalyEvent(
                severity=Severity(data.get("severity", "medium")),
                affected_services=data.get("affected_services", [event.service_name]),
                anomaly_type=anomaly_type,
                metrics_snapshot=MetricsSnapshot(**data.get("metrics_snapshot", {})),
                reasoning=data.get("reasoning", content),
                confidence=float(data.get("confidence", 0.5)),
                raw_event=event.payload,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("response_parse_failed", error=str(e))
            # Fallback: create event from the raw text reasoning
            return AnomalyEvent(
                severity=Severity.MEDIUM,
                affected_services=[event.service_name],
                anomaly_type=AnomalyType.LATENCY_SPIKE,
                metrics_snapshot=MetricsSnapshot(),
                reasoning=content,
                confidence=0.5,
                raw_event=event.payload,
            )

    async def process_batch(
        self,
        events: list[TelemetryEvent],
        cost_tracker: LLMCostTracker | None = None,
    ) -> list[AnomalyEvent]:
        """Process a batch of telemetry events. Returns detected anomalies."""
        anomalies = []
        for event in events:
            result = await self.process_event(event, cost_tracker)
            if result:
                anomalies.append(result)
        return anomalies
