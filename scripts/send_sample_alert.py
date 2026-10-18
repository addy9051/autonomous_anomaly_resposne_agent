"""Send a sample Alertmanager alert to the local API (used by `make demo`)."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--severity", default="critical")
    p.add_argument("--url", default=os.environ.get("API_URL", "http://127.0.0.1:8010"))
    p.add_argument("--token", default=os.environ.get("API_AUTH_TOKEN", "dev-local-token"))
    args = p.parse_args()
    parsed_url = urllib.parse.urlsplit(args.url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        p.error("--url must be an absolute HTTP or HTTPS URL")

    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "severity": args.severity,
                    "service": "payment-gateway",
                    "instance": "pay-1",
                },
                "annotations": {
                    "summary": "5xx error rate 38% on payment-gateway",
                    "description": "Error rate rose from 0.5% to 38% after deploy",
                },
                "startsAt": datetime.now(UTC).isoformat(),
            }
        ]
    }
    req = urllib.request.Request(  # noqa: S310 - args.url is restricted to HTTP(S) above.
        f"{args.url}/api/v1/alerts/alertmanager?dry_run=true",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": args.token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310 - URL scheme is restricted above.
        print(json.dumps(json.loads(resp.read()), indent=2))


if __name__ == "__main__":
    main()
