# Requirements and paper-to-code mapping

Inputs: the supplied `AutoPilot_PRD(4).pdf`, `latest_research_paper.pdf`, and detailed `Pasted text(3).txt` instructions. This map identifies implementation support and known deviations. It does not claim that the supplied paper's proposed results have been reproduced.

| Requirement or research concept | Implementation | What can be demonstrated |
|---|---|---|
| Distributed task execution | `app/workers/celery_app.py`, `tasks.py`, `publisher.py` | Real broker dispatch, worker execution, durable task outcomes |
| Six workload categories | `app/workers/operations.py` | Captured SMTP message, CSV, PNG, HTTP response, DB receipt, calculation |
| Retry, idempotency, poison containment | `tasks.py`, `publisher.py`, `ProcessedRecord` | Bounded counters, duplicate rejection, uncertain email redelivery, quarantine |
| Correlated incident detection | `app/incidents/detector.py` | Fingerprint/window grouping, occurrence counts, initial evidence |
| Task failures, missing workers, backlog | `app/observability/monitor.py` | Actual error logs, heartbeat ages, measured queue depths |
| High latency and failure rate | `monitor.py`, `core/config.py` | Threshold checks after minimum sample count; actual measured windows |
| Independent diagnosis during failure | `app/control.py`, proxy and supervisor services | Controller stays separate from the experimental worker/broker path |
| Iterative autonomous investigation | `app/agents/workflow.py` | Real LangGraph tool loop, structured choices, bounds, persisted timeline |
| Optional LLM providers | `app/llm/provider.py` | Groq, Ollama, compatible endpoint contract; explicit errors and usage |
| Restricted tool system | `app/tools/registry.py` | 20 allowlisted diagnostic capabilities; no execution tools in model context |
| Logs, traces, metrics, task and worker evidence | Registry, `tools/probes.py`, `TaskLog`, `MetricSample` | Persisted observed records and real probes with source/time metadata |
| Source and deployment context | `app/tools/source.py`, `DeploymentRecord` | Bounded code reads, literal search, optional Git/GitHub history, controlled v2 metadata |
| Evidence-grounded diagnosis | `Diagnosis`, `DiagnosisEvidence`, `workflow.py` | Valid collected evidence references and public alternatives/contradictions |
| Deterministic verification | `app/investigation/verification.py` | Cause/component consistency, current evidence diversity, signature checks |
| Reinvestigation | LangGraph verification edge | Failed verification feeds missing evidence back within remaining budget |
| Distinct confidence and verification | `Diagnosis.confidence`, `Verification.verification_score` | Separate scores in API, UI, and metrics; neither is treated as calibrated probability |
| Policy-controlled remediation | `app/remediation/policy.py`, `service.py` | Safe target binding, allowlist, mode, confidence, idempotency, cooldown checks |
| Human approval | `ApprovalRequest`, `decide_approval()` | One-use decisions, expiry, current-policy recheck, visible action/target/reason |
| Post-remediation observation | `app/remediation/recovery.py` | Task/worker/broker/dependency/queue/failure checks before RECOVERED |
| Learning from recovered incidents | `app/retrieval/client.py`, `services/retrieval/main.py` | Only verified confirmed recoveries are indexed; no fabricated seeds |
| Local semantic embeddings | SentenceTransformers CPU and Chroma | Persistent local index; model download and execution require local integration validation |
| Controlled failures | `app/faults/service.py`, dependency/proxy/supervisor services | Eleven executable fault mechanisms and one healthy control |
| Isolation from evaluation labels | Safe projections, tool imports, source restriction, frozen history | Tests enforce absence of evaluator models in agent modules and scope restrictions |
| Six comparison configurations | `Configuration`, registry gating, workflow branches | Capabilities genuinely disabled instead of prompt-only instructions |
| Reproducible experiment metadata | `app/experiments/service.py` | Frozen corpus IDs and configuration snapshot per run |
| Sequential evaluation | `app/experiments/runner.py` | Prerequisite checks, scope isolation, queue drain, cleanup, blocked/skipped states |
| Accuracy and detection metrics | `app/experiments/metrics.py` | Ground-truth comparison only after diagnosis; TP/FP/FN/TN and explicit denominators |
| MTTD, MTTR, investigation time | Evaluator timestamp definitions | Null MTTR without confirmed execution recovery; no invented benchmark numbers |
| Mean, median, standard deviation | `aggregate()` | Per-metric sample count and sample SD, null for insufficient n |
| Live operational dashboard | `frontend/src/pages/`, `useApi.ts` | Overview, incidents, timeline, history, tasks, workers, queues, metrics, faults, approvals, health, research, settings |
| WebSocket updates | `app/main.py`, `StreamEvent`, `useLive()` | Durable cursor notifications and REST refresh; 30-second fallback |
| Research graphs and exports | `Research.tsx`, experiment API | Real per-configuration comparisons, filters, raw run view, CSV/JSON |
| Optional local application login | `app/core/auth.py`, frontend native form | HttpOnly signed session, origin checks, bounded login attempts; default disabled |
| Operational telemetry | `observability/`, `monitoring/` | Structured logs, DB-backed Prometheus metrics, provisioned Grafana, traces to Jaeger |
| Windows reproducibility | Compose, Dockerfiles, `scripts/*.ps1` | Source package, environment template, startup/demo/check/reset commands |
| Schema and checks | Explicit Alembic migration, `backend/tests/`, frontend tooling | Executed checks are recorded separately in `VALIDATION.md` |

