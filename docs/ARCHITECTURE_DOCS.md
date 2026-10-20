# Technical Architecture

This document describes what the code does today. For the reasoning behind each choice see [DECISIONS.md](./DECISIONS.md); for isolation and the AWS design see [DEPLOYMENT_SECURITY.md](./DEPLOYMENT_SECURITY.md); for evaluation see [LLM_EVALS.md](./LLM_EVALS.md).

Status key: **built** = in code and covered by tests; **mocked** = code path exists but the integration is simulated; **designed** = docs/Terraform only, never applied.

## 1. Summary

A sandboxed SRE intelligence layer. An alert starts an investigation: the system collects evidence, produces evidence-grounded root-cause hypotheses, and then, depending on incident severity and a deterministic policy, proposes or runs a limited set of reversible actions. It is a local demo, not a production operator.

- **Critical incidents:** root-cause analysis only. No action is proposed or executed (enforced in the orchestrator, `ActionAgent` and `DeterministicActionPolicy`).
- **Non-critical incidents:** Tier 1 reversible actions may auto-run if allowlisted and enabled; Tier 2 needs approval; Tier 3 is human-only.
- **Models:** open-weight models behind an OpenAI-compatible endpoint (vLLM; Modal in dev). The app only talks to `LLM_BASE_URL`.
- **Remediation executors are mocked.** n8n workflows return "mocked" without an API key.

## 2. Tech stack

| Area | Implementation |
| --- | --- |
| API | FastAPI (`api.py`, port 8010 in the Makefile) |
| Orchestration | `AgentOrchestrator` (`orchestrator.py`) |
| Diagnosis workflow | LangGraph, 4 nodes, in-memory `MemorySaver` checkpointer |
| LLM access | LangChain `ChatOpenAI` / `OpenAIEmbeddings` pointed at a self-hosted vLLM endpoint (`shared/llm.py`) |
| Egress control | Fail-closed host allowlist (`shared/egress.py`) |
| Knowledge base | PostgreSQL + pgvector; hybrid retrieval (see section 4) |
| Persistence | Postgres incident store, audit log, agent controls and episodic memory (`shared/incident_store.py`, `shared/episodic_memory.py`, migrations 001-004); in-memory backends for tests |
| Observability | OpenTelemetry tracing, structlog, self-hosted Langfuse, Prometheus |
| Frontend | Next.js dashboard (`dashboard/`) and a static HTML control page (`ui/`) |
| Evaluation | Deterministic checks, guardrails and an LLM judge (`evals/`), DeepEval retrieval A/B |
| Infra | `docker-compose.yml` (Postgres+pgvector, Prometheus, Langfuse), `infra/modal/vllm_app.py` (dev inference), `infra/aws/terraform` (**designed**, never applied) |

## 3. Pipeline

```
Alertmanager webhook / telemetry event / demo scenario
        |
        v
Monitoring Agent (telemetry path only)  -- Alertmanager alerts skip this step and map labels to severity
        |
        v
Episodic memory: fingerprint de-duplication
        |
        v
Diagnosis Agent (LangGraph)
  gather_context -> rag_runbook_lookup -> supervisor -> synthesise_rca
        |
        v
Severity branch
  critical     -> RCA only, no action
  non-critical -> Action Agent -> DeterministicActionPolicy -> tier handling
        |
        v
Recovery Verifier (after any executed action; fails closed to INCONCLUSIVE)
        |
        v
Incident store + audit log + regression detector
```

### 3.1 API and orchestration (`api.py`, `orchestrator.py`) - built
- Routes cover health (`/health`, `/api/v1/health/detailed`), intake (`/api/v1/events/process`, `/api/v1/alerts/alertmanager`), demo scenarios, streaming, connectors (list/select/credentials/test/activate/deactivate), incidents (list, active, detail, brief, approve, feedback), audit, agent controls (`GET/PUT /api/v1/agent/controls`), episodes, action tiers, metrics and the knowledge base (`seed`, `search`, `health`).
- `AgentOrchestrator.process_event` / `process_anomaly` run the pipeline above; `run_streaming` and `run_batch` drive synthetic telemetry.
- Approvals are claimed atomically from the store and bound to a proposal hash (`compute_proposal_hash` in `agents/action/policy.py`).

