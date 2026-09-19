# Delivery validation

Validation date: 9 September 2026. These results refer to the restored source included in this ZIP. Earlier results from a previous temporary copy are not used as evidence for this delivery.

## Executed checks

| Check | Actual result |
|---|---|
| Backend pytest suite | **102 passed**, 0 failures, 0 skips; final run 5.58 seconds |
| Python Ruff | Passed for backend application, tests, migrations, all service code, and smoke script |
| Frontend ESLint | Passed with no errors or warnings |
| TypeScript and Vite production build | Passed; Node 24.19.0, Vite 8.2.2; routes load in separate chunks |
| SQLite migration upgrade | Passed on a fresh temporary database |
| Alembic model/schema comparison | No new upgrade operations detected |
| PostgreSQL migration generation | Offline SQL generation passed; generated SQL included for inspection |
| Installed backend dependency compatibility | `uv pip check`: 93 installed packages checked, all compatible |
| Retrieval version availability | Dry-run resolver found `chromadb==1.5.0` and `sentence-transformers==5.1.0`; no-dependency check only |
| Compose/config static inspection | 17 services; YAML/JSON parsed; dependency graph acyclic; bind paths present; named volumes resolve; all published ports bind to loopback |

The pytest run reports one upstream deprecation warning from Starlette's TestClient use of an AnyIO alias. It does not fail the suite. No warning is hidden in the test configuration.

Machine-readable records are included under `validation/`: `backend-tests.xml`, `compose-static.json`, and `postgres-schema.sql`. The SQL is generated schema output, not evidence of a live PostgreSQL migration.

## What the tests exercise

- API task creation and idempotency, payload conflicts, validation limits, origin checks, missing records, authenticated sessions, native login, secret non-disclosure, and durable WebSocket notifications.
- A transaction failure is returned before a task-accepted response can be sent.
- Real local CSV and image outputs, database operation receipts, and numerical processing, with the controlled profile lookup replaced by a test transport.
- Duplicate execution, uncertain email redelivery, poison retry exhaustion, delivery caps, and dispatch persistence after broker errors.
- Actual LangGraph transitions using a scripted test provider, including failed verification followed by evidence collection and successful reinvestigation.
- Disabled baseline tools, unknown providers, tool budgets, scoped task access, failed evidence sources, bounded source paths, symlink escape rejection, and secret/label redaction.
- Provider HTTP request contracts with HTTPX test transports: forced function decisions, bounded invalid-output retries, token accounting, error redaction, and Ollama schema responses.
- Deterministic evidence checks, action-policy boundaries, dry-run non-execution, one execution claim, current-mode rechecks, expiry/rejection, and uncertain action handling.
- Cause-to-fault plans, actual dependency HTTP 500/200 responses through FastAPI TestClient, cleanup retries, one-active-fault rules, production fault prohibition, and experiment resource readiness.
- Frozen experiment setup, run limits, metric denominators, null MTTR behavior, healthy false positives, and interrupted-controller handling.

Test transports and scripted providers exist only in tests. The running application has no mocked model, seeded history, prefilled incidents, or fabricated experiment charts.

## Not executed here

| Integration | Why it remains unverified | Provided local check |
|---|---|---|
| Docker image builds and container startup | Docker CLI/engine unavailable in this environment | `scripts/start.ps1`, `docker compose ps`, `docker compose logs` |
| Docker Compose engine validation | Static parsing is not the Compose engine | `docker compose config --quiet` |
| Real PostgreSQL transaction/lock concurrency | No usable PostgreSQL service in this environment | Container startup migration; live workload; targeted concurrency testing before production use |
| Celery/Redis distributed retry/redelivery and real worker kill | Requires the live services | Fault laboratory plus `scripts/smoke.py` |
| Prometheus/Grafana/Jaeger delivery | Provisioning inspected; containers not run | Open service UIs after full stack startup |
| Groq or another hosted provider | No authorized provider key supplied | Configure `.env`, run FULL_SYSTEM, inspect actual usage/errors |
| Ollama inference | No running local model server | Configure host endpoint and run an investigation |
| SentenceTransformer model and Chroma indexing/search | Full model environment/download not executed | Wait for retrieval health, create verified recovery, inspect history indexing |
| GitHub repository integration | No repository/token supplied | Configure optional GitHub settings and inspect source/deployment tools |
| PowerShell execution on Windows | This workspace is Linux; PowerShell was not available | Run supplied scripts from Windows VS Code |
| Browser interaction, layout, accessibility, and WebSocket reconnect behavior in a real browser | Frontend compiled and linted; no browser session was run | Open each page, execute forms, resize window, stop/restart backend |
| Research performance improvement | No real comparative experiment results collected | Follow `EXPERIMENTS.md`; retain all raw exports |

## Local acceptance checklist

1. `docker compose config --quiet` exits successfully and `docker compose up -d --build` completes.
2. API liveness responds; both workers are observed ONLINE; a complete mixed workload has real logs and outputs.
3. `py scripts/smoke.py` reports five successful tasks through the live broker/worker path.
4. The no-key dry-run walkthrough records a diagnosis, verification, and policy decision without an execution.
5. A configured FULL_SYSTEM run records actual provider requests and iterative evidence collection; a missing provider remains visibly blocked.
6. At least one manual execute-mode scenario is observed through recovery checks; rejected or partial outcomes are retained honestly.
7. Each selected fault activates and resets, including worker crash and broker disruption; interrupted-controller cleanup works.
8. A small experiment exports configuration metadata, per-run outcomes, and nulls for unmeasured metrics.
9. A verified recovered incident is indexed and can be retrieved in a later experiment without violating its frozen cutoff.

The implementation and supplied checks are ready for this local acceptance pass. This delivery does not assert that unexecuted integrations have passed.
