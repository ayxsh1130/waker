from enum import StrEnum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")




class ConnectorEventInput(StrictModel):
    event_id: str = Field(min_length=8, max_length=100)
    application_id: str = Field(min_length=36, max_length=100)
    event_type: Literal[
        "task.sent",
        "task.received",
        "task.started",
        "task.succeeded",
        "task.failed",
        "task.retried",
        "task.revoked",
        "worker.online",
        "worker.heartbeat",
        "worker.offline",
    ]
    occurred_at: datetime
    task_id: str | None = Field(default=None, max_length=36)
    task_name: str | None = Field(default=None, max_length=200)
    worker_id: str | None = Field(default=None, max_length=200)
    queue: str | None = Field(default=None, max_length=100)
    correlation_id: str | None = Field(default=None, max_length=36)
    trace_id: str | None = Field(default=None, max_length=32)
    payload: dict = Field(default_factory=dict)


class Cause(StrEnum):
    API_TIMEOUT = "API_TIMEOUT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    WORKER_FAILURE = "WORKER_FAILURE"
    TASK_EXCEPTION = "TASK_EXCEPTION"
    POISON_TASK = "POISON_TASK"
    BROKER_DISRUPTION = "BROKER_DISRUPTION"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    OVERLOAD = "OVERLOAD"
    BAD_CONFIGURATION = "BAD_CONFIGURATION"
    DEPLOYMENT_REGRESSION = "DEPLOYMENT_REGRESSION"
    INTERMITTENT_FAILURE = "INTERMITTENT_FAILURE"
    HEALTHY = "HEALTHY"
    UNKNOWN = "UNKNOWN"


class Action(StrEnum):
    RETRY_TASK = "RETRY_TASK"
    RESTART_WORKER = "RESTART_WORKER"
    PAUSE_QUEUE = "PAUSE_QUEUE"
    RESUME_QUEUE = "RESUME_QUEUE"
    QUARANTINE_TASK = "QUARANTINE_TASK"
    CLEAR_RETRY_STATE = "CLEAR_RETRY_STATE"
    NO_ACTION = "NO_ACTION"
    REQUEST_HUMAN = "REQUEST_HUMAN"


class Configuration(StrEnum):
    RULE_BASED = "RULE_BASED"
    LLM_ONLY = "LLM_ONLY"
    LLM_RAG = "LLM_RAG"
    AGENT_TOOLS = "AGENT_TOOLS"
    AGENT_RAG = "AGENT_RAG"
    FULL_SYSTEM = "FULL_SYSTEM"


class DiagnosisOutput(StrictModel):
    root_cause: Cause
    summary: str = Field(min_length=10, max_length=1800)
    affected_component: Literal[
        "dependency",
        "worker",
        "task",
        "broker",
        "database",
        "queue",
        "configuration",
        "deployment",
        "unknown",
    ]
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str] = Field(max_length=15)
    contradicting_evidence: list[str] = Field(max_length=15)
    alternative_hypotheses: list[str] = Field(max_length=5)
    recommended_action: Action


class ToolArguments(StrictModel):
    task_id: str | None = Field(None, max_length=36)
    worker: str | None = Field(None, max_length=100)
    path: str | None = Field(None, max_length=250)
    query: str | None = Field(None, max_length=200)
    start_line: int = Field(1, ge=1, le=100000)
    limit: int = Field(20, ge=1, le=50)


class AgentDecision(StrictModel):
    kind: Literal["tool", "diagnosis"]
    hypothesis: str = Field(max_length=500)
    reason: str = Field(max_length=500)
    tool: str | None = Field(None, max_length=100)
    arguments: ToolArguments = Field(default_factory=ToolArguments)
    diagnosis: DiagnosisOutput | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "tool" and (not self.tool or self.diagnosis is not None):
            raise ValueError("Tool decision requires a tool and no diagnosis")
        if self.kind == "diagnosis" and (self.diagnosis is None or self.tool is not None):
            raise ValueError("Diagnosis decision requires a diagnosis and no tool")
        return self


class TaskInput(StrictModel):
    name: Literal[
        "send_email",
        "process_report",
        "resize_image",
        "call_external_api",
        "process_database_record",
        "data_processing_task",
    ]
    idempotency_key: str = Field(min_length=8, max_length=120)
    count: int = Field(1, ge=1, le=100)
    size: int = Field(128, ge=16, le=1024)
    recipient: str = Field(
        "research@example.test", pattern=r"^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", max_length=160
    )


class WorkloadInput(StrictModel):
    count: int = Field(10, ge=1, le=100)
    name: Literal["mixed", "call_external_api", "data_processing_task"] = "mixed"


class FaultInput(StrictModel):
    fault_type: Cause
    duration_seconds: int = Field(40, ge=10, le=600)
    task_count: int = Field(3, ge=1, le=30)

    @model_validator(mode="before")
    @classmethod
    def worker_test_window(cls, value):
        if isinstance(value, dict) and value.get("fault_type") == Cause.WORKER_FAILURE:
            value = {"duration_seconds": 600, **value}
        return value

    @model_validator(mode="after")
    def supported(self):
        if self.fault_type == Cause.UNKNOWN:
            raise ValueError("UNKNOWN is not an injectable scenario")
        if self.fault_type != Cause.WORKER_FAILURE and self.duration_seconds > 120:
            raise ValueError("Only WORKER_FAILURE supports durations above 120 seconds")
        return self

class ExperimentInput(StrictModel):
    name: str = Field(min_length=3, max_length=150)
    configurations: list[Configuration] = Field(min_length=1, max_length=6)
    faults: list[Cause] = Field(min_length=1, max_length=12)
    trials: int = Field(1, ge=1, le=10)

    @model_validator(mode="after")
    def bounded(self):
        if len(set(self.configurations)) != len(self.configurations) or len(set(self.faults)) != len(
            self.faults
        ):
            raise ValueError("Duplicate scenarios or configurations")
        if Cause.UNKNOWN in self.faults or len(self.configurations) * len(self.faults) * self.trials > 120:
            raise ValueError("Invalid scenario or more than 120 runs")
        return self