### 3.2 Monitoring Agent (`agents/monitoring/`) - built
- A LangChain tool-calling loop over `TelemetryEvent`s, with PII masking first. It emits an `AnomalyEvent` when confidence meets the threshold (default 0.75).
- Tools: `prometheus_query`, `baseline_compare` (Z-score against rolling baselines) and `anomaly_classifier` (scikit-learn Isolation Forest).
- Severity on this path is proposed by the LLM; there is no deterministic business-impact rubric. On the Alertmanager path severity comes from the alert label (`data_pipeline/connectors/alertmanager_intake.py`, missing or unknown maps to MEDIUM).

### 3.3 Diagnosis Agent (`agents/diagnosis/`) - built
- Graph: `gather_context` -> `rag_runbook_lookup` -> `supervisor` -> `synthesise_rca`.
- `supervisor` dispatches the specialists (`DatabaseExpert`, `NetworkExpert`, `SecurityExpert`, `ApplicationExpert` in `experts.py`). Each retrieves from its own collection plus the shared `policy` collection through the same `HybridSearchService`.
- `evidence.py` and `hypotheses.py`: evidence gets citable ids; invented citations are dropped; confidence is capped at 0.4 with no real support and at 0.7 with a single record or any contradiction. A failing data source is recorded as an evidence gap, never filled with mock data. These caps are heuristics, not calibrated probabilities.
- Prompt "skills" for each incident class live in `skills/*/SKILL.md` and are loaded by `shared/skills.py`.

### 3.4 Action Agent (`agents/action/`) - built, executors mocked
- `tiers.py`: Tier 1 reversible actions, Tier 2 approval-required, Tier 3 human-only. Unknown actions default to Tier 3.
- `policy.py` (`DeterministicActionPolicy.evaluate`): critical is always denied; unregistered workflows are denied; parameters are validated against typed bounds; high-risk actions are recommend-only; Tier 1 actions may auto-run only if allowlisted, `AUTO_REMEDIATION_ENABLED` is set, the service is in `AUTO_ACTION_TARGET_SERVICES` and the hourly limit is not reached; everything else needs approval in production.
- `workflows.py`: n8n workflow registry and webhook client. `pagerduty.py`: PagerDuty client for Tier 3 escalation. Both are inactive without credentials and the egress allowlist.

### 3.5 Recovery verification (`agents/verification/verifier.py`) - built
- Independently re-reads signals after an executed action and returns recovered, not recovered or INCONCLUSIVE. Stale, simulated or missing signals fail closed to INCONCLUSIVE.

### 3.6 Feedback and regression (`agents/feedback/regression.py`, `evals/`) - built
- `RegressionDetector` flags a new incident that recurs after a previously "recovered" one.
- There is no reinforcement-learning or online-policy component. Human feedback is recorded through `POST /api/v1/incidents/{id}/feedback`; the judge-vs-human gate in `evals/` has no human labels yet, so it is **not** passing.

### 3.7 Data pipeline (`data_pipeline/`) - built
- `connectors/adapters/`: Prometheus, Datadog and Splunk adapters plus a simulator, selected by `factory.py`, with query templates and deploy-event ingestion.
- `alertmanager_intake.py`: Alertmanager payload to `AnomalyEvent`, with keyword anomaly-type classification.
- `synthetic_producer.py` and `story_simulation.py`: synthetic telemetry and scenarios for demos and tests. All data in the demo is synthetic.
- `flink_jobs/anomaly_features.py`: sliding-window feature extraction and alert de-duplication, run in-process (no Flink cluster).
- `shared/pubsub.py`: Pub/Sub wrapper used by the synthetic producer; not part of the production design.

## 4. Knowledge base and retrieval (`knowledge_base/`) - built

- **Ingestion** (`ingestion/pipeline.py`): chunks runbooks (512 characters, 64 overlap), embeds them and upserts into the `documents` table with `collection` and `service_tags`. Eight sample runbooks are built in; `ingest_directory` loads `<root>/<collection>/*.md`.
- **Retrieval** (`retrieval/search.py`, `HybridSearchService`):
  1. Dense vector search (pgvector cosine), up to `rag_candidates_per_leg` (50) results.
  2. Sparse BM25, up to 50 results. Primary: ParadeDB `pg_search` (`USING bm25` index from migration 005, `|||` match, `pdb.score`, Top-K pushed into the index, collection and service filters applied in-query). Fallback: in-process Okapi BM25 (`retrieval/bm25.py`) when the extension or index is missing (`RAG_BM25_BACKEND=auto`; `paradedb` forces it and raises on error; `inprocess` forces the fallback). Existing databases need the `paradedb/paradedb` image and migration 005 applied manually.
  3. FlashRank cross-encoder (`retrieval/rerank.py`, `ms-marco-TinyBERT-L-2-v2`, local ONNX) scores the union of both candidate sets.
  4. Weighted Reciprocal Rank Fusion (k=60) over the vector, BM25 and reranker rankings; the top `rag_top_k` (5) are returned as `RunbookReference`s.
  - If reranking is disabled or fails, it falls back to two-way RRF. The FlashRank model must be pre-provisioned in `rag_rerank_cache_dir` because the egress allowlist blocks downloads.
