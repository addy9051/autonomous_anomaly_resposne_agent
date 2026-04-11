"""
Phase 7 Verification — Learning & Reward Simulation.

Simulates a series of incidents to verify the Feedback Loop and Reward Agent behavior.
Checks that the 'LLM-as-a-Judge' correctly penalizes poor reasoning and rewards success.
"""

import asyncio
import uuid
import sys
import os
from datetime import datetime

# Add project root to path (at index 0 to override site-packages)
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from orchestrator import AgentOrchestrator
from shared.schemas import (
    TelemetryEvent, IncidentRecord, IncidentStatus, AnomalyEvent, 
    MetricsSnapshot, Severity, AnomalyType, DiagnosisResult, 
    RootCauseCategory, RecommendedAction, ActionTier, ActionResult, 
    SemanticReward
)
from shared.utils import LLMCostTracker

async def simulate_learning():
    # Initialize orchestrator (sets up all agents)
    orchestrator = AgentOrchestrator()
    feedback = orchestrator.feedback_agent
    
    print("\n" + "="*60)
    print("PHASE 7: RL LEARNING VERIFICATION (SIMULATION)")
    print("="*60 + "\n")
    
    # Scenarios designed to test the RewardAgent's judgement
    scenarios = [
        {
            "name": "Logical & Successful (Latency Spike)",
            "metrics": {"p99_latency_ms": 5200, "error_rate": 0.01, "cpu_percent": 85},
            "rca": "High CPU utilization on payment-gateway leading to request queuing and P99 latency spikes.",
            "action": "scale_replicas",
            "success": True,
            "expected_reward": "high"
        },
        {
            "name": "Illogical RCA (Error Rate vs Network)",
            "metrics": {"p99_latency_ms": 150, "error_rate": 0.65, "cpu_percent": 10},
            "rca": "Network jitter is causing errors, though metrics show zero packet loss and high application error rate.",
            "action": "flush_cache",
            "success": False,
            "expected_reward": "low"
        },
        {
            "name": "False Positive (Normal Metrics)",
            "metrics": {"p99_latency_ms": 80, "error_rate": 0.001, "cpu_percent": 15},
            "rca": "Minor fluctuation detected, likely a transient blip. No action required.",
            "action": "no_action",
            "success": True,
            "is_fp": True,
            "expected_reward": "neutral/low"
        }
    ]
    
    for i, scenario in enumerate(scenarios):
        print(f"--- Scenario {i+1}: {scenario['name']}")
        
        incident_id = f"sim-{uuid.uuid4().hex[:8]}"
        
        # 1. Create Mock Anomaly
        anomaly = AnomalyEvent(
            severity=Severity.HIGH,
            affected_services=["payment-gateway"],
            anomaly_type=AnomalyType.LATENCY_SPIKE if scenario["metrics"]["p99_latency_ms"] > 1000 else AnomalyType.ERROR_RATE,
            metrics_snapshot=MetricsSnapshot(**scenario["metrics"]),
            reasoning="Simulated anomaly for Phase 7 verification.",
            confidence=0.85
        )
        
        # 2. Create Mock Diagnosis
        diagnosis = DiagnosisResult(
            incident_id=incident_id,
            event_id=anomaly.event_id,
            root_cause=scenario["rca"],
            root_cause_category=RootCauseCategory.APPLICATION,
            recommended_actions=[RecommendedAction(action=scenario["action"], tier=ActionTier.TIER_1_AUTO)],
            confidence=0.75,
            reasoning_chain=f"Observation: {scenario['metrics']}. Conclusion: {scenario['rca']}"
        )
        
        # 3. Create Mock Action Result
        action_result = ActionResult(
            incident_id=incident_id,
            action_taken=scenario["action"],
            tier=ActionTier.TIER_1_AUTO,
            execution_status="success" if scenario["success"] else "failed",
            timestamp=datetime.utcnow()
        )
        
        # 4. Assemble Incident Record
        incident = IncidentRecord(
            incident_id=incident_id,
            status=IncidentStatus.RESOLVED if scenario["success"] else IncidentStatus.ESCALATED,
            anomaly_event=anomaly,
            diagnosis_result=diagnosis,
            action_results=[action_result],
            auto_resolved=scenario["success"] and not scenario.get("is_fp", False),
            false_positive=scenario.get("is_fp", False),
            time_to_mitigate_seconds=45 if scenario["success"] else None,
            resolved_at=datetime.utcnow()
        )
        
        # 5. Run Reward Agent Evaluation
        cost_tracker = LLMCostTracker(incident_id=incident_id)
        print(f"   [Brain] RewardAgent evaluating...")
        
        semantic_reward = await orchestrator.reward_agent.evaluate(incident, cost_tracker)
        
        print(f"   [Score] Quality Score: {semantic_reward.overall_quality_score:.2f}")
        print(f"   [Justification] Justification: {semantic_reward.justification[:120]}...")
        
        # 6. Record in Feedback Loop
        reward = await feedback.record_outcome(incident, semantic_reward)
        
        print(f"   [Reward] Hybrid Reward: {reward:.4f}")
        print(f"   [Stats] Buffer Status: {len(feedback.experience_buffer)} experiences\n")

    # Final Status
    print("="*60)
    print("📊 FINAL POLICY STATUS")
    print("="*60)
    status = feedback.get_policy_status()
    print(f"Policy Version: {status['current_version']}")
    print(f"Total Experiences: {status['buffer_size']}")
    
    print("\nAction Performance Stats:")
    for action, stats in status['action_stats'].items():
        print(f"  🔹 {action:15} | count: {stats['count']} | mean_reward: {stats['mean_reward']:.4f}")
    
    print("\n" + "="*60)
    print("PHASE 7 VERIFICATION COMPLETE")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(simulate_learning())
