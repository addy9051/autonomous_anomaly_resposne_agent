# Agent Playbooks (Procedural Skills)

This document explains the **runtime skills** in [`skills/`](../skills): what they are, how the agent selects and uses them, and how to add or change one.

> The red-team skills in `.agents/skills/` are a different thing. They guide a human or IDE assistant doing security reviews and are not loaded by the agent at runtime. See [ENTERPRISE_RED_TEAMING.md](./ENTERPRISE_RED_TEAMING.md).

## 1. Purpose

Skills are the agent's **procedural memory**: plain Markdown playbooks that tell the diagnosis agents *how to behave* for a given kind of incident. Each one states what evidence to gather, how to reason about it, which actions may be proposed, when to escalate and what the output must look like.

They exist so that incident-handling behaviour can be:

- **Edited without code changes.** Change a file, not the graph.
- **Reviewed in git.** Skills are plain files with normal diffs and review.
- **Selected deterministically.** The alert's metadata chooses the skills, not the LLM.
- **Auditable.** The names of the skills used are recorded on the diagnosis result (`skills_used`) and in episodic memory.

## 2. How a skill reaches the agent

```
AnomalyEvent (type, severity, affected_services)
        │
        ▼
SkillRouter.select()            shared/skills.py
  - loads skills/*/SKILL.md (legacy flat skills/*.md also loads)
  - keeps skills whose triggers match the alert
  - sorts by priority (high first), then name
        │
        ▼
SkillRouter.render()            -> "### Skill: <name> (v<version>)\n<body>" per skill
        │
        ▼
DiagnosisState.skills_text      agents/diagnosis/graph.py
        │
        ├─► expert sub-agent prompts   "## Procedural skills"        (agents/diagnosis/experts.py)
        └─► final diagnosis prompt     "## Procedural skills (follow these)"
```

Key points:

- Selection happens once per alert in `DiagnosisAgent.diagnose()`. The names are stored in `last_skills`.
- The skill text is passed through `sanitize_for_llm` before it goes into expert prompts.
- If nothing matches, the agent is told to fall back to general SRE judgement and to say so.
- A malformed skill is logged (`skill_load_failed`) and skipped. It never breaks alert handling.

### Matching rules

A skill matches when **every non-empty** trigger list contains the alert's value:

| Trigger | Alert field |
|---------|-------------|
| `anomaly_types` | `anomaly_type` |
| `severities` | `severity` |
| `services` | `affected_services` (any overlap) |

An empty `triggers: {}` matches everything. Several skills can match one alert, and all of them are rendered into the prompt, highest priority first.

## 3. File format

```markdown
---
name: latency-spike              # required; used in skills_used
description: One-line summary of when to use it.
metadata:
  version: "2"                   # shown in the rendered prompt, default "1"
  priority: 60                   # higher is rendered first, default 50
  triggers:
    anomaly_types: [latency_spike]
    severities: [high, medium, low]
---
# Body: Gather / Reason / Actions / Escalate / Output / Example
```

The existing skills all use the same body headings: **Gather**, **Reason**, **Actions**, **Escalate**, **Output** and **Example**. Keep them for consistency.

## 4. The skills

| Skill | Priority | Triggers | Automated actions | Escalation |
|-------|----------|----------|-------------------|------------|
| `baseline-conduct` | 100 | every alert | n/a: sets conduct rules | n/a |
| `critical-rca-only` | 95 | severity `critical` | **None.** `recommended_actions` must be empty | Always, immediately |
| `security-and-fraud` | 70 | `security`, `fraud_signal` | **None.** Recommend-only | Always to security on-call |
| `latency-spike` | 60 | `latency_spike`, non-critical | `scale_replicas` (1-10), `clear_cache`, `restart_unhealthy_pod` | If scaling does not recover p99, or latency is global |
| `error-rate` | 60 | `error_rate`, non-critical | `restart_unhealthy_pod`, `update_circuit_breaker`, `clear_cache` | If errors persist after one action, or rollback is likely |
| `resource-saturation` | 60 | `resource_saturation`, `volume_anomaly`, non-critical | `scale_replicas`, `clear_cache`, `restart_unhealthy_pod`, `add_rate_limit` | If saturation is cluster-wide or scaling hits its cap |
| `data-quality` | 60 | `data_quality` | **None.** Recommend-only, verification query first | To the data owner |

### What each one does

- **`baseline-conduct`** is the always-on guard. It requires evidence ids (E1..En) for every claim, treats `[SIMULATED]` evidence as a hint rather than proof, and requires evidence gaps to be reported. It forbids internet lookups, prefers the smallest reversible action, and restricts the agent to actions named in the matched skill. It also defines the structured brief (issue, evidence, root cause with confidence, actions, open questions).
- **`critical-rca-only`** turns a critical incident into advisory analysis only: 2-3 competing hypotheses, blast radius, a change timeline and one next read-only diagnostic step. It never proposes remediation.
- **`security-and-fraud`** separates abuse from misconfiguration or legitimate traffic changes. Containment such as blocking or throttling is recommended with its blast radius and rollback, never executed.
- **`latency-spike`** separates application (deploy), database (connections or locks) and network or infrastructure (many services at once) causes.
- **`error-rate`** separates release regressions (step change at a deploy), saturation (gradual rise) and security (401/403 spike). Rollbacks are human-only.
- **`resource-saturation`** separates organic growth, a runaway client and a leak (steady climb without traffic growth).
- **`data-quality`** decides whether the source changed, ingestion failed or a transform regressed, and proposes a read-only check before any repair.

### Example: how they combine

A **critical** `latency_spike` on `checkout`:

- `baseline-conduct` (100) matches because it has no triggers.
- `critical-rca-only` (95) matches on severity.
- `latency-spike` does **not** match, because its severities are only `high`, `medium` and `low`.

The agent therefore produces an RCA with no actions and escalates. For a **medium** `latency_spike`, `baseline-conduct` and `latency-spike` apply, so the agent may propose or take the allowed scaling and cache actions.

## 5. Skills versus enforcement

Skills **guide** the LLM through prompt text. They are not a security boundary. Execution of any action is still gated by the deterministic action policy in [`agents/action/policy.py`](../agents/action/policy.py) and the egress allowlist, regardless of what a skill or the LLM says. Treat the "allowed actions" in a skill as the intended behaviour and the policy as the guarantee.

## 6. Adding or changing a skill

1. Create `skills/<name>/SKILL.md` with the front matter above. Use a unique `name` and bump `version` when you change behaviour.
2. Pick `triggers` as narrowly as possible. Use a priority below `baseline-conduct` (100) and `critical-rca-only` (95).
3. List allowed actions only if they exist in the action policy. Keep rollbacks human-only.
4. Include a short **Example** to anchor the output format.
5. Review the change in a pull request. Verify it loads with:

   ```powershell
   .venv\Scripts\python.exe -c "from shared.skills import SkillRouter; r = SkillRouter(); print([(s.name, s.priority) for s in r.skills])"
   ```

## 7. Known limitations

- **Not SRE-reviewed.** The skills were authored with a model and have not been reviewed by an SRE ([DECISIONS.md](./DECISIONS.md) section 5.1). Review them before production use.
- **No priority caps or signing** (finding ENT-006 in [ENTERPRISE_RED_TEAMING.md](./ENTERPRISE_RED_TEAMING.md)). A new skill with a high priority could outrank the safety skills in the prompt. The recommended mitigations are to make `baseline-conduct` and `critical-rca-only` immutable guards and to add `CODEOWNERS` on `skills/`.
- **Prompt-level guidance only.** The LLM can ignore a skill, so the deterministic policy remains the enforcement layer.