- **Limits:** the in-process fallback loads the whole table per query (small sets only), and ParadeDB behaviour at ~1M chunks is unmeasured; ParadeDB uses no stemming, so scores differ from the fallback (harmless for rank-based RRF); the unused `pg_trgm` indexes from migrations 001 and 002 remain; retrieval quality is measured on a small engineer-written set (`evals/retrieval_ab.py`) and on a synthetic-derived live set (`evals/retrieval_live.py`, section 4.1).
- Runbooks are reference material, not permission to act; only the action policy authorises actions.

### 4.1 Knowledge-base corpus and poisoning guards (trust filter and stored Jev score wired into serving)

**Corpus** (ingested into ParadeDB, 13,784 chunks): 12,953 real (OpenShift alerts, prometheus-operator, GitLab runbooks, Kubernetes and PostgreSQL docs; licence and origin in `metadata`, manifest in `knowledge_base/synthetic/provenance.py`), 649 synthetic runbooks grounded on the real ones (`knowledge_base/synthetic/build.py`, deterministic quality gate), and 182 labelled adversarial chunks (8 attack types, `trust_tier=untrusted`). Sources whose licence forbids generation (SRE book, postmortems, Redis, nginx/envoy docs) are ingestible but blocked from grounding; licences in the manifest are my recollection and must be verified.

**Retrieval eval on the live KB** (`evals/retrieval_live.py`, 123 queries, k=5; queries and ground truth come from the same taxonomy that generated the synthetic runbooks, which favours lexical matching, so treat as a regression and poisoning gate, not a general quality claim):

| Strategy | target hit@5 | MRR |
|---|---|---|
| vector only | 0.902 | 0.772 |
| BM25 only | 0.756 | 0.600 |
| RRF only | 0.862 | 0.774 |
| reranker only | 0.943 | 0.814 |
| three-way fusion (production) | 0.919 | 0.837 |

