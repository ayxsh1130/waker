import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Play, ShieldCheck, Workflow } from "lucide-react";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import { configurations } from "../types";
import type { Detail, Incident, Row } from "../types";
import {
  Badge,
  DataTable,
  Empty,
  Json,
  Notice,
  PageTitle,
  Panel,
  RecordLink,
} from "../components/ui";
import { date, number, show } from "../components/format";
export function IncidentList({
  investigation = false,
}: {
  investigation?: boolean;
}) {
  const { data, error } = useApi<Incident[]>("/incidents");
  const [filter, setFilter] = useState("");
  const rows = (data ?? []).filter((r) =>
    (r.title + " " + r.status + " " + r.component)
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        title={investigation ? "Investigation workspace" : "Incident inbox"}
        description={
          investigation
            ? "Open an incident to inspect hypotheses, tool observations, and evidence links."
            : "Correlated operational signals, ordered by detection time."
        }
      />
      <Notice error={error} />
      <label className="search">
        Filter incidents
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search status, component, or title"
        />
      </label>
      <Panel title={`${rows.length} incidents`}>
        <DataTable
          rows={rows}
          columns={[
            {
              key: "title",
              name: "Incident",
              render: (r) => <RecordLink id={r.id} text={r.title} />,
            },
            {
              key: "status",
              name: "State",
              render: (r) => <Badge value={r.status} />,
            },
            {
              key: "severity",
              name: "Severity",
              render: (r) => <Badge value={r.severity} />,
            },
            { key: "component", name: "Component" },
            { key: "occurrence_count", name: "Signals" },
            {
              key: "created_at",
              name: "Detected",
              render: (r) => date(r.created_at),
            },
          ]}
        />
      </Panel>
    </>
  );
}
const tabs = [
  "Overview",
  "Evidence",
  "Investigation",
  "Similar incidents",
  "Diagnosis",
  "Verification",
  "Remediation",
  "Metrics",
  "Raw events",
];
function newestFirst(rows: Row[]): Row[] {
  return [...rows].sort(
    (a, b) =>
      new Date(String(b.created_at ?? "")).getTime() -
      new Date(String(a.created_at ?? "")).getTime(),
  );
}

