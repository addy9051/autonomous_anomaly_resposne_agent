"""Read-only static red-team audit for the enterprise air-gapped agent (main branch).

Usage:
    python static_audit.py                 # audit working tree
    python static_audit.py --ref main      # audit a git ref (e.g. main)
    python static_audit.py --history       # scan git history for leaked credentials

Exit code: 1 if any CRITICAL/HIGH rule matches, else 0. No network, no writes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Paths to exclude from scanning
SKIP = re.compile(r"(node_modules|__pycache__|\.pyc$|package-lock\.json$|\.png$|\.jpg$|evals/reports)")


@dataclass(frozen=True)
class Rule:
    rid: str
    sev: str
    path: str          # regex on repo-relative path
    pattern: str       # regex on line content
    message: str
    ignore: str = r"(localhost|127\.0\.0\.1|your[-_]|xxxx|placeholder|example|\.\.\.)"


RULES: list[Rule] = [
    # --- Secrets & Credentials ---------------------------------------------------
    Rule("ENT-SEC-01", "CRITICAL", r"^(?!tests/|evals/).*", r"AKIA[0-9A-Z]{16}", "AWS Access Key ID literal"),
    Rule("ENT-SEC-02", "CRITICAL", r"^(?!tests/|evals/).*", r"xox[baprs]-[0-9A-Za-z]{10,}", "Slack bot or user token literal"),
    Rule("ENT-SEC-03", "CRITICAL", r"^(?!tests/|evals/).*", r"postgres(ql)?://[^:\s]+:[^@\s*]{6,}@", "PostgreSQL DSN with inline password"),
    Rule("ENT-SEC-04", "HIGH", r"^(?!tests/|evals/).*\.py$", r"print\(.*(database_url|DATABASE_URL|api_key|secret|password)", "Secret-bearing variable printed to stdout"),

    # --- Identity & Gateway Offload ----------------------------------------------
    Rule("ENT-ID-01", "HIGH", r".*api\.py$", r"if\s+environment\s*!=\s*[\"']development[\"']\s+or\s+config\.require_gateway_auth:\s*return\s+identity", "Identity trusted without gateway secret verification in non-dev (ENT-002)"),
    Rule("ENT-ID-02", "HIGH", r".*api\.py$", r"@app\.(post|put|delete)\([^)]*\)(?![\s\S]{1,200}Depends\(verify_auth\))", "Mutating endpoint without verify_auth dependency"),

    # --- Autonomy & Action Policy ------------------------------------------------
    Rule("ENT-ACT-01", "HIGH", r".*policy\.py$", r"_auto_history:\s*dict.*defaultdict", "In-memory rate limiting — resets on restart, scales per replica (ENT-001)"),
    Rule("ENT-ACT-02", "HIGH", r".*policy\.py$", r"allowlist\s*=\s*\{.*auto_action_allowlist", "Verify allowlist intersects strictly with LOW_RISK_ACTIONS"),

    # --- Air-Gap & Egress --------------------------------------------------------
    Rule("ENT-EGR-01", "HIGH", r"agents/.*\.py$", r"httpx\.(Async)?Client\(", "Direct httpx client in agent without guarded_async_client wrapper"),
    Rule("ENT-EGR-02", "HIGH", r"shared/.*\.py$", r"requests\.(get|post|put|delete)\(", "Unchecked synchronous requests call bypassing egress guard"),
    Rule("ENT-EGR-03", "MEDIUM", r"shared/egress\.py$", r"socket\.getaddrinfo", "Check-then-connect DNS rebinding surface (ENT-004)"),

    # --- Infrastructure & Terraform ----------------------------------------------
    Rule("ENT-INF-01", "HIGH", r"infra/.*\.tf$", r"cidr_blocks\s*=\s*\[\s*\"0\.0\.0\.0/0\"\s*\]", "Egress or ingress rule open to 0.0.0.0/0 in air-gapped VPC"),
    Rule("ENT-INF-02", "HIGH", r"infra/.*\.tf$", r"actions\s*=\s*\[\s*\"\*\"\s*\]", "IAM policy with wildcard '*' action grant"),
    Rule("ENT-INF-03", "MEDIUM", r"infra/.*Dockerfile$", r"FROM\s+[\w.-]+:[a-z]+(?<!@sha256)", "Base image unpinned by SHA256 digest"),
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def iter_files(ref: str | None):
    if ref:
        for path in git("ls-tree", "-r", "--name-only", ref).splitlines():
            # Exclude SaaS and node_modules
            if path.startswith("fully_deployed_saas/") or SKIP.search(path):
                continue
            if Path(path).suffix in {".py", ".html", ".sql", ".yml", ".yaml", ".toml", ".ts", ".js", ".md", ".tf", ".example", ""}:
                yield path, git("show", f"{ref}:{path}")
    else:
        for p in Path(".").rglob("*"):
            rel = p.as_posix()
            if rel.startswith("fully_deployed_saas/") or SKIP.search(rel):
                continue
            if p.is_file() and p.suffix in {".py", ".html", ".sql", ".yml", ".yaml", ".toml", ".ts", ".js", ".md", ".tf", ".example", ""}:
                try:
                    yield rel, p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue


def audit(ref: str | None) -> list[tuple[str, str, str, int, str]]:
    hits = []
    compiled = [(r, re.compile(r.path), re.compile(r.pattern), re.compile(r.ignore, re.I)) for r in RULES]
    for path, text in iter_files(ref):
        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, pre, cre, ire in compiled:
                if pre.search(path) and cre.search(line) and not (rule.rid.startswith("ENT-SEC") and ire.search(line)):
                    hits.append((rule.sev, rule.rid, path, lineno, rule.message))
    return hits


def history_scan() -> list[str]:
    out = []
    for label, pattern in [("AWS Key", r"AKIA[0-9A-Z]{16}"), ("Slack Token", r"xox[baprs]-[0-9A-Za-z]{10,}"), ("Private Key", r"BEGIN (RSA |EC )?PRIVATE KEY")]:
        commits = git("log", "--all", "--format=%h %s", "-G", pattern).strip()
        if commits:
            out.append(f"[CRITICAL] HIST  {label} pattern found in git history:\n    " + commits.replace("\n", "\n    "))
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=None, help="Git ref to audit (e.g. main or HEAD)")
    ap.add_argument("--history", action="store_true", help="Scan full git commit history")
    args = ap.parse_args()

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    hits = sorted(audit(args.ref), key=lambda h: (order.get(h[0], 99), h[1], h[2], h[3]))
    print(f"Enterprise Air-Gapped static audit — target: {args.ref or 'working tree'} ({len(hits)} hits)\n")
    for sev, rid, path, lineno, msg in hits:
        print(f"[{sev:8}] {rid:10} {path}:{lineno}  {msg}")
    if args.history:
        print("\n--- Git History Credential Scan ---")
        lines = history_scan()
        if lines:
            for l in lines:
                print(l)
        else:
            print("[OK] No credential patterns discovered in git commit history.")
    blocking = any(h[0] in ("CRITICAL", "HIGH") for h in hits)
    print("\nRESULT:", "BLOCKING findings present — review before merging" if blocking else "No blocking findings.")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
