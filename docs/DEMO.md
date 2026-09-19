# Demonstration guide

## No-key walkthrough

1. Copy `.env.example` to `.env`, start the stack, and wait for both workers to be ONLINE.
2. Keep `LLM_PROVIDER=none` and `REMEDIATION_MODE=dry_run`.
3. In Overview, run a mixed workload. Inspect each task's actual result and log. Open Mailpit to see the captured email. CSV and resized PNG outputs live in the `task-artifacts` volume.
4. In Fault laboratory, select API_TIMEOUT, 40 seconds, 3 tasks. The dependency really delays the HTTP response beyond the worker client's two-second timeout.
5. Open the new incident. Automatic FULL_SYSTEM investigation is visibly BLOCKED if no provider is configured. This is expected. Select RULE_BASED and click Investigate.
6. Review the timeline's fixed diagnostic checks, actual exception, retry history, dependency probes, and linked evidence IDs.
7. Click Verify diagnosis. Inspect missing evidence or contradictions if it does not pass. Click Evaluate remediation and inspect its policy reason. DRY_RUN is a recorded proposal, not an execution.
8. Request fault cleanup or let the deadline expire. A successful later workload is visible in Tasks; do not equate fault expiry with autonomous recovery.
9. Run a small experiment: RULE_BASED and FULL_SYSTEM, API_TIMEOUT and HEALTHY, one trial. With no LLM configured the failing FULL_SYSTEM trial is SKIPPED; healthy controls need no diagnosis. Export CSV and JSON.

The supplied `run-demo.ps1` automates steps 3–8 with the same API. Its no-key rule investigation is predictable; an LLM investigation may take longer than the script's two-minute detection/diagnosis timeout. Use the UI when demonstrating a configured LLM.

## LLM loop demonstration

Configure a supported provider and model, recreate backend/control, and inject another timeout. In the new incident select FULL_SYSTEM. The provider chooses diagnostic tools repeatedly; tool outputs become evidence records and are fed back to the model. The system validates the diagnosis and, when necessary, returns verification feedback for bounded reinvestigation.

Show the differences directly:

- LLM_ONLY receives the observed incident and initial signal, with no diagnostic tools.
- LLM_RAG receives one fixed historical retrieval, with no dynamic tools.
- AGENT_TOOLS can collect operational evidence but cannot search recovered history.
- AGENT_RAG adds historical search but ends without automatic verification/policy.
- FULL_SYSTEM adds deterministic verification, reinvestigation, and policy evaluation.

The tool timeline contains short public hypotheses and reasons, not private chain-of-thought. Structured-output or provider errors remain visible.

## Controlled execution

Changing the mode explicitly enables real local actions. Use a new manual test incident, with no experiment running.

1. Set `REMEDIATION_MODE=execute`, leave production disabled, and recreate backend/control.
2. Inject an API timeout long enough to leave a terminal, idempotent, non-quarantined failed task. Let the fault reset before proposing a retry if you want to demonstrate a repair after the dependency recovers.
3. Investigate and verify. If an earlier diagnosis already has a dry-run proposal, run a new investigation; an existing proposal retains its original mode for auditability.
4. Evaluate remediation. A valid retry is scheduled once; counters and historical failures remain visible. If the task is still running, already succeeded, quarantined, unverified, or below threshold, retry is denied.
5. Watch the execution progress to OBSERVING. After the observation interval, inspect every post-check. RECOVERED requires a successful task and healthy operational checks without new failures. Otherwise the result is PARTIALLY_RECOVERED or FAILED.
6. To inspect medium-risk handling, use an appropriate verified worker/queue diagnosis. Review the exact parameters and reason in Approvals; approve or reject once. An expired approval cannot execute.
7. Return `.env` to `REMEDIATION_MODE=dry_run` and recreate backend/control.

An injected worker crash terminates only the fixed experiment worker process. Its supervisor remains running and supports a controlled restart. Fault cleanup also restarts it after the injection duration; account for that automatic cleanup when interpreting timing.

## Useful inspection commands

```powershell
docker compose ps
docker compose logs --tail=100 control
docker compose logs --tail=100 worker worker-control
docker compose exec postgres psql -U autopilot -d autopilot -c "SELECT status, count(*) FROM task_executions GROUP BY status;"
docker compose exec backend ls -l /data/artifacts
```

Do not paste `.env` or provider keys into logs, screenshots, paper appendices, or the dashboard. The application login token, if enabled, is separate from model-provider credentials.

## Presentation claims you can defend

Say: “We implemented a controlled research pipeline and can show its evidence and outcomes for these recorded runs.” Report the exact configurations, sample counts, failures, skips, and unmeasured values.

Do not say “production-ready,” “exactly-once execution,” “100% accurate,” or “improves MTTR” based only on passing code tests. Unit tests, one successful demo, and comparative experimental evidence answer different questions.
