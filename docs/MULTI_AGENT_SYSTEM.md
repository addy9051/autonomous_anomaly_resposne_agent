# Multi-agent system

Open the editable [Excalidraw diagram](./multi-agent-system.excalidraw) in
[Excalidraw](https://excalidraw.com) to explore or modify the incident-response
flow.

The diagram follows the runtime pipeline: telemetry ingestion and anomaly
detection feed the orchestrator; the diagnosis graph gathers evidence, searches
runbooks, dispatches relevant specialist agents concurrently, and synthesizes
an RCA; then deterministic action policy gates remediation and an independent
verifier determines recovery. Incident state, audit records, and episodic
memory support persistence, duplicate handling, and recurrence feedback.
