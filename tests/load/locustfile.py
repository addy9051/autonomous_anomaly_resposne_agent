from locust import HttpUser, between, task


class AnomalyAgentUser(HttpUser):
    wait_time = between(1, 2)  # Wait 1-2 seconds between requests

    @task
    def process_anomaly_event(self) -> None:
        """
        Simulate an incoming high-priority latency anomaly event.
        """
        payload = {
            "severity": "high",
            "service_name": "payment-gateway",
            "metrics": {"p99_latency_ms": 1200, "error_rate": 0.04},
        }

        self.client.post("/api/v1/events/process", json=payload)

    @task(3)
    def check_health(self) -> None:
        """
        Simulate constant load balancer health checks.
        """
        self.client.get("/health")
