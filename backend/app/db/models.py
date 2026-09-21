from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Record, now


class Account(Record, Base):
    __tablename__ = "accounts"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Record, Base):
    __tablename__ = "login_sessions"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginBucket(Base):
    __tablename__ = "login_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)


class Application(Record, Base):
    __tablename__ = "applications"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The existing controller only operates on this installation's local workload.
    kind: Mapped[str] = mapped_column(String(30), default="registered")


class ApplicationMember(Base):
    __tablename__ = "application_members"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)


class ConnectorCredential(Record, Base):
    __tablename__ = "connector_credentials"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessAudit(Record, Base):
    __tablename__ = "access_audit"
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class TaskExecution(Record, Base):
    __tablename__ = "task_executions"
    name: Mapped[str] = mapped_column(String(80))
    queue: Mapped[str] = mapped_column(String(60), index=True)
    worker: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default="PENDING")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    idempotent: Mapped[bool] = mapped_column(Boolean, default=True)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    delivery_count: Mapped[int] = mapped_column(Integer, default=0)
    exception: Mapped[str | None] = mapped_column(Text)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    trace_context: Mapped[dict] = mapped_column(JSON, default=dict)
    scope_id: Mapped[str] = mapped_column(String(36), index=True, default="local")
    dispatch_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)


class TaskLog(Record, Base):
    __tablename__ = "task_logs"
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_executions.id"), index=True)
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Worker(Record, Base):
    __tablename__ = "workers"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(20), default="ONLINE")
    active: Mapped[list] = mapped_column(JSON, default=list)
    reserved: Mapped[list] = mapped_column(JSON, default=list)
    scheduled: Mapped[list] = mapped_column(JSON, default=list)
    queues: Mapped[list] = mapped_column(JSON, default=list)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Incident(Record, Base):
    __tablename__ = "incidents"
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="HIGH")
    status: Mapped[str] = mapped_column(String(40), index=True, default="DETECTED")
    component: Mapped[str] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("task_executions.id"), index=True)
    worker: Mapped[str | None] = mapped_column(String(100))
    queue: Mapped[str | None] = mapped_column(String(60))
    symptoms: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    scope_id: Mapped[str] = mapped_column(String(36), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    investigation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_configuration: Mapped[str] = mapped_column(String(30), default="FULL_SYSTEM")


class IncidentEvent(Record, Base):
    __tablename__ = "incident_events"
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    content: Mapped[dict] = mapped_column(JSON, default=dict)


class Investigation(Record, Base):
    __tablename__ = "investigations"
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), index=True)
    configuration: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(30), default="none")
    prompt_version: Mapped[str] = mapped_column(String(30))
    workflow_version: Mapped[str] = mapped_column(String(30))
    retrieval_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    history_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Evidence(Record, Base):
    __tablename__ = "evidence"
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), index=True)
    investigation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("investigations.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(100))
    component: Mapped[str] = mapped_column(String(80))
    retrieval_method: Mapped[str] = mapped_column(String(80))
    content: Mapped[dict] = mapped_column(JSON)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class InvestigationStep(Record, Base):
    __tablename__ = "investigation_steps"
    investigation_id: Mapped[str] = mapped_column(String(36), ForeignKey("investigations.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    hypothesis: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    tool: Mapped[str | None] = mapped_column(String(100))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("investigation_id", "number"),)


class Diagnosis(Record, Base):
    __tablename__ = "diagnoses"
    investigation_id: Mapped[str] = mapped_column(String(36), ForeignKey("investigations.id"), index=True)
    root_cause: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    affected_component: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float)
    supporting_evidence: Mapped[list] = mapped_column(JSON)
    contradicting_evidence: Mapped[list] = mapped_column(JSON)
    alternative_hypotheses: Mapped[list] = mapped_column(JSON)
    recommended_action: Mapped[str] = mapped_column(String(40))


class DiagnosisEvidence(Record, Base):
    __tablename__ = "diagnosis_evidence"
    diagnosis_id: Mapped[str] = mapped_column(String(36), ForeignKey("diagnoses.id"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(36), ForeignKey("evidence.id"))
    role: Mapped[str] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint("diagnosis_id", "evidence_id", "role"),)


class Verification(Record, Base):
    __tablename__ = "verifications"
    diagnosis_id: Mapped[str] = mapped_column(String(36), ForeignKey("diagnoses.id"), index=True)
    verified: Mapped[bool] = mapped_column(Boolean)
    verification_score: Mapped[float] = mapped_column(Float)
    supporting_checks: Mapped[list] = mapped_column(JSON)
    contradictions: Mapped[list] = mapped_column(JSON)
    missing_evidence: Mapped[list] = mapped_column(JSON)


class Remediation(Record, Base):
    __tablename__ = "remediations"
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), index=True)
    diagnosis_id: Mapped[str] = mapped_column(String(36), ForeignKey("diagnoses.id"), unique=True)
    verification_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("verifications.id"))
    action: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(20))
    policy_decision: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40))
    requested: Mapped[bool] = mapped_column(Boolean, default=False)


