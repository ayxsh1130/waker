"""Evaluation rubric is intentionally separate from investigation capabilities."""

from statistics import mean, median, stdev

from app.db.base import seconds

ACTION_RUBRIC = {
    "API_TIMEOUT": {"RETRY_TASK", "REQUEST_HUMAN"},
    "DEPENDENCY_ERROR": {"RETRY_TASK", "REQUEST_HUMAN"},
    "INTERMITTENT_FAILURE": {"RETRY_TASK", "REQUEST_HUMAN"},
    "WORKER_FAILURE": {"RESTART_WORKER", "REQUEST_HUMAN"},
    "TASK_EXCEPTION": {"REQUEST_HUMAN", "QUARANTINE_TASK"},
    "POISON_TASK": {"QUARANTINE_TASK", "REQUEST_HUMAN"},
    "BROKER_DISRUPTION": {"REQUEST_HUMAN"},
    "DATABASE_FAILURE": {"REQUEST_HUMAN"},
    "OVERLOAD": {"PAUSE_QUEUE", "REQUEST_HUMAN"},
    "BAD_CONFIGURATION": {"REQUEST_HUMAN"},
    "DEPLOYMENT_REGRESSION": {"REQUEST_HUMAN"},
    "HEALTHY": {"NO_ACTION"},
}


def evaluate_run(
    truth,
    fault_at,
    incident=None,
    investigation=None,
    diagnosis=None,
    verification=None,
    execution=None,
    task=None,
    usage=None,
):
    detected = incident is not None
    healthy = truth == "HEALTHY"
    diagnosed = diagnosis is not None
    action = diagnosis.recommended_action if diagnosed else None
    valid = (action in ACTION_RUBRIC[truth]) if diagnosed else None
    if action == "RETRY_TASK" and task and not task.idempotent:
        valid = False
    confirmed = bool(execution and execution.status == "RECOVERED" and incident and incident.recovered_at)
    values = {
        "ground_truth": truth,
        "predicted_cause": diagnosis.root_cause if diagnosed else None,
        "detected": detected,
        "true_positive": int(detected and not healthy),
        "false_positive": int(detected and healthy),
        "false_negative": int(not detected and not healthy),
        "true_negative": int(not detected and healthy),
        "diagnosis_accuracy": (diagnosis.root_cause == truth) if diagnosed and not healthy else None,
        "mttd_seconds": seconds(incident.created_at, fault_at) if detected and fault_at else None,
        "mttr_seconds": seconds(incident.recovered_at, fault_at) if confirmed and fault_at else None,
        "investigation_seconds": seconds(investigation.completed_at, investigation.created_at)
        if investigation and investigation.completed_at
        else None,
        "confidence": diagnosis.confidence if diagnosed else None,
        "verification_score": verification.verification_score if verification else None,
        "verified": verification.verified if verification else None,
        "action_validity": valid,
        "recommended_action": action,
        "automatic_recovery": confirmed if execution and execution.automatic else None,
        "recovery_confirmed": confirmed if execution else None,
        "execution_outcome": execution.status if execution else None,
    }
    usage = usage or []
    values.update(
        llm_calls=len(usage),
        input_tokens=sum(u.input_tokens for u in usage)
        if all(u.input_tokens is not None for u in usage)
        else None,
        output_tokens=sum(u.output_tokens for u in usage)
        if all(u.output_tokens is not None for u in usage)
        else None,
        estimated_cost=sum(u.estimated_cost for u in usage)
        if usage and all(u.estimated_cost is not None for u in usage)
        else None,
    )
    return values


def aggregate(rows):
    keys = [
        "diagnosis_accuracy",
        "mttd_seconds",
        "mttr_seconds",
        "investigation_seconds",
        "confidence",
        "verification_score",
        "action_validity",
        "automatic_recovery",
        "recovery_confirmed",
        "llm_calls",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
    ]
    result = {"runs_with_metrics": len(rows), "metrics": {}}
    for key in keys:
        values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
        result["metrics"][key] = {
            "n": len(values),
            "mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "sample_sd": stdev(values) if len(values) > 1 else None,
        }
    tp, fp, fn, tn = (
        sum(r.get(k, 0) for r in rows)
        for k in ["true_positive", "false_positive", "false_negative", "true_negative"]
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    result["detection"] = {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
    }
    return result
