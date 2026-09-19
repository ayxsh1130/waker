BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE deployments (
    scope_id VARCHAR(36) NOT NULL, 
    commit_hash VARCHAR(80) NOT NULL, 
    version VARCHAR(80) NOT NULL, 
    environment VARCHAR(30) NOT NULL, 
    changed_files JSON NOT NULL, 
    description TEXT NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_deployments_created_at ON deployments (created_at);

CREATE INDEX ix_deployments_scope_id ON deployments (scope_id);

CREATE TABLE experiments (
    name VARCHAR(150) NOT NULL, 
    configurations JSON NOT NULL, 
    faults JSON NOT NULL, 
    trials INTEGER NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    history_snapshot JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_experiments_created_at ON experiments (created_at);

CREATE TABLE metric_samples (
    values JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_metric_samples_created_at ON metric_samples (created_at);

CREATE TABLE runtime_settings (
    key VARCHAR(100) NOT NULL, 
    value JSON NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (key)
);

CREATE TABLE stream_events (
    id SERIAL NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    topic VARCHAR(60) NOT NULL, 
    entity_id VARCHAR(60) NOT NULL, 
    data JSON NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE task_executions (
    name VARCHAR(80) NOT NULL, 
    queue VARCHAR(60) NOT NULL, 
    worker VARCHAR(100), 
    status VARCHAR(30) NOT NULL, 
    payload JSON NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    idempotency_key VARCHAR(120) NOT NULL, 
    idempotent BOOLEAN NOT NULL, 
    quarantined BOOLEAN NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    retry_count INTEGER NOT NULL, 
    delivery_count INTEGER NOT NULL, 
    exception TEXT, 
    stack_trace TEXT, 
    result JSON, 
    correlation_id VARCHAR(36) NOT NULL, 
    trace_id VARCHAR(32) NOT NULL, 
    trace_context JSON NOT NULL, 
    scope_id VARCHAR(36) NOT NULL, 
    dispatch_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    dispatch_attempts INTEGER NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_task_executions_correlation_id ON task_executions (correlation_id);

CREATE INDEX ix_task_executions_created_at ON task_executions (created_at);

CREATE INDEX ix_task_executions_queue ON task_executions (queue);

CREATE INDEX ix_task_executions_scope_id ON task_executions (scope_id);

CREATE INDEX ix_task_executions_status ON task_executions (status);

CREATE INDEX ix_task_executions_trace_id ON task_executions (trace_id);

CREATE INDEX ix_task_executions_worker ON task_executions (worker);

CREATE TABLE workers (
    name VARCHAR(100) NOT NULL, 
    last_heartbeat TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    active JSON NOT NULL, 
    reserved JSON NOT NULL, 
    scheduled JSON NOT NULL, 
    queues JSON NOT NULL, 
    completed INTEGER NOT NULL, 
    stats JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE INDEX ix_workers_created_at ON workers (created_at);

CREATE TABLE incidents (
    title VARCHAR(240) NOT NULL, 
    description TEXT NOT NULL, 
    severity VARCHAR(20) NOT NULL, 
    status VARCHAR(40) NOT NULL, 
    component VARCHAR(80) NOT NULL, 
    task_id VARCHAR(36), 
    worker VARCHAR(100), 
    queue VARCHAR(60), 
    symptoms JSON NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    scope_id VARCHAR(36) NOT NULL, 
    correlation_id VARCHAR(36), 
    trace_id VARCHAR(32), 
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    recovered_at TIMESTAMP WITH TIME ZONE, 
    occurrence_count INTEGER NOT NULL, 
    investigation_requested BOOLEAN NOT NULL, 
    requested_configuration VARCHAR(30) NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(task_id) REFERENCES task_executions (id)
);

CREATE INDEX ix_incident_correlation ON incidents (fingerprint, scope_id, last_seen_at);

CREATE INDEX ix_incidents_created_at ON incidents (created_at);

CREATE INDEX ix_incidents_fingerprint ON incidents (fingerprint);

CREATE INDEX ix_incidents_scope_id ON incidents (scope_id);

CREATE INDEX ix_incidents_status ON incidents (status);

CREATE INDEX ix_incidents_task_id ON incidents (task_id);

CREATE TABLE processed_records (
    task_id VARCHAR(36) NOT NULL, 
    value JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(task_id) REFERENCES task_executions (id), 
    UNIQUE (task_id)
);

CREATE INDEX ix_processed_records_created_at ON processed_records (created_at);

CREATE TABLE task_logs (
    task_id VARCHAR(36) NOT NULL, 
    level VARCHAR(20) NOT NULL, 
    message TEXT NOT NULL, 
    data JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(task_id) REFERENCES task_executions (id)
);

CREATE INDEX ix_task_logs_created_at ON task_logs (created_at);

CREATE INDEX ix_task_logs_task_id ON task_logs (task_id);

CREATE TABLE detection_receipts (
    log_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (log_id), 
    FOREIGN KEY(log_id) REFERENCES task_logs (id)
);

CREATE TABLE incident_events (
    incident_id VARCHAR(36) NOT NULL, 
    kind VARCHAR(60) NOT NULL, 
    content JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id)
);

CREATE INDEX ix_incident_events_created_at ON incident_events (created_at);

CREATE INDEX ix_incident_events_incident_id ON incident_events (incident_id);

CREATE TABLE investigations (
    incident_id VARCHAR(36) NOT NULL, 
    configuration VARCHAR(30) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    model VARCHAR(100) NOT NULL, 
    provider VARCHAR(30) NOT NULL, 
    prompt_version VARCHAR(30) NOT NULL, 
    workflow_version VARCHAR(30) NOT NULL, 
    retrieval_snapshot JSON NOT NULL, 
    history_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id)
);

CREATE INDEX ix_investigations_created_at ON investigations (created_at);

CREATE INDEX ix_investigations_incident_id ON investigations (incident_id);

CREATE TABLE diagnoses (
    investigation_id VARCHAR(36) NOT NULL, 
    root_cause VARCHAR(60) NOT NULL, 
    summary TEXT NOT NULL, 
    affected_component VARCHAR(80) NOT NULL, 
    confidence FLOAT NOT NULL, 
    supporting_evidence JSON NOT NULL, 
    contradicting_evidence JSON NOT NULL, 
    alternative_hypotheses JSON NOT NULL, 
    recommended_action VARCHAR(40) NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(investigation_id) REFERENCES investigations (id)
);

CREATE INDEX ix_diagnoses_created_at ON diagnoses (created_at);

CREATE INDEX ix_diagnoses_investigation_id ON diagnoses (investigation_id);

CREATE TABLE evidence (
    incident_id VARCHAR(36) NOT NULL, 
    investigation_id VARCHAR(36), 
    kind VARCHAR(80) NOT NULL, 
    source VARCHAR(100) NOT NULL, 
    component VARCHAR(80) NOT NULL, 
    retrieval_method VARCHAR(80) NOT NULL, 
    content JSON NOT NULL, 
    available BOOLEAN NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id), 
    FOREIGN KEY(investigation_id) REFERENCES investigations (id)
);

CREATE INDEX ix_evidence_created_at ON evidence (created_at);

CREATE INDEX ix_evidence_incident_id ON evidence (incident_id);

CREATE INDEX ix_evidence_investigation_id ON evidence (investigation_id);

CREATE TABLE experiment_runs (
    experiment_id VARCHAR(36) NOT NULL, 
    configuration VARCHAR(30) NOT NULL, 
    fault_type VARCHAR(60) NOT NULL, 
    trial INTEGER NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    ground_truth VARCHAR(60) NOT NULL, 
    parameters JSON NOT NULL, 
    metadata_snapshot JSON NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    incident_id VARCHAR(36), 
    investigation_id VARCHAR(36), 
    error TEXT, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(experiment_id) REFERENCES experiments (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id), 
    FOREIGN KEY(investigation_id) REFERENCES investigations (id), 
    UNIQUE (experiment_id, configuration, fault_type, trial)
);

CREATE INDEX ix_experiment_runs_created_at ON experiment_runs (created_at);

CREATE INDEX ix_experiment_runs_experiment_id ON experiment_runs (experiment_id);

CREATE TABLE investigation_steps (
    investigation_id VARCHAR(36) NOT NULL, 
    number INTEGER NOT NULL, 
    hypothesis TEXT NOT NULL, 
    reason TEXT NOT NULL, 
    tool VARCHAR(100), 
    parameters JSON NOT NULL, 
    observation JSON NOT NULL, 
    evidence_ids JSON NOT NULL, 
    decision VARCHAR(40) NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(investigation_id) REFERENCES investigations (id), 
    UNIQUE (investigation_id, number)
);

CREATE INDEX ix_investigation_steps_created_at ON investigation_steps (created_at);

CREATE INDEX ix_investigation_steps_investigation_id ON investigation_steps (investigation_id);

CREATE TABLE llm_usage (
    investigation_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(40) NOT NULL, 
    model VARCHAR(100) NOT NULL, 
    input_tokens INTEGER, 
    output_tokens INTEGER, 
    estimated_cost FLOAT, 
    latency_seconds FLOAT NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(investigation_id) REFERENCES investigations (id)
);

CREATE INDEX ix_llm_usage_created_at ON llm_usage (created_at);

CREATE INDEX ix_llm_usage_investigation_id ON llm_usage (investigation_id);

CREATE TABLE diagnosis_evidence (
    diagnosis_id VARCHAR(36) NOT NULL, 
    evidence_id VARCHAR(36) NOT NULL, 
    role VARCHAR(20) NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(diagnosis_id) REFERENCES diagnoses (id), 
    FOREIGN KEY(evidence_id) REFERENCES evidence (id), 
    UNIQUE (diagnosis_id, evidence_id, role)
);

CREATE INDEX ix_diagnosis_evidence_created_at ON diagnosis_evidence (created_at);

CREATE INDEX ix_diagnosis_evidence_diagnosis_id ON diagnosis_evidence (diagnosis_id);

CREATE TABLE experiment_metrics (
    run_id VARCHAR(36) NOT NULL, 
    values JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(run_id) REFERENCES experiment_runs (id), 
    UNIQUE (run_id)
);

CREATE INDEX ix_experiment_metrics_created_at ON experiment_metrics (created_at);

CREATE TABLE fault_injections (
    scope_id VARCHAR(36) NOT NULL, 
    fault_type VARCHAR(60) NOT NULL, 
    parameters JSON NOT NULL, 
    ground_truth VARCHAR(60) NOT NULL, 
    component VARCHAR(60) NOT NULL, 
    run_id VARCHAR(36), 
    status VARCHAR(30) NOT NULL, 
    injected_at TIMESTAMP WITH TIME ZONE, 
    reset_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(run_id) REFERENCES experiment_runs (id), 
    UNIQUE (scope_id)
);

CREATE INDEX ix_fault_injections_created_at ON fault_injections (created_at);

CREATE INDEX ix_fault_injections_run_id ON fault_injections (run_id);

CREATE TABLE historical_incidents (
    incident_id VARCHAR(36) NOT NULL, 
    diagnosis_id VARCHAR(36) NOT NULL, 
    document TEXT NOT NULL, 
    outcome VARCHAR(30) NOT NULL, 
    embedding_model VARCHAR(150) NOT NULL, 
    indexed BOOLEAN NOT NULL, 
    index_error VARCHAR(200), 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(diagnosis_id) REFERENCES diagnoses (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id), 
    UNIQUE (incident_id)
);

CREATE INDEX ix_historical_incidents_created_at ON historical_incidents (created_at);

CREATE TABLE verifications (
    diagnosis_id VARCHAR(36) NOT NULL, 
    verified BOOLEAN NOT NULL, 
    verification_score FLOAT NOT NULL, 
    supporting_checks JSON NOT NULL, 
    contradictions JSON NOT NULL, 
    missing_evidence JSON NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(diagnosis_id) REFERENCES diagnoses (id)
);

CREATE INDEX ix_verifications_created_at ON verifications (created_at);

CREATE INDEX ix_verifications_diagnosis_id ON verifications (diagnosis_id);

CREATE TABLE remediations (
    incident_id VARCHAR(36) NOT NULL, 
    diagnosis_id VARCHAR(36) NOT NULL, 
    verification_id VARCHAR(36), 
    action VARCHAR(40) NOT NULL, 
    parameters JSON NOT NULL, 
    risk VARCHAR(20) NOT NULL, 
    policy_decision VARCHAR(40) NOT NULL, 
    reason TEXT NOT NULL, 
    mode VARCHAR(20) NOT NULL, 
    status VARCHAR(40) NOT NULL, 
    requested BOOLEAN NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(diagnosis_id) REFERENCES diagnoses (id), 
    FOREIGN KEY(incident_id) REFERENCES incidents (id), 
    FOREIGN KEY(verification_id) REFERENCES verifications (id), 
    UNIQUE (diagnosis_id)
);

CREATE INDEX ix_remediations_created_at ON remediations (created_at);

CREATE INDEX ix_remediations_incident_id ON remediations (incident_id);

CREATE TABLE approval_requests (
    remediation_id VARCHAR(36) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    decided_by VARCHAR(100), 
    decided_at TIMESTAMP WITH TIME ZONE, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(remediation_id) REFERENCES remediations (id), 
    UNIQUE (remediation_id)
);

CREATE INDEX ix_approval_requests_created_at ON approval_requests (created_at);

CREATE TABLE remediation_executions (
    remediation_id VARCHAR(36) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    automatic BOOLEAN NOT NULL, 
    result JSON NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    observe_after TIMESTAMP WITH TIME ZONE, 
    post_result JSON, 
    id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(remediation_id) REFERENCES remediations (id), 
    UNIQUE (remediation_id)
);

CREATE INDEX ix_remediation_executions_created_at ON remediation_executions (created_at);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

