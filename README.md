# Waker

A local research prototype for investigating failures in a distributed task system and applying controlled, evidence-backed remediation.

Waker runs real Celery tasks, observes their operational failures, collects evidence through restricted diagnostic tools, records a diagnosis, checks that diagnosis deterministically, and evaluates an allowlisted action policy. The dashboard shows actual persisted observations. It starts with an empty incident history and no experiment results.

**Start with `REMEDIATION_MODE=dry_run`.** No LLM key is required for the rule baseline. An LLM provider is required for the five model-based configurations. No ChatGPT account authentication is used.

## Accounts and application registration

The identity milestone adds named accounts, revocable sessions, viewer/operator/admin permissions, application memberships, and scoped connector credentials. Follow [IDENTITY_SETUP.md](docs/IDENTITY_SETUP.md) to upgrade an existing database and enable sign-in. Registered external applications do not yet ingest workloads; existing operational records belong to the local workload.

## Run on Windows

Install Docker Desktop with its Linux container engine and WSL 2 enabled. Start Docker Desktop before running these commands. Extract the ZIP and open the inner `waker` folder in VS Code.

In the VS Code PowerShell terminal:

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Open [Waker](http://localhost:5173). Initial container builds and the first local embedding-model download can take several minutes. Optional retrieval may remain unavailable while its model loads; the API and rule baseline can still run.

Or use the startup script:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

This invocation applies execution policy only to that PowerShell process. It does not change your machine's policy.

For a smaller first run without the model, Grafana, Prometheus, or tracing containers:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 -CoreOnly
```

Optional services will show as unavailable in this mode. To add them later, run `docker compose up -d --build`.

## First demonstration

Wait until **Workers** lists `default@autopilot` and `experiment@autopilot` as online. Then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-demo.ps1
```

The script creates a mixed workload, injects a bounded HTTP timeout, waits for an incident, runs the rule baseline, verifies its evidence, records a dry-run policy decision, and requests cleanup. It prints the incident link and the actual verification result. It does not claim recovery from a dry run.

For a direct infrastructure smoke test after startup, with Python installed:

```powershell
py .\scripts\smoke.py
```

It queues five real operations through the running API and waits for the workers to complete them with durable success logs. See [DEMO.md](docs/DEMO.md) for the manual workflow, LLM demonstration, and controlled execution procedure.

## What is included

- Six real workload types: email to a local Mailpit inbox, CSV generation, image resizing, HTTP requests, database record processing, and numerical data processing.
- Durable task records and dispatch retry, Celery late acknowledgements, bounded retry/backoff, duplicate protection, and quarantine. Email redelivery with an uncertain prior outcome requires human review.
- Worker heartbeat and queue monitoring; task errors, missing workers, broker connectivity, persistent backlog, high failure-rate, and high-latency alerts.
- Correlated incidents, source-linked evidence, chronological public investigation steps, diagnoses, verification records, approvals, action executions, and post-action observations.
- A real LangGraph investigation loop, 20 restricted diagnostic tools, Groq/local Ollama/compatible provider support, and structured output validation.
- Local sentence-transformer embeddings and persistent Chroma retrieval over verified, recovered historical incidents.
- Six genuinely different experiment configurations, eleven injected failure scenarios plus a healthy control, frozen retrieval history, sequential trials, statistics, and CSV/JSON exports.
- React/TypeScript dashboard, WebSocket notifications backed by a durable event table, polling fallback, Prometheus metrics, provisioned Grafana panels, and OpenTelemetry traces exported to Jaeger.
- PostgreSQL schema migration, Python tests, frontend lint/type checking, Windows scripts, and CI configuration.

The prototype is designed for local academic evaluation. It is not a validated production incident-response system, an HA platform, or evidence of improved MTTR until you run and analyze experiments.

## Stack and process boundaries

| Layer | Implementation |
|---|---|
| Dashboard | React, Vite, TypeScript, React Router, Recharts, Lucide |
| API | Python 3.12, FastAPI, Pydantic |
| Persistence | PostgreSQL 16, SQLAlchemy, Alembic |
| Task execution | Celery 5.6, Redis 7.4 |
| Independent control plane | Python process with a PostgreSQL advisory singleton lock |
| Investigation | LangGraph; schema-validated provider responses |
| Historical retrieval | SentenceTransformers on CPU; persistent Chroma |
| Operational visibility | Prometheus, Grafana, OpenTelemetry, Jaeger, structured DB logs |
| Controlled services | HTTP dependency, fixed worker supervisor, fixed-target Redis TCP proxy |
| Local mail | Mailpit; no external email credentials required |

The API, controller, default worker, and experimental worker are separate processes. Killing the experimental worker does not kill the investigator. Broker injection interrupts only the experimental worker's connection through the proxy; PostgreSQL and the control plane retain access to the main Redis service.

Architecture, workflow, DFD, data model, and sequence diagrams are in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Local URLs

All published ports bind to `127.0.0.1`.

| Service | URL | Credentials |
|---|---|---|
| Dashboard | http://localhost:5173 | None by default |
| API documentation | http://localhost:8000/docs | App session required for protected operations if enabled |
| API liveness | http://localhost:8000/health | None; does not imply dependency health |
| Prometheus | http://localhost:9090 | Local only |
| Grafana | http://localhost:3000 | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from `.env` |
| Jaeger | http://localhost:16686 | Local only |
| Mailpit inbox | http://localhost:8025 | Local only; messages are captured, not sent to the internet |

PostgreSQL, Redis, internal controls, and retrieval are not published as host ports. Do not expose the Compose stack publicly without additional network, authentication, and operational controls.

## Configure a model

Edit `.env`, then recreate the API and controller:

```powershell
docker compose up -d --force-recreate backend control
```

For Groq:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=your_provider_key
GROQ_MODEL=llama-3.3-70b-versatile
```

For Ollama running on your Windows host:

```powershell
ollama pull qwen3:8b
```

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:8b
```

The Ollama server must be reachable from Docker. Follow your Ollama installation's network settings; keep access local. Model speed and memory use depend on hardware, quantization, and context size. An 8B model plus the complete monitoring stack may be heavy on a 16 GB laptop; use the smaller core stack or a provider API if needed. A local model has no per-request provider fee, but uses your hardware and downloads model weights. Provider free tiers and quotas are not guaranteed by this project.

For an OpenAI-compatible provider:

```dotenv
LLM_PROVIDER=compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your_provider_key
LLM_MODEL=your_supported_model
```

The compatible provider must support the forced function-call contract used in `backend/app/llm/provider.py`. Arbitrary providers are not assumed compatible. Failed calls and invalid outputs are recorded; no fake diagnosis replaces them. Ollama uses its JSON-schema response format.

Cost estimates are null unless both numeric per-million-token price settings are explicitly configured. Token usage is null when the provider does not report it. An unavailable LLM does not prevent tasks, monitoring, fault injection, or the rule baseline from working.

## Retrieval and source context

Chroma starts empty. Only a verified diagnosis followed by confirmed executed recovery becomes a history document. Dry runs, failed actions, and quarantined tasks do not seed recovered memory. This means the first RAG experiments may have no useful historical matches; that is an experimental condition, not a reason to seed invented results.

Each experiment freezes the IDs of indexed history records at creation. Retrieval also enforces a timestamp cutoff and excludes the current incident. No experiment injection labels enter those documents.

The agent can inspect only bounded code under `/source/workers`, mounted read-only from `backend/app/workers`. Paths outside that root, hidden files, evaluation/control folders, oversized files, and symlink escapes are rejected. Git history is optional: the ZIP is not a Git checkout, so commit inspection may be unavailable until you initialize a repository or configure `GITHUB_REPOSITORY` and `GITHUB_TOKEN`. The controlled v2 release has explicit local deployment metadata; `local-v2` is not presented as a real Git commit.

## Remediation behavior

| Action | Policy |
|---|---|
| Retry task | Verified, above threshold, terminal FAILED, idempotent, non-quarantined; retry history retained |
| Quarantine task | Verified failing task; containment only |
| Restart worker | Approval required; only `experiment@autopilot`, outside production |
| Pause / resume queue | Approval required; only known default/experiment queues |
| Clear retry state | Approval required; terminal idempotent task; old counters archived; does not enqueue |
| No action / request human | Recorded without execution |
| Any other action | Denied |

An execution rechecks the latest diagnosis, mode, verification, confidence, current task state, cooldown, previous failures, and approval expiry. An execution record is claimed before the action. If an external action's result is uncertain, it is not automatically replayed after restart. There is no arbitrary shell tool, `eval`, Docker-socket access, or user-supplied command execution.

Recovery requires a successful business task, a healthy worker/broker/dependency where applicable, acceptable measured queue depth, and no new scope failure logs during observation. Containment is reported as `PARTIALLY_RECOVERED`. A worker-only action without a successful associated task cannot be labeled full recovery.

## Experiments and research claims

| Configuration | LLM | Dynamic tools | History | Automatic verification | Automatic policy |
|---|---|---|---|---|---|
| RULE_BASED | No | Fixed eight-check rule pass | No | No | No |
| LLM_ONLY | Yes | No | No | No | No |
| LLM_RAG | Yes | No | One fixed retrieval | No | No |
| AGENT_TOOLS | Yes | Yes | No | No | No |
| AGENT_RAG | Yes | Yes | Yes | No | No |
| FULL_SYSTEM | Yes | Yes | Yes | Yes, with bounded reinvestigation | Yes, still subject to mode/approval |

Manual verification is available for ordinary incidents. It is not silently enabled for baseline experiment runs. Read [EXPERIMENTS.md](docs/EXPERIMENTS.md) for definitions, denominators, controls, and limitations. Read [PAPER_MAPPING.md](docs/PAPER_MAPPING.md) for the mapping from supplied requirements/paper concepts to code and deviations.

## Tests and development

Container-based checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1
```

Backend checks without Docker:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
ruff check app tests alembic ..\services ..\scripts\smoke.py
pytest -q
cd ..
```

Frontend development with Node 24 (or supported Node >=22.12):

```powershell
cd frontend
npm ci
npm run lint
npm run build
npm run dev
```

The Vite development server proxies `/api` and WebSockets to localhost:8000. Stop the frontend container first if it occupies port 5173: `docker compose stop frontend`. The development backend still needs a reachable database, Redis, and supporting services. Use Docker for the backend on Windows because Celery worker pools and the test supervisor rely on Linux process behavior.

Python direct dependencies and the frontend lockfile are included. Container tags are pinned to stated release series, not immutable image digests. See [VALIDATION.md](docs/VALIDATION.md) for checks actually executed in this delivery and those still requiring your machine.

## Files to understand first

| Path | Purpose |
|---|---|
| `backend/app/main.py` | API assembly, middleware, WebSocket stream |
| `backend/app/control.py` | Independent monitoring/investigation/remediation loops |
| `backend/app/workers/` | Durable dispatch, Celery execution, real operations |
| `backend/app/agents/workflow.py` | Actual LangGraph transitions and reinvestigation |
| `backend/app/tools/registry.py` | Baseline tool access and safe operational projections |
| `backend/app/investigation/verification.py` | Deterministic evidence rules |
| `backend/app/remediation/` | Policy, approvals, action dispatcher, post-checks |
| `backend/app/faults/` | Controlled injection and cleanup; evaluation-side only |
| `backend/app/experiments/` | Trial runner, frozen metadata, evaluator, aggregates |
| `backend/app/db/models.py` | Durable operational/evaluation schema |
| `backend/alembic/versions/0001_initial_schema.py` | Explicit initial migration |
| `services/` | Isolated dependency, worker supervisor, TCP proxy, retrieval |
| `frontend/src/pages/` | Dashboard pages and operator controls |
| `monitoring/` | Prometheus, Grafana, and OpenTelemetry provisioning |
| `scripts/` | Windows startup, demo, checks, reset, and live smoke test |

## Stop, inspect, reset

```powershell
docker compose logs --tail=100 backend control worker worker-control
docker compose logs --tail=100 retrieval
docker compose down
```

Normal `down` retains named volumes. A destructive reset is deliberately explicit:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\reset-dev.ps1 -DeleteData
```

The script asks you to type DELETE. This deletes experiment results and local state as well as model caches. Export results first.

If the dependency or proxy is left in a fault state after interruption, restart `control`; it marks active faults for cleanup. If cleanup remains pending, inspect the internal service logs. Do not manually mark a run recovered to make a chart look successful.
