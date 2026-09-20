from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyContext:
    verified: bool
    confidence: float
    min_confidence: float
    idempotent: bool
    task_status: str
    quarantined: bool
    worker: str | None
    queue: str | None
    previous_failure: bool = False
    repeated: bool = False
    environment: str = "development"
    cause: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    risk: str
    reason: str


def evaluate(action, context):
    def decision(value, reason, risk="LOW"):
        return PolicyDecision(value, risk, reason)

    if action == "NO_ACTION":
        return decision("NO_ACTION", "No change recommended")
    if action == "REQUEST_HUMAN":
        return decision("HUMAN_REVIEW", "Human diagnosis or repair required")
    if not context.verified:
        return decision("DENIED", "Diagnosis has not passed deterministic verification")
    if context.confidence < context.min_confidence:
        return decision("DENIED", "Confidence below configured threshold")
    if context.cause is not None:
        causes = {
            "RESTART_WORKER": {"WORKER_FAILURE"},
            "RETRY_TASK": {"API_TIMEOUT", "DEPENDENCY_ERROR", "INTERMITTENT_FAILURE", "TASK_EXCEPTION"},
            "QUARANTINE_TASK": {"POISON_TASK", "TASK_EXCEPTION"},
            "PAUSE_QUEUE": {"OVERLOAD", "POISON_TASK"},
        }
        if action in causes and context.cause not in causes[action]:
            return decision("DENIED", "Action does not match the verified root cause")
    if context.previous_failure or context.repeated:
        return decision("DENIED", "Prior failed action or active cooldown requires human review")
    if action == "RETRY_TASK":
        if context.idempotent and context.task_status == "FAILED" and not context.quarantined:
            return decision("ALLOW", "One retry of a failed idempotent task")
        return decision("DENIED", "Retry requires a terminal, non-quarantined, idempotent task")
    if action == "QUARANTINE_TASK":
        if context.task_status in {"FAILED", "RETRYING", "QUARANTINED"}:
            return decision("ALLOW", "Contain a repeatedly failing task; containment is not recovery")
        return decision("DENIED", "Only failing tasks can be quarantined")
    if action == "RESTART_WORKER":
        if context.worker == "experiment@autopilot" and context.environment != "production":
            return decision("APPROVAL_REQUIRED", "Restart the fixed experimental worker process", "MEDIUM")
        return decision("DENIED", "Worker is outside the permitted experimental target", "MEDIUM")
    if action in {"PAUSE_QUEUE", "RESUME_QUEUE"}:
        if context.queue in {"default", "experiment"}:
            return decision("APPROVAL_REQUIRED", "Queue consumer change requires approval", "MEDIUM")
        return decision("DENIED", "Unknown queue", "MEDIUM")
    if action == "CLEAR_RETRY_STATE":
        if context.idempotent and context.task_status in {"FAILED", "QUARANTINED"}:
            return decision(
                "APPROVAL_REQUIRED", "Archive retry counters and clear quarantine without enqueuing", "MEDIUM"
            )
        return decision("DENIED", "Only a terminal idempotent task can be reset", "MEDIUM")
    return decision("DENIED", "Action is not on the execution allowlist", "HIGH")