## Resolved conflicts and boundaries

| Source tension | Implemented decision | Reason / consequence |
|---|---|---|
| PRD mentions Next.js; detailed build instructions specify React/Vite | React + Vite + TypeScript | Follows the concrete local dashboard instruction. No Next.js server or Vercel deployment is claimed. |
| Paper groups worker/queue changes as low risk | Fixed worker restart and queue changes require approval | These operations affect other tasks; the implementation uses a stricter medium-risk policy. |
| Paper comparison list versus expanded requested experiment list | Six configurations, including AGENT_RAG | Separates retrieval effects from verification/policy effects. |
| Proposed paper results versus executable evidence | No seeded metrics, claims, or fabricated historical incidents | The user must run the experiment protocol to obtain reportable results. |
| PRD logging-stack references | Structured durable DB logs plus Prometheus/Grafana/OTel/Jaeger | Loki ingestion is not included; the UI reads persisted operational logs. This is a disclosed observability-stack deviation. |
| “Worker restart” could imply Docker management | A private fixed-process supervisor | No Docker socket or host-level arbitrary control. |
| “Database outage” could destroy the audit path | A workload database connection fails against a controlled unavailable endpoint | Genuine connection exception while the audit database remains available. It is not a full persistence outage test. |
| “Deployment regression” could imply automatic Git rollback | Controlled real v2 workload implementation and metadata | Source evidence exists; arbitrary deployment or rollback is not implemented. Escalation is an allowed outcome. |
| Automatic remediation and approval experiment timing | Only FULL_SYSTEM evaluates automatic policy; approval may remain pending | Baseline MTTR and unexecuted/late-approved recovery remain unmeasured. |
| “Production oriented” versus local college prototype | Explicit safety controls without a production-readiness guarantee | No multi-tenancy, HA control failover, immutable audit service, SSO/RBAC, secret manager, or production capacity testing is claimed. |

## What still requires real experimental evidence

1. Real provider behavior for the selected model, including tool-choice quality, quota failures, and inference latency.
2. Local Chroma model download/index/search and recovered-history quality.
3. Distributed container behavior under worker kill, Redis proxy disruption, database reconnect, and controller restart.
4. Repeated measured comparisons with confidence intervals or other justified statistical analysis before claiming improvement.
5. Full browser interaction and Windows Docker validation on the target laptop.

Read `VALIDATION.md` before describing anything as tested. A tested evaluator formula is not the same as an observed experimental result.