class RemediationExecution(Record, Base):
    __tablename__ = "remediation_executions"
    remediation_id: Mapped[str] = mapped_column(String(36), ForeignKey("remediations.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30))
    automatic: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observe_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_result: Mapped[dict | None] = mapped_column(JSON)


class ApprovalRequest(Record, Base):
    __tablename__ = "approval_requests"
    remediation_id: Mapped[str] = mapped_column(String(36), ForeignKey("remediations.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoricalIncident(Record, Base):
    __tablename__ = "historical_incidents"
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), unique=True)
    diagnosis_id: Mapped[str] = mapped_column(String(36), ForeignKey("diagnoses.id"))
    document: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(30))
    embedding_model: Mapped[str] = mapped_column(String(150))
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    index_error: Mapped[str | None] = mapped_column(String(200))


class FaultInjection(Record, Base):
    __tablename__ = "fault_injections"
    scope_id: Mapped[str] = mapped_column(String(36), unique=True)
    fault_type: Mapped[str] = mapped_column(String(60))
    parameters: Mapped[dict] = mapped_column(JSON)
    ground_truth: Mapped[str] = mapped_column(String(60))
    component: Mapped[str] = mapped_column(String(60))
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("experiment_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="SCHEDULED")
    injected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class DeploymentRecord(Record, Base):
    __tablename__ = "deployments"
    scope_id: Mapped[str] = mapped_column(String(36), index=True)
    commit_hash: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(80))
    environment: Mapped[str] = mapped_column(String(30))
    changed_files: Mapped[list] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text)


class Experiment(Record, Base):
    __tablename__ = "experiments"
    name: Mapped[str] = mapped_column(String(150))
    configurations: Mapped[list] = mapped_column(JSON)
    faults: Mapped[list] = mapped_column(JSON)
    trials: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_snapshot: Mapped[list] = mapped_column(JSON)


class ExperimentRun(Record, Base):
    __tablename__ = "experiment_runs"
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id"), index=True)
    configuration: Mapped[str] = mapped_column(String(30))
    fault_type: Mapped[str] = mapped_column(String(60))
    trial: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    ground_truth: Mapped[str] = mapped_column(String(60))
    parameters: Mapped[dict] = mapped_column(JSON)
    metadata_snapshot: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("incidents.id"))
    investigation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("investigations.id"))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("experiment_id", "configuration", "fault_type", "trial"),)


class ExperimentMetric(Record, Base):
    __tablename__ = "experiment_metrics"
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiment_runs.id"), unique=True)
    values: Mapped[dict] = mapped_column(JSON)


class LLMUsage(Record, Base):
    __tablename__ = "llm_usage"
    investigation_id: Mapped[str] = mapped_column(String(36), ForeignKey("investigations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    latency_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))


class MetricSample(Record, Base):
    __tablename__ = "metric_samples"
    values: Mapped[dict] = mapped_column(JSON)


class ProcessedRecord(Record, Base):
    __tablename__ = "processed_records"
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_executions.id"), unique=True)
    value: Mapped[dict] = mapped_column(JSON)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DetectionReceipt(Base):
    __tablename__ = "detection_receipts"
    log_id: Mapped[str] = mapped_column(ForeignKey("task_logs.id"), primary_key=True)


class StreamEvent(Base):
    __tablename__ = "stream_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    topic: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


Index("ix_incident_correlation", Incident.fingerprint, Incident.scope_id, Incident.last_seen_at)
