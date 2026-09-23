import { Activity, Plus } from "lucide-react";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Incident, Row, Sample, Summary, Task } from "../types";
import {
  Badge,
  DataTable,
  Notice,
  PageTitle,
  Panel,
  RecordLink,
  Stat,
} from "../components/ui";
import { date, newestFirst, show } from "../components/format";
import { SeriesChart } from "../components/charts";
export default function Overview() {
  const summary = useApi<Summary>("/metrics/summary");
  const samples = useApi<Sample[]>("/metrics/timeseries");
  const incidents = useApi<Incident[]>("/incidents");
  const tasks = useApi<Task[]>("/tasks");
  const approvals = useApi<Row[]>("/approvals");
  const action = useAction();
  const latest = summary.data?.latest;
  const pendingApprovals = newestFirst(approvals.data ?? []).filter(
    (r) => r.status === "PENDING",
  );
  return (
    <>
      <PageTitle
        eyebrow="Control center"
        title="System overview"
        description="Observe the workload. Investigate the evidence. Verify the outcome."
        action={
          <button
            className="primary"
            disabled={action.busy}
            onClick={() =>
              void action.run(
                () => api("/workload", "POST", { count: 12, name: "mixed" }),
                "12 tasks accepted for dispatch",
              )
            }
          >
            <Plus size={16} />
            Run workload
          </button>
        }
      />
      <Notice
        error={
          summary.error ||
          incidents.error ||
          samples.error ||
          tasks.error ||
          approvals.error ||
          action.error
        }
        message={action.message}
      />
      <div className="stats">
        <Stat
          title="Active incidents"
          value={latest?.active_incidents}
          detail="Open operational incidents"
        />
        <Stat
          title="Throughput"
          value={latest?.tasks_per_minute}
          unit="/ min"
          detail="Completions in the last 60 seconds"
        />
        <Stat
          title="Failure rate"
          value={
            latest?.failure_rate == null ? null : latest.failure_rate * 100
          }
          unit="%"
          detail="Terminal failures / recent completions"
        />
        <Stat
          title="Pending approvals"
          value={pendingApprovals.length}
          detail="Awaiting a human decision"
        />
      </div>
      <div className="section-note">
        <Activity size={15} />
        <span>
          {latest
            ? "Latest observation: " + date(latest.at)
            : "Waiting for control-plane measurements. No sample data is preloaded."}
        </span>
      </div>
      <div className="two-col">
        <SeriesChart
          title="Task throughput"
          field="tasks_per_minute"
          data={samples.data ?? []}
        />
        <SeriesChart
          title="Active incidents"
          field="active_incidents"
          data={samples.data ?? []}
        />
      </div>
      <div className="three-col">
        <Panel
          title="Incidents"
          aside={
            <span className="muted">
              {summary.data?.recovered_incidents ?? 0} recovered
            </span>
          }
        >
          <DataTable
            rows={newestFirst(incidents.data ?? []).slice(0, 6)}
            empty="No incidents observed yet."
            columns={[
              {
                key: "title",
                name: "Incident",
                render: (r) => <RecordLink id={r.id} text={r.title} />,
              },
              {
                key: "status",
                name: "Status",
                render: (r) => <Badge value={r.status} />,
              },
              {
                key: "created_at",
                name: "Detected",
                render: (r) => date(r.created_at),
              },
            ]}
          />
        </Panel>
        <Panel
          title="Tasks"
          aside={<span className="muted">{tasks.data?.length ?? 0} total</span>}
        >
          <DataTable
            rows={newestFirst(tasks.data ?? []).slice(0, 6)}
            empty="No tasks submitted yet."
            columns={[
              { key: "name", name: "Task" },
              {
                key: "status",
                name: "State",
                render: (r) => <Badge value={r.status} />,
              },
              {
                key: "created_at",
                name: "Accepted",
                render: (r) => date(r.created_at),
              },
            ]}
          />
        </Panel>
        <Panel
          title="Approvals"
          aside={<span className="muted">{pendingApprovals.length} pending</span>}
        >
          <DataTable
            rows={pendingApprovals.slice(0, 6)}
            empty="Nothing is waiting on a human decision."
            columns={[
              { key: "action", name: "Action", render: (r) => show(r.action) },
              {
                key: "risk",
                name: "Risk",
                render: (r) => <Badge value={r.risk} />,
              },
              {
                key: "expires_at",
                name: "Expires",
                render: (r) => date(r.expires_at),
              },
            ]}
          />
        </Panel>
      </div>
    </>
  );
}