Poisoned *documents* appeared in the top 5 for 37% of queries on the production path, but poisoned *chunks carrying a payload* (matching the gate's injection, secret or destructive-command regexes; 23 of 182) appeared for none of the strategies. Filtering on `trust_tier` removed all poisoned results for about one point of hit rate. **The serving path now applies the trust-tier filter and the stored guard score (see Implemented design below); the eval strategies above were measured before that change.**

**Guard comparison** (`evals/guard_benchmark.py`, `evals/guards.py`; chunk level, threshold 0.5; 649 clean synthetic, 100 to 400 sampled real chunks):

| Chunk type | Local DeBERTa (ProtectAI `deberta-v3-base-prompt-injection-v2`) | TypeSafe Jev 1.13 (`Noul` question via `langchain-typesafe`) |
|---|---|---|
| Injection payload (8) | 75% flagged | 100% |
| Secrets/PII-bait payload (7) | 57% | 100% |
| Dangerous-procedure payload (8) | 0% | 100% |
| Contradiction, wrong-scope, near-duplicate, decoy, keyword-stuffing | 0-18% | 0-32% (mostly chunks that carry a payload) |
| False positives, clean synthetic | 0% | 0% |
| False positives, real | 0.3% (400 chunks) | 0% (100 chunks) |

| Trade-off | Local DeBERTa | TypeSafe Jev |
|---|---|---|
| Runs inside the sandbox (no egress) | Yes, weights in `models/prompt-injection-v2` (Apache-2.0, upstream archived) | No, hosted API; chunk text leaves the boundary; `TYPESAFE_API_KEY` required |
| Cost and latency | Free; CPU inference (latency not benchmarked separately) | $0.042 per million input tokens; network round trip (latency not measured) |
| Flexible, custom question | No, injection vs benign only | Yes, but my question wording is untuned |
| Weakness | Misses dangerous commands and some secrets bait | Version drift (`jev-latest` moves; pin `jev-1.13.0`); external dependency |

Neither guard reliably detects contradiction, wrong-scope or near-duplicate poisoning; that relies on provenance and trust tier and on the action tiers. In the retrieval eval, the guard strategies matched or barely differed from the baseline (hit 0.919, MRR 0.833 to 0.837) because payload chunks never reached the top 5, so that eval shows the guards cost nothing in ranking quality, not that they help.

**Hardened evaluation** (`python -m evals.poison_holdout`, data in `evals/poison_holdout_data.py`). Two checks address the weaknesses above.

*Independent holdout:* 21 hand-written attacks and 12 benign items. The run asserts that no attack matches the gate regexes. Benign items include trigger words used legitimately (`rm -rf /tmp/build-cache`, `password: <redacted>`, an on-call email address).

| Detector (threshold 0.5) | Attack recall | Benign false positives | Notes |
|---|---|---|---|
| Regex gate | 0% | 33% | By construction on the attack side; fires on legitimate trigger words |
| Local DeBERTa | 19% | 17% | Misses all destructive-command and all subtle items; flags benign `b-2` and `b-7` |
| Jev | 100% | 8% (1 of 12) | Flags `b-2` (credential rotation). At 0.9 recall falls to 71%; at 0.1 false positives rise to 50% |

The 100% Jev result includes my contradiction and wrong-scope items, which are blatantly absurd (pool size 5000, edit nginx for Redis). Subtle versions would probably score lower, and the Jev question mentions "guidance that is clearly wrong", so the recall is not evidence that Jev catches real subtle poisoning. The set is small (33 items) and written by the same author as the question, so the figures are directional.

*Attacker-aware retrieval:* 46 queries built from the 23 payload chunks (title, and opening sentence), against the live KB, k=5.

| Strategy | Payload chunk in top 5 | Target chunk itself in top 5 |
|---|---|---|
| fusion_3way (production) | 93.5% | 45.7% |
| fusion_3way_trusted | 0% | 0% |
| fusion_3way_classified (DeBERTa) | 65.2% | 28.3% |
| fusion_3way_jev | 0% | 0% |

This reverses the earlier reassurance: payload chunks never surfaced for incident-symptom queries, but anyone who can steer a query (or write a document that resembles real incident text) gets them into the top 5 almost every time. The local classifier removes 29 points of that exposure and still leaks dangerous-procedure and secrets chunks. Jev and the trust filter remove all of it, on this set.

Conclusions: (1) the trust-tier filter is the cheapest control that works here and must be on by default, but it only holds while provenance labels are correct; (2) Jev is the only content guard that generalised to the independent attacks, at the cost of one false positive and a hosted dependency; (3) the local classifier is not sufficient alone for this threat model; (4) none of this tests an attacker who is aware of the Jev question wording. **Remaining caveats:** 23 payload chunks and 33 holdout items; payload labels in the targeted run still use the gate regexes. **Implemented design:** (1) every retrieval query (vector, ParadeDB BM25 and the in-process fallback) excludes `trust_tier=untrusted` rows (`rag_exclude_untrusted`, default on) and rows whose stored `guard_score` is at or above `rag_guard_threshold` (default 0.5; null disables); unscored rows (NULL) pass, so the trust tier is the base control. (2) Migration `006_guard_score.sql` adds `guard_score`, `guard_model` and `guard_scored_at`; `python -m knowledge_base.score_guard` fills them with Jev (pinned `jev-1.13.0`) after each ingest, so the hosted call is never on the incident path. Not implemented: the local classifier as a query-time secondary signal and a re-score policy when the Jev question changes (use `--rescore`). Scoring during ingestion is built but off by default: set `RAG_GUARD_ON_INGEST=true` and `RunbookIngestionPipeline` scores each chunk (as `title` plus `content`, with the pinned `rag_guard_model`) before the upsert. A chunk whose scoring call fails is stored with `guard_score` NULL, which retrieval keeps, so a hosted-API outage never blocks ingestion but leaves unscored rows; `python -m knowledge_base.score_guard` backfills them. The upsert overwrites the guard columns on re-ingest, so a changed chunk cannot keep a stale score. Tests: `tests/unit/test_ingest_guard.py`. At the time of writing all but 2 of the 13.6k chunks are scored (124 real chunks flagged at 0.5); the hosted API saw that text. Tests: `tests/unit/test_search_safety.py` (SQL and argument checks, plus a live integration test gated on `PARADEDB_TEST_DSN`). Earlier eval scores are cached in `data/raw/guard_scores_*.json`; run `python -m evals.guard_benchmark --guard local|jev`.

**Filtered-serving measurements** (live KB, all 13,600 of 13,602 non-failed chunks scored; 2 failed and pass unscored). Retrieval eval, `fusion_3way`, k=5, 123 queries:

| Filter | hit@5 | MRR | Poisoned doc in top 5 | Payload chunk in top 5 (attack queries) |
|---|---|---|---|---|
| Off | 0.919 | 0.838 | 37.4% | 93.5% |
| Trust tier + guard < 0.5 | 0.911 | 0.827 | 0% | 0% |
| Trust tier + guard < 0.9 | 0.911 | 0.827 | 0% | not run |

The hit-rate cost is one query of 123 and the cause is unconfirmed. With the trust filter off, a guard threshold of either 0.5 or 0.9 alone blocks all 23 payload chunks (their lowest Jev score is 0.93), so the guard is an independent control for mislabelled documents on these blatant payloads. This eval cannot separate 0.5 from 0.9, because no synthetic target is flagged.

The threshold's real cost falls on real runbooks. Flagged at 0.5 / 0.9: gitlab-com 101 / 4 of 6,052, openshift 16 / 0 of 1,910, postgresql.org docs 3 / 1 of 1,284, postmortems 3 / 0 of 1,120, prometheus-operator 1 / 0 of 324, none from the SRE book, Kubernetes, Redis or nginx (124 / 5 of 12,953 in total). A read of a random sample of 14 flagged real chunks found no malicious content: they are legitimate operational procedures that contain delete or destroy steps (deleting a Patroni replica's VM and disks, removing a namespace from the search index, clearing Ceph flags, deferring Sidekiq jobs), postmortem narratives of an operator error (wiping the wrong database directory), and one deprecated alert that says it can be ignored. So at 0.5 the flags on real data are false positives for this use. Their scores are low: of the 124 chunks flagged at 0.5, 55 score 0.5-0.6, 23 score 0.6-0.7, 21 score 0.7-0.8, 19 score 0.8-0.9 and 6 score 0.9 or higher, whereas the lowest score among the 23 payload chunks is 0.93. That suggests a threshold between 0.8 and 0.9 could keep recall on blatant payloads and drop few real runbooks, but I have not re-measured holdout recall at that point (0.9 gave 71%, 0.5 gave 100%), so the default stays 0.5 until that is measured. On the independent holdout, 0.5 gave 100% attack recall with 8% benign false positives and 0.9 gave 71% recall with 0%. Default stays 0.5 (security first); `RAG_GUARD_THRESHOLD=0.9` is the relaxed setting if recall of real runbooks matters more.

