import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import { faults } from "../types";
import type { Row } from "../types";
import { Badge, DataTable, Notice, PageTitle, Panel } from "../components/ui";
import { date, show } from "../components/format";
const explanations: Record<string, string> = {
  API_TIMEOUT: "The dependency sleeps beyond the HTTP client timeout.",
  DEPENDENCY_ERROR: "The dependency returns actual HTTP 500 responses.",
  WORKER_FAILURE:
    "Kills the isolated experimental worker after a task starts, then restarts it during cleanup.",
  TASK_EXCEPTION:
    "A temporary validation error is raised inside task execution.",
  POISON_TASK:
    "Persistent record validation failure exhausts bounded task retries.",
  BROKER_DISRUPTION:
    "Closes the experimental worker’s Redis TCP connections through the private proxy.",
  DATABASE_FAILURE:
    "The workload attempts a real connection to an unavailable database endpoint.",
  OVERLOAD:
    "Submits a bounded burst of slow tasks to build a measurable queue.",
  BAD_CONFIGURATION:
    "An invalid numeric setting produces a real parsing exception.",
  DEPLOYMENT_REGRESSION:
    "Selects a controlled v2 implementation containing a divisor regression.",
  INTERMITTENT_FAILURE:
    "The dependency alternates actual successful and failed HTTP responses.",
  HEALTHY: "Runs the same controlled workload without an injected failure.",
};
export default function Faults() {
  const { data, error } = useApi<Row[]>("/faults");
  const action = useAction();
  const [fault, setFault] = useState(faults[0]);
  const [duration, setDuration] = useState("40");
  const [count, setCount] = useState("3");
  const active = (data ?? []).some((r) =>
    ["SCHEDULED", "ACTIVE", "RESETTING"].includes(String(r.status)),
  );
  return (
    <>
      <PageTitle
        eyebrow="Test environment"
        title="Fault laboratory"
        description="Inject bounded faults into isolated experimental tasks and observe the resulting signals."
      />
      <div className="notice warning">
        <FlaskConical size={18} />
        Faults affect the local experimental worker and its tasks. They
        automatically expire. Only one scenario runs at a time.
      </div>
      <Notice error={error || action.error} message={action.message} />
      <Panel title="Configure scenario">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!e.currentTarget.reportValidity()) return;
            void action.run(
              () =>
                api("/faults/inject", "POST", {
                  fault_type: fault,
                  duration_seconds: Number(duration),
                  task_count: Number(count),
                }),
              "Scenario scheduled; operational evidence will appear in Incidents",
            );
          }}
        >
          <div className="toolbar">
            <label>
              Scenario
              <select value={fault} onChange={(e) => {
                setFault(e.target.value);
                setDuration(e.target.value === "WORKER_FAILURE" ? "600" : "40");
              }}>
                {faults.map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </label>
            <label>
              Duration, seconds
              <input
                type="number"
                required
                step="1"
                min="10"
                max={fault === "WORKER_FAILURE" ? 600 : 120}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
              />
            </label>
            <label>
              Tasks
              <input
                type="number"
                required
                step="1"
                min="1"
                max="30"
                value={count}
                onChange={(e) => setCount(e.target.value)}
              />
            </label>
            <button className="primary" disabled={action.busy || active}>
              Inject scenario
            </button>
          </div>
          <p>{explanations[fault]}</p>
          {fault === "WORKER_FAILURE" && (
            <p>For manual worker tests, pending approvals extend cleanup up to their deadline,
              with a maximum of 30 minutes after the crash. Reset cancels pending restart approvals.</p>
          )}
        </form>
      </Panel>
      <Panel title="Injection and cleanup ledger">
        <DataTable
          rows={data ?? []}
          columns={[
            { key: "fault_type", name: "Scenario" },
            {
              key: "status",
              name: "State",
              render: (r) => <Badge value={r.status} />,
            },
            {
              key: "injected_at",
              name: "Activated",
              render: (r) => date(r.injected_at),
            },
            { key: "reset_at", name: "Reset", render: (r) => date(r.reset_at) },
            { key: "error", name: "Error" },
            {
              key: "reset",
              name: "Control",
              render: (r) => (
                <button
                  disabled={action.busy || r.status === "RESET" || !!r.run_id}
                  onClick={() =>
                    void action.run(
                      () => api("/faults/" + r.id + "/reset", "POST"),
                      "Cleanup requested",
                    )
                  }
                >
                  {r.run_id ? "Experiment controlled" : "Reset"}
                </button>
              ),
            },
          ]}
        />
      </Panel>
      <p className="muted">
        This ledger is visible to the operator and evaluator. Its labels and
        configuration are excluded from diagnostic tools, prompts, and
        historical retrieval. Active scope:{" "}
        {show(data?.find((r) => r.status === "ACTIVE")?.scope_id)}
      </p>
    </>
  );
}
