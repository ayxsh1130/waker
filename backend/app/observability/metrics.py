from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

from app.db.models import (
    Diagnosis,
    Incident,
    Investigation,
    InvestigationStep,
    LLMUsage,
    MetricSample,
    RemediationExecution,
    TaskExecution,
    Worker,
)
from app.db.session import session_scope


class DatabaseCollector:
    def collect(self):
        with session_scope() as db:
            for name, model, column, description in [
                (
                    "autopilot_tasks",
                    TaskExecution,
                    TaskExecution.status,
                    "Durable task counts by current status",
                ),
                (
                    "autopilot_incidents",
                    Incident,
                    Incident.status,
                    "Durable incident counts by lifecycle status",
                ),
                ("autopilot_investigations", Investigation, Investigation.status, "Investigation outcomes"),
                (
                    "autopilot_remediations",
                    RemediationExecution,
                    RemediationExecution.status,
                    "Remediation execution outcomes",
                ),
            ]:
                metric = GaugeMetricFamily(name, description, labels=["status"])
                for status, count in db.execute(select(column, func.count(model.id)).group_by(column)):
                    metric.add_metric([status], count)
                yield metric
            workers = GaugeMetricFamily(
                "autopilot_worker_online", "Latest observed worker availability", labels=["worker"]
            )
            for w in db.scalars(select(Worker)):
                workers.add_metric([w.name], int(w.status == "ONLINE"))
            yield workers
            sample = db.scalar(select(MetricSample).order_by(MetricSample.created_at.desc()).limit(1))
            if sample:
                queues = GaugeMetricFamily(
                    "autopilot_queue_depth", "Measured Redis queue length", labels=["queue"]
                )
                for queue, depth in sample.values.get("queue_depths", {}).items():
                    queues.add_metric([queue], depth)
                yield queues
                for key in ["tasks_per_minute", "failure_rate", "mean_latency_seconds", "active_incidents"]:
                    value = sample.values.get(key)
                    if value is not None:
                        yield GaugeMetricFamily("autopilot_" + key, "Latest sampled " + key, value=value)
            tools = GaugeMetricFamily(
                "autopilot_tool_calls", "Persisted diagnostic tool call count", labels=["tool"]
            )
            for name, count in db.execute(
                select(InvestigationStep.tool, func.count(InvestigationStep.id))
                .where(InvestigationStep.tool.is_not(None))
                .group_by(InvestigationStep.tool)
            ):
                tools.add_metric([name], count)
            yield tools
            for key, column in [
                ("input_tokens", LLMUsage.input_tokens),
                ("output_tokens", LLMUsage.output_tokens),
            ]:
                value = db.scalar(select(func.sum(column)))
                if value is not None:
                    yield GaugeMetricFamily(
                        "autopilot_llm_" + key, "Reported tokens from recorded provider requests", value=value
                    )
            confidence = db.scalar(select(func.avg(Diagnosis.confidence)))
            if confidence is not None:
                yield GaugeMetricFamily(
                    "autopilot_diagnosis_confidence",
                    "Mean reported confidence; not a calibrated probability",
                    value=confidence,
                )


def exposition():
    registry = CollectorRegistry()
    registry.register(DatabaseCollector())
    return generate_latest(registry)