## 5. Isolation and safety - built (application level)

- `shared/egress.py`: private ranges are always allowed; public hosts need `EGRESS_ALLOWED_HOSTS`. Applied to the LLM client and every HTTP client, with a startup self-test.
- `shared/pii.py`: regex PII masking before model calls.
- Deterministic action policy and approval hashing (section 3.4).
- There is no OS-level, container or tenant isolation. The private AWS VPC (no internet route, VPC endpoints) is **designed**, not applied. In dev, Modal endpoints are public HTTPS protected by an API key, acceptable for synthetic data only.

## 6. Known gaps

- Remediation executors (n8n, PagerDuty, Slack) are mocked or inactive by default.
- Severity on the telemetry path is LLM-proposed, with no deterministic impact rubric.
- The AWS stack has never been applied; the Terraform assumptions need reconciling with the Postgres incident store.
- No human-labelled judge gate and no calibrated confidence.
- The LangGraph checkpointer is in-memory, so diagnosis state is lost on restart.
- Retrieval is evaluated on a live 13.8k-chunk KB, but the queries come from the same taxonomy as the synthetic runbooks, and `HybridSearchService` now applies a trust-tier filter and a stored Jev `guard_score` ceiling (section 4.1).
- Single GPU inference server, no failover.

## 7. Production-readiness checklist

**Current status: not production-ready.** This is a tested prototype. Retrieval and poisoning defences are measured on synthetic and small hand-written sets; everything downstream of diagnosis is simulated. Each item names how it is verified; nothing is ticked until that evidence exists. Nothing below is done unless stated in sections 3 to 6.

