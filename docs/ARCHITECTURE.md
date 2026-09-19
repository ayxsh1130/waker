# Architecture and design

## 1. Runtime components

```mermaid
flowchart TD
  UI["React dashboard"] --> API["FastAPI"]
  API --> DB["PostgreSQL"]
  API --> Stream["Durable event stream"]
  Stream --> UI
  Controller["Independent controller"] --> DB
  Controller --> Redis["Redis broker"]
  Redis --> Default["Default Celery worker"]
  Redis --> Proxy["Experimental TCP proxy"]
  Proxy --> Experiment["Experimental Celery worker"]
  Default --> DB
  Experiment --> DB
```

The controller has independent loops for dispatch, fault control, monitoring, investigation, remediation, indexing, and experiments. A session-level PostgreSQL advisory lock prevents multiple active controller processes. The API and workers do not require the controller's Python thread to accept a new request; accepted tasks remain durable until dispatch resumes.

## 2. Incident lifecycle

```mermaid
stateDiagram-v2
  [*] --> DETECTED
  DETECTED --> INVESTIGATING
  INVESTIGATING --> BLOCKED: Provider unavailable
  INVESTIGATING --> FAILED: Investigation error
  INVESTIGATING --> DIAGNOSED
  DIAGNOSED --> VERIFIED: Evidence checks pass
  DIAGNOSED --> UNVERIFIED: Missing or conflicting evidence
  UNVERIFIED --> INVESTIGATING: Remaining budget
  VERIFIED --> APPROVAL_REQUIRED: Medium risk
  VERIFIED --> REMEDIATING: Eligible execute action
  APPROVAL_REQUIRED --> REMEDIATING: Valid approval
  REMEDIATING --> RECOVERED: Successful post-checks
  REMEDIATING --> PARTIALLY_RECOVERED: Containment or incomplete task
  REMEDIATING --> FAILED: Post-check failure
  REMEDIATING --> HUMAN_REVIEW: Uncertain action outcome
```

Baseline configurations may stop at DIAGNOSED. Dry-run policy records do not create an execution or mark the incident recovered. Restarted investigations become INTERRUPTED and are not resumed from invented model state.

## 3. Actual LangGraph topology

```mermaid
flowchart TD
  Start["Start"] --> Investigate["Choose tool or diagnosis"]
  Investigate -->|Tool decision| Collect["Collect bounded evidence"]
  Collect --> Investigate
  Investigate -->|Diagnosis decision| Diagnose["Persist diagnosis and evidence links"]
  Diagnose -->|Comparison baseline| End["Finish"]
  Diagnose -->|Full system| Verify["Deterministic verification"]
  Verify -->|Pass or budget exhausted| End
  Verify -->|Missing evidence and budget available| Investigate
```

`InvestigationRunner.graph()` compiles this graph. Each model decision is validated with `AgentDecision`. Tools are removed from the allowed set when the call/step budget is reached. Verification feedback is public, structured evidence feedback, not hidden reasoning. RULE_BASED bypasses the model graph and executes a fixed evidence pass.

## 4. Policy and execution gate

```mermaid
flowchart TD
  Proposal["Recommended action"] --> Mode{"Dry run?"}
  Mode -->|Yes| Record["Record policy result"]
  Mode -->|No| Policy{"Verification, confidence, state, cooldown"}
  Policy -->|Rejected| Human["Deny or request human"]
  Policy -->|Medium risk| Approval["Expiring one-use approval"]
  Approval --> Check["Recheck latest policy"]
  Policy -->|Low risk allowed| Check
  Check --> Claim["Claim one execution record"]
  Claim --> Dispatch["Fixed action dispatcher"]
  Dispatch --> Observe["Post-action observation"]
```

The model cannot supply arbitrary execution parameters. The server derives task, queue, and worker targets from the incident. A unique execution constraint and a committed EXECUTING claim prevent the controller from replaying an uncertain operation after a crash. This deliberately prefers human review over assuming an external action did not happen.

## 5. Context data-flow diagram

```mermaid
flowchart TD
  Operator["Local operator"] -->|Workload and controls| System["AutoPilot system"]
  System -->|Evidence and outcomes| Operator
  Provider["Optional model provider"] -->|Structured decisions| System
  System -->|Sanitized operational context| Provider
  Workload["Controlled workload environment"] -->|Task events and measurements| System
  System -->|Allowlisted actions| Workload
```

Model-provider requests can send sanitized operational logs/code excerpts outside the machine when a hosted provider is selected. Redaction limits common credential fields and patterns but is not a general-purpose DLP guarantee. Use local Ollama or carefully scoped test inputs when that matters. No secrets are put in frontend environment variables.

## 6. Operational evidence data flow

