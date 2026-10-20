"""Seed taxonomy: stack x failure mode x environment. Every generated runbook starts from one seed."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

# stack -> (collection, service_tags)
STACKS: dict[str, tuple[str, list[str]]] = {
    "PostgreSQL": ("database", ["postgres", "database"]),
    "Redis": ("database", ["redis", "cache"]),
    "Kafka": ("database", ["kafka", "streaming"]),
    "Kubernetes": ("application", ["kubernetes", "k8s"]),
    "NGINX ingress": ("network", ["nginx", "ingress"]),
    "Envoy service mesh": ("network", ["envoy", "service-mesh"]),
    "DNS and CDN": ("network", ["dns", "cdn"]),
    "JVM microservice": ("application", ["jvm", "microservice"]),
    "Node.js API": ("application", ["nodejs", "api"]),
    "OAuth and authentication": ("security", ["auth", "oauth"]),
    "API gateway and WAF": ("security", ["api-gateway", "waf"]),
}

FAILURE_MODES: dict[str, str] = {
    "connection pool exhaustion": "requests queue waiting for a free connection and p99 latency climbs",
    "high latency": "p99 latency is several times its baseline while traffic is normal",
    "elevated 5xx error rate": "the 5xx rate rises sharply and clients see failed requests",
    "memory leak": "memory grows steadily until the process is OOM-killed",
    "CPU saturation": "CPU is pinned near 100 percent and request queues grow",
    "disk space exhaustion": "free disk falls below 10 percent and writes begin to fail",
    "replication or consumer lag": "followers or consumers fall minutes behind the leader",
    "crash loop after deploy": "instances restart repeatedly right after a release",
    "certificate expiry": "TLS handshakes start failing as a certificate nears or passes expiry",
    "thundering herd after restart": "a burst of retries overwhelms the service after a restart",
    "rate limiting misconfiguration": "legitimate clients receive 429 responses unexpectedly",
    "traffic spike": "request volume doubles within minutes and capacity is exceeded",
}

ENVIRONMENTS = ("production", "staging")


@dataclass(frozen=True)
class Seed:
    stack: str
    failure_mode: str
    symptom: str
    environment: str
    collection: str
    service_tags: list[str]

    @property
    def key(self) -> str:
        return f"{self.stack}|{self.failure_mode}|{self.environment}"


def build_seeds() -> list[Seed]:
    return [
        Seed(stack, mode, symptom, env, collection, tags)
        for (stack, (collection, tags)), (mode, symptom), env in itertools.product(
            STACKS.items(), FAILURE_MODES.items(), ENVIRONMENTS
        )
    ]