Order of work: A and B gate any real data; C gates any action; D to F gate scale.

### A. Blockers before any real data or users

| # | Item | Verified by | Status |
|---|---|---|---|
| A1 | Apply the AWS stack to a staging account (private VPC, no internet route, VPC endpoints); reconcile Terraform with the Postgres incident store | `terraform apply` in staging; egress self-test passes; no public route to the LLM or DB | not started (designed only) |
| A2 | Replace public Modal endpoints with private inference inside the VPC | LLM and embedding calls work with no public egress | not started |
| A3 | Persistent LangGraph checkpointer (Postgres) | Kill the API mid-diagnosis, restart, and the incident resumes or fails closed | not started (in-memory today) |
| A4 | Data-handling decision for the hosted Jev guard: chunk text leaves the machine; zero retention is enterprise-only | Written approval from the data owner, or a self-hosted/private-gateway alternative | not started |
| A5 | Secrets management (no keys in `.env`; rotate the API keys used in dev) | Secrets from a manager; `.env` absent in deployed images | not started |
| A6 | Authn/authz and per-tenant isolation on the API; OS or container isolation for workers | Pen-test or security review; cross-tenant access test fails closed | not started (application-level only) |
| A7 | Verify corpus licences recorded in the provenance manifest (my recollection, unverified) | Legal sign-off per source; blocked sources absent from the DB | not started |

### B. Evaluation and trust (the "reliable results" gate)

| # | Item | Verified by | Status |
|---|---|---|---|
| B1 | Shadow mode: run read-only against one real team's alert stream; no action executes | SREs rate each diagnosis; labels stored | not started |
| B2 | Human labels for the judge-vs-human gate | Gate passes on real labels (`make judge-gate`) | not passing (no labels) |
| B3 | Calibrate confidence (today: heuristic caps, 0.4 / 0.7) | Reliability diagram on B1 labels; thresholds set from it | not started |
| B4 | Deterministic business-impact severity rubric on the telemetry path | Rubric tests; LLM-proposed severity can only raise, never lower, the rubric's level | not started |
| B5 | Retrieval eval on real incidents and independently written queries (today: synthetic-derived, lexical-friendly) | hit@5 and MRR on a human-written set, with recall@5 added | not started |
| B6 | Fix the 3 failing tests in `tests/unit/test_judge_independence.py` | Green `make test-unit` | failing (pre-existing) |
| B7 | Regression gate in CI: unit, eval and poisoning holdout | CI blocks merge on failure | not started |

### C. Action safety (gates any executor)

| # | Item | Verified by | Status |
|---|---|---|---|
| C1 | Real executors for Tier 1 reversible actions only (n8n, PagerDuty, Slack are mocked today) | Staging run: action executes, verifier returns recovered, rollback works | not started (mocked) |
| C2 | Critical incidents stay analysis-only; Tier 3 stays human-only | Existing orchestrator and policy tests, plus an audit review in shadow mode | built, unchanged |
| C3 | Start auto-remediation disabled; enable per service after B1 results; keep the hourly limit | `AUTO_REMEDIATION_ENABLED` off by default; allowlist reviewed | built; default off |
| C4 | Approval and audit trail reviewed with the on-call team | Tabletop exercise: approve, reject, replay | not started |
| C5 | Kill switch to disable all execution without a deploy | Toggle tested under load | partial (`PUT /api/v1/agent/controls`; behaviour under failure untested) |

### D. Knowledge-base poisoning controls

| # | Item | Verified by | Status |
|---|---|---|---|
| D1 | Trust-tier exclusion and stored `guard_score` ceiling at retrieval | `tests/unit/test_search_safety.py`; attack queries return 0 payload chunks | built (section 4.1) |
| D2 | Scoring during ingest, failing open to NULL | `tests/unit/test_ingest_guard.py` | built, opt-in (`RAG_GUARD_ON_INGEST`) |
| D3 | Turn on ingest scoring and backfill NULL rows; alert on any NULL rows older than N hours | Query: no unscored rows; alert fires on a seeded NULL | 2 chunks unscored; alerting not built |
| D4 | Pick the guard threshold from a sweep (0.7, 0.8, 0.85); default 0.5 drops about 1% of real runbooks (124 of 12,953), mostly false positives in a 14-chunk sample | Holdout recall and real-chunk loss at each threshold | not done |
| D5 | Independently written attacks beyond the 33-item holdout, including attackers who know the guard question | Larger red-team set; subtle poisoning (contradiction, wrong-scope) has no content control | not done |
| D6 | Write access to the KB is access-controlled and audited; provenance labels (`trust_tier`) cannot be set by uploaders | Access review; test that uploads default to `untrusted` | not started |
| D7 | Re-score policy when the Jev question or model changes (`score_guard --rescore`) | Version recorded in `guard_model`; rescore runbook | partial |
| D8 | Explain the unexplained one-query hit-rate drop from the filter (hit 0.919 to 0.911) | Reproduce, then confirm the cause | open |