```mermaid
flowchart TD
  Task["Task operation"] --> Logs["Task logs and trace IDs"]
  Heartbeat["Worker heartbeat"] --> Monitor["Detector and monitor"]
  Queue["Redis measurements"] --> Monitor
  Logs --> Monitor
  Monitor --> Incident["Correlated incident"]
  Incident --> Tools["Restricted diagnostic reads"]
  Tools --> Evidence["Persisted evidence records"]
  Evidence --> Diagnosis["Diagnosis with evidence IDs"]
```

Incident fingerprints include scope, component, signal kind, queue, and worker context where available. Signals within the configured correlation window update the existing incident's occurrence count and append events. A detection receipt prevents the same error log being consumed repeatedly. Different signal kinds may still generate related separate incidents; this is not an unlimited semantic event-correlation engine.

## 7. End-to-end sequence

```mermaid
sequenceDiagram
  participant API
  participant Database
  participant Controller
  participant Worker
  participant Model
  API->>Database: Persist accepted task
  Controller->>Worker: Publish task through Redis
  Worker->>Worker: Run actual operation
  Worker->>Database: Persist task log and outcome
  Controller->>Database: Detect signal and record incident
  Controller->>Model: Incident plus allowed tools
  loop Bounded evidence gathering
    Model-->>Controller: Schema-valid tool choice
    Controller->>Database: Persist collected evidence
    Controller->>Model: Updated observations
  end
  Model-->>Controller: Diagnosis and evidence IDs
  Controller->>Controller: Verify and evaluate policy
  Controller->>Database: Persist outcome and notification
  API->>Database: Read updated records for dashboard
```

Workers persist directly to PostgreSQL. The HTTP model calls are optional and never used by the rule baseline. Redis is omitted as a participant to keep this diagram focused on evidence order.

## 8. Core evidence relationships

```mermaid
erDiagram
  TASK ||--o{ INCIDENT : produces
  INCIDENT ||--o{ INVESTIGATION : has
  INCIDENT ||--o{ EVIDENCE : records
  INVESTIGATION ||--o{ EVIDENCE : collects
  INVESTIGATION ||--o{ DIAGNOSIS : proposes
```

Additional normalized relationships are explicit in SQLAlchemy/Alembic:

| Record | Relationship |
|---|---|
| DiagnosisEvidence | Diagnosis, evidence, and supporting/contradicting role |
| Verification | Diagnosis plus checks, contradictions, missing evidence, score |
| Remediation | Incident and unique diagnosis; action, bound targets, policy, mode |
| ApprovalRequest | Unique remediation; decision, actor, expiry |
| RemediationExecution | Unique remediation; action result, observation time, post-result |
| HistoricalIncident | Unique incident and verified diagnosis; indexed document |
| ExperimentRun | Experiment, configuration, scenario, trial; immutable setup snapshot |
| ExperimentMetric | Unique run and measured evaluator values |
| FaultInjection | Private evaluation scope, actual activation/reset time, optional run |
| LLMUsage | Per actual provider request, including invalid outputs and failures |
| StreamEvent | Monotonic durable notification cursor for WebSockets |

## 9. Retrieval and evaluation boundary

```mermaid
flowchart TD
  Recovery["Verified confirmed recovery"] --> History["Historical document"]
  History --> Embed["Local embedding and Chroma index"]
  Snapshot["Frozen IDs and time cutoff"] --> Search["Filtered semantic search"]
  Embed --> Search
  Search --> Agent["Investigation evidence"]
  Agent --> Diagnosis["Recorded diagnosis"]
  Labels["Injection labels"] --> Evaluator["Post-diagnosis evaluation"]
  Diagnosis --> Evaluator
```

There is no label-to-agent path. The operator UI can show evaluation labels because the operator administers the local experiment. They are excluded from the diagnostic capability set and source root.

## Failure and storage semantics

Celery/Redis delivery is at least once. A durable dispatch row, idempotency key, bounded retries, delivery cap, and per-task operation receipts reduce duplicate side effects; they do not create a universal exactly-once guarantee. See the [Celery task guide](https://docs.celeryq.dev/en/stable/userguide/tasks.html) for late-acknowledgement and worker-loss semantics.

The default worker uses direct Redis connectivity; the experiment worker uses a fixed TCP proxy. The supervisor accepts only crash/restart of its own fixed child command. It has no Docker socket, arbitrary command endpoint, or selectable process ID.

Task artifacts, PostgreSQL, Redis AOF, Chroma, embedding cache, Grafana, and Prometheus use named volumes. Jaeger's default trace storage and the dependency's observation ring are ephemeral. PostgreSQL task/incident/evidence records remain the durable source of truth. No retention purge is silently applied to research records; plan retention before prolonged runs.

The graph uses the documented [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api). Local embeddings follow the [Chroma Sentence Transformers integration](https://docs.trychroma.com/integrations/embedding-models/sentence-transformer) concept, with an explicit service boundary and server-side history filters.
