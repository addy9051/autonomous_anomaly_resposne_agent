"""Read-only static red-team audit for fully_deployed_saas (multi-tenant SaaS).

Usage:
    python static_audit.py                 # audit working tree
    python static_audit.py --ref HEAD~1     # audit a git ref without checking it out
    python static_audit.py --history        # also scan full git history for burned secrets

Exit code: 1 if any CRITICAL/HIGH rule matches, else 0. No network, no writes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_PREFIX = "fully_deployed_saas/"
SKIP = re.compile(r"(node_modules|__pycache__|\.pyc$|package-lock\.json$|\.png$|\.jpg$)")


@dataclass(frozen=True)
class Rule:
    rid: str
    sev: str
    path: str          # regex on repo-relative path
    pattern: str       # regex on line content
    message: str
    ignore: str = r"(localhost|127\.0\.0\.1|your[-_]|xxxx|placeholder|example|\.\.\.)"  # false-positive filter


RULES: list[Rule] = [
    # --- Secrets -----------------------------------------------------------------
    Rule("SEC-01", "CRITICAL", r".*", r"sre_live_[A-Za-z0-9_-]{30,}", "Full tenant API key literal"),
    Rule("SEC-02", "CRITICAL", r".*", r"postgres(ql)?://[^:\s]+:[^@\s*]{6,}@", "DSN with inline password"),
    Rule("SEC-03", "CRITICAL", r".*", r"sk-or-v1-[a-f0-9]{20,}|sk-proj-[A-Za-z0-9_-]{20,}", "LLM provider key literal"),
    Rule("SEC-04", "HIGH", r".*\.py$", r"print\(.*(database_url|DATABASE_URL|dsn|api_key|secret)", "Secret-bearing value printed"),
    Rule("SEC-05", "HIGH", r".*\.py$", r"logger\.\w+\(.*(database_url|raw_key|api_key=|password)", "Secret-bearing value logged"),
    # --- Tenant isolation ---------------------------------------------------------
    Rule("TEN-01", "CRITICAL", r".*auth_routes\.py$", r"ON CONFLICT \(id\) DO UPDATE", "Registration upsert → tenant takeover (SAAS-001)"),
    Rule("TEN-02", "HIGH", r".*\.py$", r"(UPDATE|DELETE FROM)\s+\w+.*WHERE\s+(incident_id|id)\s*=\s*\$1\s*\"?,?$", "Mutation without tenant_id filter"),
    Rule("TEN-03", "MEDIUM", r".*\.py$", r"limit:\s*int\s*=\s*\d+\s*,?$", "Unbounded client-controlled limit"),
    Rule("TEN-04", "MEDIUM", r".*auth_routes\.py$", r"plan:\s*str\s*=", "Client-controlled billing plan (SAAS-009)"),
    # --- Governance ---------------------------------------------------------------
    Rule("GOV-01", "HIGH", r".*incident_routes\.py$", r"status = 'pending'\s*$", "Non-atomic pending check — verify UPDATE ... WHERE status='pending' RETURNING"),
    Rule("GOV-02", "MEDIUM", r".*diagnosis\.py$", r"action_type=item\.get\(", "LLM action_type accepted without closed catalog (SAAS-006)"),
    # --- Client side --------------------------------------------------------------
    Rule("XSS-01", "HIGH", r".*\.html$", r"innerHTML\s*=.*\$\{", "Template literal into innerHTML (stored XSS risk)"),
    Rule("XSS-02", "HIGH", r".*\.html$", r"^\s*<div[^>]*>\$\{(inc|data)\.", "Unescaped server data interpolated into markup"),
    Rule("XSS-03", "MEDIUM", r".*\.html$", r"onclick=\"[^\"]*\$\{", "Inline handler built from data"),
    Rule("XSS-04", "MEDIUM", r".*\.html$", r"localStorage\.setItem\(['\"]sre_api_key", "API key persisted in localStorage (XSS blast radius)"),
    # --- Network / SSRF -----------------------------------------------------------
    Rule("NET-01", "HIGH", r".*\.py$", r"allow_origins=\[\"\*\"\][^\n]*allow_credentials=True", "Wildcard CORS with credentials"),
    Rule("NET-02", "MEDIUM", r".*adapters\.py$", r"except ValueError:\s*$", "SSRF guard may skip DNS resolution for hostnames (SAAS-007)"),
    Rule("NET-03", "MEDIUM", r".*\.py$", r"follow_redirects\s*=\s*True", "Redirects followed on outbound client"),
    Rule("NET-04", "LOW", r".*main\.py$", r"\"environment\":\s*settings\.app_env", "Health endpoint discloses environment"),
    # --- Runtime ------------------------------------------------------------------
    Rule("RUN-02", "HIGH", r".*\.py$", r"app_env\s*==\s*\"development\"", "Dev auth fallback — ensure APP_ENV=production on Render"),
]
# RUN-01 (non-root container) needs whole-file logic; handled in audit().


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def iter_files(ref: str | None):
    if ref:
        for path in git("ls-tree", "-r", "--name-only", ref, "--", ROOT_PREFIX).splitlines():
            if not SKIP.search(path):
                yield path, git("show", f"{ref}:{path}")
    else:
        for p in Path(ROOT_PREFIX).rglob("*"):
            rel = p.as_posix()
            if p.is_file() and not SKIP.search(rel) and p.suffix in {".py", ".html", ".sql", ".yml", ".yaml", ".toml", ".ts", ".js", ".md", ".example", ""}:
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
                if pre.search(path) and cre.search(line) and not (rule.rid.startswith("SEC") and ire.search(line)):
                    hits.append((rule.sev, rule.rid, path, lineno, rule.message))
        if path.endswith("Dockerfile") and not re.search(r"^USER\s+(?!root)\w+", text, re.M):
            hits.append(("MEDIUM", "RUN-01", path, 0, "Container has no non-root USER directive"))
    return hits


def history_scan() -> list[str]:
    out = []
    for label, pattern in [("tenant key", r"sre_live_[A-Za-z0-9_-]{30,}"), ("neon password", r"npg_[A-Za-z0-9]{10,}"),
                           ("openrouter key", r"sk-or-v1-[a-f0-9]{20,}")]:
        commits = git("log", "--all", "--format=%h %s", "-G", pattern).strip()
        if commits:
            out.append(f"[CRITICAL] HIST  {label} pattern found in history (treat as burned → revoke/rotate):\n    " + commits.replace("\n", "\n    "))
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=None)
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    hits = sorted(audit(args.ref), key=lambda h: (order[h[0]], h[1], h[2], h[3]))
    print(f"SaaS static red-team audit — target: {args.ref or 'working tree'} ({len(hits)} hits)\n")
    for sev, rid, path, lineno, msg in hits:
        print(f"[{sev:8}] {rid:6} {path}:{lineno}  {msg}")
    if args.history:
        print()
        for line in history_scan() or ["[OK] no secret patterns in git history"]:
            print(line)
    blocking = any(h[0] in ("CRITICAL", "HIGH") for h in hits)
    print("\nRESULT:", "BLOCKING findings present — triage before deploy" if blocking else "no blocking patterns")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