function Cards({ rows, title }: { rows: Row[]; title: string }) {
  return rows.length ? (
    <div className="cards">
      {newestFirst(rows).map((r) => (
        <Panel
          key={r.id}
          title={title + " · " + r.id.slice(0, 8)}
          aside={<span className="muted">{date(r.created_at)}</span>}
        >
          <Json value={r} />
        </Panel>
      ))}
    </div>
  ) : (
    <Empty text={`No ${title.toLowerCase()} records yet.`} />
  );
}
export function IncidentDetail() {
  const { id } = useParams();
  const { data, error, loading } = useApi<Detail>("/incidents/" + id);
  const [tab, setTab] = useState("Overview");
  const [config, setConfig] = useState("FULL_SYSTEM");
  const action = useAction();
  if (!data)
    return (
      <>
        <PageTitle
          title="Incident detail"
          description={
            loading ? "Loading incident…" : "The incident could not be loaded."
          }
        />
        <Notice error={error} />
      </>
    );
  const incident = data.incident;
  const latest = data.diagnoses.at(-1);
  const verification = data.verifications.filter((r) => r.diagnosis_id === latest?.id).at(-1);
  const pendingApproval = data.approvals.find((r) =>
    r.status === "PENDING"
  );
  const stages = [
    ["Detection", true],
    ["Investigation", data.investigations.length > 0],
    ["Diagnosis", data.diagnoses.length > 0],
    ["Verification", verification?.verified === true],
    ["Remediation", data.executions.length > 0],
    ["Recovery", incident.status === "RECOVERED"],
  ] as const;
  return (
    <>
      <PageTitle
        eyebrow={"Incident / " + incident.id.slice(0, 8)}
        title={incident.title}
        description={incident.description}
        action={<Badge value={incident.status} />}
      />
      <Notice error={error || action.error} message={action.message} />
      {pendingApproval && (
        <div className="notice">
          Approval pending until {date(pendingApproval.expires_at)}.{" "}
          <Link to="/approvals">Review the proposed action</Link>
        </div>
      )}
      <div className="lifecycle">
        {stages.map(([name, reached]) => (
          <div key={name} className={reached ? "reached" : ""}>
            <span />
            {name}
          </div>
        ))}
      </div>
      <div className="toolbar">
        <label>
          Configuration
          <select value={config} onChange={(e) => setConfig(e.target.value)}>
            {configurations.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <button
          className="primary"
          disabled={
            action.busy ||
            incident.investigation_requested ||
            data.investigations.some((r) => r.status === "RUNNING")
          }
          onClick={() =>
            void action.run(
              () =>
                api("/incidents/" + id + "/investigate", "POST", {
                  configuration: config,
                }),
              "Investigation queued",
            )
          }
        >
          <Play size={15} />
          Investigate
        </button>
        <button
          disabled={action.busy || !latest}
          onClick={() =>
            void action.run(
              () => api("/incidents/" + id + "/verify", "POST"),
              "Verification recorded",
            )
          }
        >
          <ShieldCheck size={15} />
          Verify diagnosis
        </button>
        <button
          disabled={action.busy || !latest}
          onClick={() =>
            void action.run(
              () => api("/incidents/" + id + "/remediate", "POST"),
              "Policy decision recorded; inspect Remediation",
            )
          }
        >
          <Workflow size={15} />
          Evaluate remediation
        </button>
      </div>
      <div className="tabs" role="tablist" aria-label="Incident sections">
        {tabs.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Overview" && (
        <div className="two-col">
          <Panel title="Observed signal">
            <dl className="details">
              <dt>Component</dt>
              <dd>{incident.component}</dd>
              <dt>Task</dt>
              <dd>{show(incident.task_id)}</dd>
              <dt>Worker</dt>
              <dd>{show(incident.worker)}</dd>
              <dt>Queue</dt>
              <dd>{show(incident.queue)}</dd>
              <dt>Detected</dt>
              <dd>{date(incident.created_at)}</dd>
              <dt>Recovered</dt>
              <dd>{date(incident.recovered_at)}</dd>
              <dt>Correlated signals</dt>
              <dd>{incident.occurrence_count}</dd>
            </dl>
            <Json value={incident.symptoms} />
          </Panel>
          <Panel title="Latest assessment">
            {latest ? (
              <>
                <h3>{show(latest.root_cause).replaceAll("_", " ")}</h3>
                <p>{show(latest.summary)}</p>
                <dl className="details">
                  <dt>Reported confidence</dt>
                  <dd>{number(Number(latest.confidence) * 100)}%</dd>
                  <dt>Recommended action</dt>
                  <dd>{show(latest.recommended_action)}</dd>
                  <dt>Verification</dt>
                  <dd>
                    {verification ? (
                      <Badge
                        value={
                          verification.verified ? "VERIFIED" : "UNVERIFIED"
                        }
                      />
                    ) : (
                      "Not performed"
                    )}
                  </dd>
                </dl>
                <p className="muted">
                  Confidence is a model or rule score. Deterministic
                  verification and policy are separate controls.
                </p>
              </>
            ) : (
              <Empty text="Start an investigation to collect an assessment." />
            )}
          </Panel>
        </div>
      )}
      {tab === "Evidence" && (
        <div className="cards">
          {newestFirst(data.evidence).map((e) => (
            <Panel
              key={e.id}
              title={show(e.kind)}
              aside={
                <Badge value={e.available ? "AVAILABLE" : "UNAVAILABLE"} />
              }
            >
              <div className="meta">
                {show(e.source)} · {date(e.created_at)} · Evidence {e.id}
              </div>
              <Json value={e.content} />
            </Panel>
          ))}
          {!data.evidence.length && <Empty />}
        </div>
      )}
      {tab === "Investigation" && (
        <>
          <DataTable
            rows={newestFirst(data.investigations)}
            columns={[
              { key: "configuration", name: "Configuration" },
              {
                key: "status",
                name: "Status",
                render: (r) => <Badge value={r.status} />,
              },
              { key: "model", name: "Model" },
              { key: "error", name: "Error" },
            ]}
          />
          <div className="timeline">
            {newestFirst(data.steps).map((s) => (
              <article key={s.id}>
                <span className="step-number">{show(s.number)}</span>
                <div className="step-body">
                  <div className="meta">
                    {show(s.tool ?? s.decision)} · {date(s.created_at)}
                  </div>
                  <h3>{show(s.hypothesis)}</h3>
                  <p>{show(s.reason)}</p>
                  <details>
                    <summary>Parameters and observed evidence</summary>
                    <Json
                      value={{
                        parameters: s.parameters,
                        observation: s.observation,
                        evidence_ids: s.evidence_ids,
                      }}
                    />
                  </details>
                </div>
              </article>
            ))}
          </div>
          <p className="muted">
            The timeline records brief hypotheses and operational observations,
            not private model reasoning.
          </p>
        </>
      )}
      {tab === "Similar incidents" && (
        <Cards
          rows={data.evidence.filter(
            (e) => e.kind === "search_historical_incidents",
          )}
          title="Retrieval"
        />
      )}
      {tab === "Diagnosis" && <Cards rows={data.diagnoses} title="Diagnosis" />}
      {tab === "Verification" && (
        <Cards rows={data.verifications} title="Verification" />
      )}
      {tab === "Remediation" && (
        <>
          <div className="notice">
            A DRY_RUN records a proposed action. Only an execution followed by
            successful observation can produce RECOVERED.
          </div>
          <Cards rows={data.remediations} title="Policy decision" />
          <Cards rows={data.executions} title="Execution and post-check" />
          <Cards rows={data.approvals} title="Approval" />
        </>
      )}
      {tab === "Metrics" && (
        <>
          <Panel title="Provider usage">
            <DataTable
              rows={newestFirst(data.usage)}
              columns={[
                { key: "model", name: "Model" },
                {
                  key: "status",
                  name: "Status",
                  render: (r) => <Badge value={r.status} />,
                },
                { key: "input_tokens", name: "Input tokens" },
                { key: "output_tokens", name: "Output tokens" },
                {
                  key: "latency_seconds",
                  name: "Request seconds",
                  render: (r) => number(r.latency_seconds, 3),
                },
                {
                  key: "estimated_cost",
                  name: "Estimated USD",
                  render: (r) => number(r.estimated_cost, 6),
                },
              ]}
            />
          </Panel>
          <p className="muted">
            Unknown provider token counts and unset price estimates remain
            blank. Experiment metrics appear under Research.
          </p>
        </>
      )}
      {tab === "Raw events" && <Cards rows={data.events} title="Event" />}
    </>
  );
}