### E. Scale and cost (decide the target before building)

An incident-response agent serves a small number of SRE teams per customer, so the scaling risk is the blast radius of what it can touch and the cost per incident, not user count. Define the target load first.

| # | Item | Verified by | Status |
|---|---|---|---|
| E1 | Load test the single vLLM server: throughput, latency, cost per incident | Measured p50/p95 and cost at the target load | not measured |
| E2 | Inference failover (single GPU, no failover today) | Failure drill: kill the server, incidents degrade safely | not started |
| E3 | ParadeDB BM25 and pgvector HNSW at realistic size (today 13.8k chunks; ~1M unmeasured): build time, recall, p95 latency | Benchmark at 100k and 1M chunks | not measured |
| E4 | Replace O(n^2) dedup with MinHash-LSH past about 50k documents | Dedup at 100k documents within budget | not started |
| E5 | Guard latency and hosted-API cost on bulk ingest (per-chunk call, not benchmarked) | Timed ingest of 10k chunks | not measured |
| E6 | Taxonomy variety caps synthetic generation at 264 seeds | Bigger taxonomy, or stop relying on synthetic data | not started |
| E7 | Grounding picker is sometimes loose; add a relevance threshold | Spot-check precision on a sample | not started |

### F. Operations

| # | Item | Verified by | Status |
|---|---|---|---|
| F1 | Observability: traces, retrieval metrics, guard flag rate, action outcomes | Dashboards and alerts live (Langfuse and Prometheus exist in dev only) | partial |
| F2 | Backups and restore drill for Postgres (a dump exists locally only) | Timed restore into a clean instance | not started |
| F3 | Migrations run in a controlled way (existing volumes need `shared_preload_libraries` and manual 005 and 006) | Documented runbook; migration test on a copy of production | partial |
| F4 | Incident process for the agent itself (it can be wrong; who is paged) | On-call rota and rollback plan | not started |
| F5 | Housekeeping: stop dev GPU spend (`make modal-stop`), retire or label the stale `claude_walkthrough.md`, push the local commits, add `langchain-typesafe` and the classifier download step to `pyproject.toml` if the guards stay | Clean tree, documented setup | open |

### Suggested sequence

1. B1 in shadow mode on one team, with A1 to A5 done first so real data is safe to touch. This produces the labels B2 to B5 need.
2. D3 to D6 before the KB accepts content from anyone but you.
3. C1 to C5 only after shadow results are acceptable, starting with a single Tier 1 action on one service.
4. E and F as the measured target load requires.

## 8. Running and testing

All commands are Makefile targets (`make help`):

- `make install`, `make dev-setup`: dependencies and environment.
- `make infra-up` / `infra-down`: Postgres+pgvector, Prometheus, Langfuse.
- `make api`: FastAPI on port 8010. `make demo` / `make demo-warning`: send a sample critical or non-critical alert.
- `make ingest-runbooks`: load sample runbooks.
- `make modal-deploy` / `modal-stop`: dev vLLM endpoints (stops GPU spend).
- `make test`, `make test-unit`, `make test-cov`, `make lint`, `make format`, `make typecheck`.
- `make judge-sanity`, `make judge-gate`, `make judge-gate-simulated`: LLM judge checks.
- `python -m evals.retrieval_ab`: retrieval A/B (RRF only vs three-way fusion vs reranker only).

Configuration is read from `.env` (see `.env.example`): `LLM_BASE_URL`, `EMBEDDING_BASE_URL`, `EGRESS_ALLOWED_HOSTS`, `AUTO_REMEDIATION_ENABLED`, `AUTO_ACTION_ALLOWLIST`, `AUTO_ACTION_TARGET_SERVICES`, `AUTO_ACTION_MAX_PER_HOUR`, the `POSTGRES_*` settings and the `RAG_*` settings (`AgentConfig` in `shared/config.py`).
