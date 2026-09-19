from sqlalchemy import select

from app.core.config import settings
from app.db.base import uid
from app.db.models import Experiment, ExperimentMetric, ExperimentRun, HistoricalIncident
from app.db.session import row_dict
from app.experiments.metrics import aggregate


def create_experiment(db, request):
    from app.db.locks import lock_experiment_resources
    from app.db.models import FaultInjection

    lock_experiment_resources(db)
    if db.scalar(
        select(FaultInjection.id).where(FaultInjection.status.in_(["ACTIVE", "SCHEDULED", "RESETTING"]))
    ):
        raise ValueError("A manual fault is active or awaiting cleanup")
    if db.scalar(select(Experiment.id).where(Experiment.status.in_(["QUEUED", "RUNNING"]))):
        raise ValueError("An experiment is already queued or running")
    cfg = settings()
    if cfg.app_environment == "production" or not cfg.faults_enabled:
        raise ValueError("Experiments require enabled test fault controls")
    history = list(db.scalars(select(HistoricalIncident.id).where(HistoricalIncident.indexed.is_(True))))
    experiment = Experiment(
        id=uid(),
        name=request.name,
        configurations=[c.value for c in request.configurations],
        faults=[c.value for c in request.faults],
        trials=request.trials,
        history_snapshot=history,
    )
    db.add(experiment)
    db.flush()
    metadata = {
        "provider": cfg.llm_provider,
        "model": cfg.model_name,
        "temperature": cfg.llm_temperature,
        "prompt_version": cfg.agent_prompt_version,
        "workflow_version": cfg.workflow_version,
        "embedding_provider": cfg.embedding_provider,
        "embedding_model": cfg.embedding_model,
        "software_revision": cfg.software_revision,
        "remediation_mode": cfg.remediation_mode,
        "min_confidence": cfg.remediation_min_confidence,
        "history_count": len(history),
        "history_ids": history,
        "max_steps": cfg.agent_max_steps,
        "max_tool_calls": cfg.agent_max_tool_calls,
        "input_cost_per_million": cfg.llm_input_cost_per_million,
        "output_cost_per_million": cfg.llm_output_cost_per_million,
    }
    for trial in range(1, request.trials + 1):
        for fault in request.faults:
            configs = list(request.configurations)
            offset = (trial - 1) % len(configs)
            for config in configs[offset:] + configs[:offset]:
                db.add(
                    ExperimentRun(
                        experiment_id=experiment.id,
                        configuration=config.value,
                        fault_type=fault.value,
                        ground_truth=fault.value,
                        trial=trial,
                        parameters={
                            "duration_seconds": 70 if fault.value == "WORKER_FAILURE" else 40,
                            "task_count": 3,
                        },
                        metadata_snapshot=metadata,
                    )
                )
    return experiment


def results(db, experiment_id=None, configuration=None, fault_type=None, model=None, since=None):
    query = select(ExperimentRun).order_by(ExperimentRun.created_at)
    if experiment_id:
        query = query.where(ExperimentRun.experiment_id == experiment_id)
    if configuration:
        query = query.where(ExperimentRun.configuration == configuration)
    if fault_type:
        query = query.where(ExperimentRun.fault_type == fault_type)
    if since:
        query = query.where(ExperimentRun.created_at >= since)
    runs = list(db.scalars(query))
    runs = [r for r in runs if not model or r.metadata_snapshot.get("model") == model]
    metrics = {
        m.run_id: m.values
        for m in db.scalars(select(ExperimentMetric).where(ExperimentMetric.run_id.in_([r.id for r in runs])))
    }
    groups = {}
    for run in runs:
        if run.id in metrics:
            groups.setdefault(run.configuration, []).append(metrics[run.id])
    return {
        "runs": [{**row_dict(r), "metrics": metrics.get(r.id)} for r in runs],
        "comparisons": [{"configuration": key, **aggregate(values)} for key, values in groups.items()],
        "summary": aggregate(list(metrics.values())),
        "total_runs": len(runs),
        "skipped_runs": sum(r.status == "SKIPPED" for r in runs),
    }
