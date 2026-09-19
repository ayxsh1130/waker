# Reproducible evaluation

## Experimental design

Create trials from Research. Each trial uses a fresh scope, a fixed scenario, one configuration, and the specified trial number. At most 120 runs are accepted per experiment. Only one experiment may be queued/running; manual fault scheduling is excluded while it owns the resources. PostgreSQL advisory transaction locking serializes scheduling across processes.

The runner waits for a healthy experimental worker/broker and a drained experimental task set before injection. An outstanding task or unhealthy prerequisite can block subsequent runs instead of silently mixing trials. It waits up to 100 seconds for observed signals/completions after scheduling. Failed activation, an unobserved worker crash, or an incomplete healthy control is excluded from metrics with a visible error. The test dependency expires profiles; the fault loop also resets proxy/worker state and retries failed cleanup.

Configurations rotate their starting order across repetitions. This reduces fixed-order effects; it is not full randomization. A fresh experiment scope prevents task-label cross-contamination. The same persistent machine and worker pool are reused, so thermal effects, provider rate limits, queue redelivery, and previous process state remain possible confounders.

Every run records provider/model, temperature, workflow/prompt versions, software revision, embedding configuration, history IDs, confidence threshold, remediation mode, budgets, task parameters, timestamps, and final outcomes. Set `SOFTWARE_REVISION` to your real Git commit before collecting paper results. The ZIP default `unversioned` is an honest placeholder, not a fabricated commit.

Do not modify `.env`, the source, model, corpus, or rate settings during an experiment. Configuration is read when controller processes start; recorded snapshots describe the run setup. If a model/provider changes remotely behind a model alias, the project cannot prevent that.

## Ground-truth isolation

`FaultInjection` and `ExperimentRun` contain evaluator labels. The API's Fault laboratory exposes them to the human operator. Agent-facing projections omit scope labels, injection parameters, task payloads, and evaluation records. Tool code does not import fault/experiment models; source access excludes evaluation/control directories.

The investigation sees symptoms and genuine operational evidence such as HTTP errors, worker state, code regions, and local release metadata. The final evaluator compares its result with the stored injection label after diagnosis. Historical documents describe verified diagnoses and observed recovery, not injection labels.

History is frozen to the IDs indexed at experiment creation, with an additional creation-time cutoff. Newly recovered incidents from earlier trials in the same experiment cannot leak into later trials. To evaluate learned history, complete an earlier training/demo phase, confirm indexing, then create a new experiment and report the corpus size.

## Metric definitions

| Metric | Definition and denominator |
|---|---|
| Diagnosis accuracy | Predicted label equals injected label, among non-healthy runs that actually produced a diagnosis. Report n and missed/failed diagnosis counts separately. |
| Detection precision | TP / (TP + FP), where a fault trial with an incident is TP and a healthy trial with an incident is FP. |
| Detection recall | TP / (TP + FN), where an injected fault without a detected incident is FN. |
| Detection F1 | 2 TP / (2 TP + FP + FN). Null when undefined. |
| False positive rate | FP / (FP + TN), across completed healthy controls. |
| MTTD | Incident creation timestamp minus actual recorded injection time. Individual runs store detection time; aggregated mean is MTTD. |
| MTTR | Confirmed post-execution recovery timestamp minus injection time. Absent for dry runs, containment, failed/uncertain outcomes, or missing execution. |
| Investigation duration | Investigation completion minus investigation start, including tools, model calls, and verification loops. |
| Confidence | The model/rule's reported score. It is not a calibrated probability. |
| Verification score | Fraction of satisfied deterministic checks among checks/missing evidence/contradictions; not model confidence. |
| Action validity | Proposed action falls in a prespecified cause/action rubric; retrying a non-idempotent task is invalid. Human escalation can be appropriate. This is distinct from successful repair. |
| Automatic recovery | Confirmed RECOVERED among executions marked automatic. Approval-dependent executions are excluded from this automatic-action denominator. |
| Recovery confirmed | Whether an execution reached RECOVERED, among recorded executions. Not a ratio over all scheduled trials. |
| Tokens and cost | Real provider-reported token counts; unknown counts remain null. Estimated cost requires both configured prices. |

Numeric aggregates include n, mean, median, and sample standard deviation. Binary metrics use 0/1 means. Sample SD is null for n < 2. Skipped runs and excluded activations do not contribute zeros. Zero LLM calls for RULE_BASED is real; a missing MTTR is not zero recovery time.

Healthy controls do not invoke a diagnosis unless an operator separately chooses to investigate an ordinary incident. In the automated runner they measure false detections, independent of an LLM's ability to label normality. A run can produce detection metrics while its investigation is FAILED; diagnosis accuracy remains null if no diagnosis was produced.

## Scenario interpretation

| Label | Actual operation | Scope limitation |
|---|---|---|
| API_TIMEOUT | Real delayed HTTP response exceeding client deadline | Controlled dependency, not an internet outage |
| DEPENDENCY_ERROR | Real HTTP 500 | Local test service |
| INTERMITTENT_FAILURE | Alternating HTTP 500/200 | Deterministic alternation, not random loss |
| WORKER_FAILURE | SIGKILL of fixed worker process group after observed task start | Experimental worker only |
| BROKER_DISRUPTION | TCP proxy closes/rejects experimental worker Redis connections | Redis service/control access stays available |
| DATABASE_FAILURE | Workload attempts unavailable PostgreSQL endpoint | Does not take the application's persistence database down |
| TASK_EXCEPTION | Short-lived ValueError inside task execution | TTL 10 seconds; retries can recover |
| POISON_TASK | Persistent validation error across retries | Bounded poison behavior, not infinite retries |
| OVERLOAD | Up to 30 slow tasks create queue backlog | Small local queue saturation, not a capacity benchmark |
| BAD_CONFIGURATION | Invalid numeric setting parsed in real task code | Controlled configuration defect |
| DEPLOYMENT_REGRESSION | Real alternate implementation with zero divisor | Local v2 selection, not a Git deploy or arbitrary rollback |
| HEALTHY | Same scoped workload with healthy dependency | Establishes negative control; requires completed workload |

Use the default backlog threshold of 20 for the bounded overload scenario. Raising it above the maximum injected burst can prevent overload detection; report any threshold changes.

## Interpretation limits

The fault deadline may reset a dependency while an investigation is running. Celery's normal retry, a human-approved action, and fault cleanup may also contribute to recovery. Recorded MTTR describes the observed system outcome; it is not proof that the agent alone caused the recovery. The current evaluator does not perform counterfactual causal attribution.

Automatic policy is intentionally absent from the five comparison baselines. Their MTTR is consequently unmeasured in this harness. Compare diagnosis/detection/tool/time metrics across all configurations; assess full-system execution separately. To make a fair remediation-efficiency comparison, define and implement an additional identical execution policy for all relevant baselines as a separate experimental protocol, rather than silently filling their MTTR with fault-expiry time.

The verifier implements bounded operational signatures, not formal proof of root cause. A passing verification checks the cited observations and consistency rules; it cannot establish uniqueness among every possible cause. Report false diagnoses, missing evidence, and verification failures, not just successful examples.

Human approvals can remain pending when an experiment finishes collecting its results. Later approval/execution does not retroactively rewrite the stored trial metrics. Export raw JSON alongside CSV and preserve the configuration snapshot.

No statistical-significance test, confidence-interval claim, production generalization, or paper-ready numerical result is supplied without collected data. Run repeated trials, retain skipped/failed runs, include healthy controls, and report per-metric denominators before drawing conclusions.
