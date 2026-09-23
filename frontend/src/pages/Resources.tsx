import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Row, Sample } from "../types";
import {
  Badge,
  DataTable,
  Empty,
  Json,
  Notice,
  PageTitle,
  Panel,
  RecordLink,
  Stat,
} from "../components/ui";
import { date, newestFirst, show } from "../components/format";
import { SeriesChart } from "../components/charts";
export function Workers() {
  const { data, error } = useApi<Row[]>("/workers");
  return (
    <>
      <PageTitle
        title="Worker fleet"
        description="Live Celery heartbeats, active work, reservations, and process statistics."
      />
      <Notice error={error} />
      <div className="cards">
        {(data ?? []).map((w) => (
          <Panel
            key={w.id}
            title={show(w.name)}
            aside={<Badge value={w.status} />}
          >
            <p className="muted">Last heartbeat {date(w.last_heartbeat)}</p>
            <div className="stats compact">
              <Stat
                title="Active"
                value={(w.active as unknown[]).length}
                detail="Currently executing"
              />
              <Stat
                title="Reserved"
                value={(w.reserved as unknown[]).length}
                detail="Waiting in this worker"
              />
              <Stat
                title="Completed"
                value={w.completed}
                detail="Processed since worker start"
              />
            </div>
            <details>
              <summary>Queues, scheduled work, and process statistics</summary>
              <Json
                value={{
                  queues: w.queues,
                  active: w.active,
                  reserved: w.reserved,
                  scheduled: w.scheduled,
                  stats: w.stats,
                }}
              />
            </details>
          </Panel>
        ))}
        {!data?.length && (
          <Empty text="No worker heartbeat has been observed. Check Docker worker and control logs." />
        )}
      </div>
    </>
  );
}
export function Queues() {
  const { data, error } = useApi<{
    status: string;
    depths: Record<string, number>;
  }>("/queues");
  const rows = Object.entries(data?.depths ?? {}).map(([name, depth]) => ({
    id: name,
    created_at: "",
    name,
    depth,
  }));
  return (
    <>
      <PageTitle
        title="Queue activity"
        description="Queue depths measured directly from Redis, including Celery priority buckets."
      />
      <Notice error={error} />
      <Panel
        title="Broker queues"
        aside={<Badge value={data?.status ?? "UNKNOWN"} />}
      >
        <DataTable
          rows={rows}
          columns={[
            { key: "name", name: "Queue" },
            { key: "depth", name: "Waiting tasks" },
          ]}
          empty="Queue depth is unavailable until Redis can be reached."
        />
      </Panel>
      <p className="muted">
        Pause and resume actions are proposed from an incident, verified by
        policy, and require approval in execute mode.
      </p>
    </>
  );
}
export function Metrics() {
  const { data, error } = useApi<Sample[]>("/metrics/timeseries");
  return (
    <>
      <PageTitle
        title="Operational metrics"
        description="Persisted measurements from the control plane. The rolling completion window is 60 seconds."
      />
      <Notice error={error} />
      <div className="two-col">
        <SeriesChart
          title="Tasks per minute"
          data={data ?? []}
          field="tasks_per_minute"
        />
        <SeriesChart
          title="Failure rate (0–1)"
          data={data ?? []}
          field="failure_rate"
        />
        <SeriesChart
          title="Queue depth"
          data={data ?? []}
          field="queue_depth"
        />
        <SeriesChart
          title="Mean task latency, seconds"
          data={data ?? []}
          field="mean_latency_seconds"
        />
      </div>
      <p className="muted">
        Task latency covers the latest execution attempt, excluding queue wait.
        Open Grafana at localhost:3000 for Prometheus dashboards and Jaeger at
        localhost:16686 for traces.
      </p>
    </>
  );
}
export function History() {
  const { data, error } = useApi<Row[]>("/history");
  return (
    <>
      <PageTitle
        title="Recovered incident memory"
        description="Only verified diagnoses with confirmed recovery enter the retrieval corpus."
      />
      <Notice error={error} />
      <div className="cards">
        {newestFirst(data ?? []).map((r) => (
          <Panel
            key={r.id}
            title={"Recovered incident " + String(r.incident_id).slice(0, 8)}
            aside={<Badge value={r.indexed ? "INDEXED" : "INDEX_PENDING"} />}
          >
            <RecordLink id={r.incident_id} />
            <p className="history-document">{show(r.document)}</p>
            <div className="meta">
              {show(r.embedding_model)} · {date(r.created_at)}
            </div>
            {Boolean(r.index_error) && <Notice error={show(r.index_error)} />}
          </Panel>
        ))}
        {!data?.length && (
          <Empty text="No verified recovery has been recorded. Historical retrieval starts empty." />
        )}
      </div>
    </>
  );
}
export function Approvals() {
  const { data, error } = useApi<Row[]>("/approvals");
  const action = useAction();
  return (
    <>
      <PageTitle
        title="Human approvals"
        description="One-use, expiring decisions for policy-approved medium-risk actions."
      />
      <Notice error={error || action.error} message={action.message} />
      <Panel title="Approval queue">
        <DataTable
          rows={newestFirst(data ?? [])}
          columns={[
            {
              key: "action",
              name: "Action",
              render: (r) => (
                <>
                  <strong>{show(r.action)}</strong>
                  <details>
                    <summary>Review target and policy</summary>
                    <p>{show(r.reason)}</p>
                    <Json value={r.parameters} />
                    <RecordLink id={r.incident_id} text="Open incident" />
                  </details>
                </>
              ),
            },
            {
              key: "risk",
              name: "Risk",
              render: (r) => <Badge value={r.risk} />,
            },
            {
              key: "status",
              name: "Decision",
              render: (r) => <Badge value={r.status} />,
            },
            {
              key: "expires_at",
              name: "Expires",
              render: (r) => date(r.expires_at),
            },
            { key: "decided_by", name: "Actor" },
            {
              key: "actions",
              name: "Review",
              render: (r) => (
                <div className="button-row">
                  <button
                    disabled={
                      action.busy ||
                      r.status !== "PENDING" ||
                      new Date(String(r.expires_at)) < new Date()
                    }
                    onClick={() =>
                      void action.run(
                        () =>
                          api(
                            "/remediations/" + r.remediation_id + "/approve",
                            "POST",
                          ),
                        "Approval recorded; execution rechecks current policy",
                      )
                    }
                  >
                    Approve
                  </button>
                  <button
                    disabled={action.busy || r.status !== "PENDING"}
                    onClick={() =>
                      void action.run(
                        () =>
                          api(
                            "/remediations/" + r.remediation_id + "/reject",
                            "POST",
                          ),
                        "Action rejected",
                      )
                    }
                  >
                    Reject
                  </button>
                </div>
              ),
            },
          ]}
          empty="No approvals are waiting. Dry-run proposals never execute."
        />
      </Panel>
      <p className="muted">
        Inspect the corresponding incident’s Remediation tab for action
        parameters and policy reasons before approving. An expired or superseded
        diagnosis cannot authorize an action.
      </p>
    </>
  );
}
export function Health() {
  const { data, error } = useApi<{
    status: string;
    services: Record<
      string,
      {
        status: string;
        detail?: string;
        configured?: boolean;
        error_type?: string;
      }
    >;
  }>("/system/health");
  return (
    <>
      <PageTitle
        title="System health"
        description="Live connectivity probes, with optional integrations reported separately."
        action={<Badge value={data?.status ?? "UNKNOWN"} />}
      />
      <Notice error={error} />
      <div className="health-grid">
        {Object.entries(data?.services ?? {}).map(([name, s]) => (
          <Panel
            key={name}
            title={name.replaceAll("_", " ")}
            aside={<Badge value={s.status} />}
          >
            <p>
              {s.detail ??
                (s.status === "HEALTHY"
                  ? "The service responded to its health probe."
                  : "The service did not pass its connectivity probe.")}
            </p>
            {s.error_type && <code>{s.error_type}</code>}
          </Panel>
        ))}
      </div>
    </>
  );
}
export function Settings() {
  const { data, error } = useApi<Record<string, unknown>>("/settings");
  return (
    <>
      <PageTitle
        title="Configuration"
        description="Effective server configuration. Keys stay on the backend."
      />
      <Notice error={error} />
      <div className="two-col">
        <Panel title="Runtime settings">
          <dl className="details">
            {Object.entries(data ?? {}).map(([k, v]) => (
              <div className="detail-pair" key={k}>
                <dt>{k.replaceAll("_", " ")}</dt>
                <dd>{show(v)}</dd>
              </div>
            ))}
          </dl>
        </Panel>
        <div>
          <Panel title="Update local configuration">
            <p>
              Edit <code>.env</code> in the project folder, then recreate the
              backend and control services:
            </p>
            <pre className="json">
              docker compose up -d --force-recreate backend control
            </pre>
            <p>
              To enable an LLM, set LLM_PROVIDER to groq, ollama, or compatible
              and configure its model and provider key where needed. The rule
              baseline works without a key.
            </p>
            <p>
              Keep REMEDIATION_MODE=dry_run for the first demonstration. Execute
              mode enables eligible actions and medium-risk approvals.
            </p>
          </Panel>
          <Panel title="Account sign-in">
            <p>
              Bootstrap an administrator, then set APP_AUTH_ENABLED=true.
              Each person signs in with an individual account. Sessions can be revoked.
              Backend provider keys are never sent to this dashboard.
            </p>
            <p className="muted">
              Production mode additionally requires HTTPS and secure cookies.
              See docs/IDENTITY_SETUP.md for migration and account setup.
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}
