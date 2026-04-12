"""
Verification script for the Hierarchical Specialized Agents.
Triggers 4 distinct scenarios to test the Supervisor's routing logic.
"""

import asyncio

import httpx

API_URL = "http://localhost:8000/api/v1/events/process"

SCENARIOS = [
    {
        "name": "DATABASE ANOMALY",
        "payload": {
            "source": "verification_script",
            "service_name": "payment-gateway",
            "event_type": "metric",
            "payload": {"db_connections": 150, "db_pool_utilization": 0.98, "p99_latency_ms": 1200},
        },
    },
    {
        "name": "NETWORK ANOMALY",
        "payload": {
            "source": "verification_script",
            "service_name": "global",
            "event_type": "metric",
            "payload": {"p99_latency_ms": 2500, "dns_resolution_failures": 50, "ingress_packet_loss": 0.05},
        },
    },
    {
        "name": "SECURITY ANOMALY",
        "payload": {
            "source": "verification_script",
            "service_name": "auth-service",
            "event_type": "metric",
            "payload": {"http_403_rate": 0.25, "auth_failures_count": 500, "p99_latency_ms": 100},
        },
    },
    {
        "name": "APPLICATION ANOMALY",
        "payload": {
            "source": "verification_script",
            "service_name": "payment-gateway",
            "event_type": "log",
            "payload": {
                "error_msg": "java.lang.OutOfMemoryError: Java heap space",
                "restart_count": 5,
                "circuit_breaker_state": "open",
            },
        },
    },
]


async def run_verification() -> None:
    print("🚀 Starting Specialized Agent Verification...\n")
    async with httpx.AsyncClient() as client:
        for scenario in SCENARIOS:
            print(f"--- Triggering {scenario['name']} ---")
            try:
                response = await client.post(API_URL, json=scenario["payload"], timeout=90.0)
                if response.status_code == 200:
                    data = response.json()
                    # Response format is {"status": "...", "incident": {...}}
                    incident_data = data.get("incident") or {}
                    incident_id = incident_data.get("incident_id", "N/A")
                    print(f"✅ Triggered successfully. Incident ID: {incident_id}")
                else:
                    print(f"❌ Failed to trigger. Status: {response.status_code}")
                    print(f"   Body: {response.text}")
            except Exception as e:
                print(f"❌ Error: {type(e).__name__}: {str(e)}")

            print("Waiting for agent processing...\n")
            await asyncio.sleep(5)  # Give it some time to process

    print("Verification triggers sent. Check your app logs and UI dashboard to confirm specialist routing.")


if __name__ == "__main__":
    asyncio.run(run_verification())
