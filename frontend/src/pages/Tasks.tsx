import { useState } from "react";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import { taskNames } from "../types";
import type { Row, Task } from "../types";
import {
  Badge,
  DataTable,
  Json,
  Notice,
  PageTitle,
  Panel,
} from "../components/ui";
import { date, newestFirst } from "../components/format";
function TaskInspector({ id }: { id: string }) {
  const { data, error } = useApi<{ task: Task; logs: Row[] }>("/tasks/" + id);
  return (
    <Panel title={"Task detail · " + id.slice(0, 8)}>
      <Notice error={error} />
      {data && (
        <>
          <Json value={data.task} />
          <h3>Execution logs</h3>
          {data.logs.map((log) => (
            <div className="log" key={log.id}>
              <span>
                {date(log.created_at)} · {String(log.level)}
              </span>
              <p>{String(log.message)}</p>
              <details>
                <summary>Structured context</summary>
                <Json value={log.data} />
              </details>
            </div>
          ))}
        </>
      )}
    </Panel>
  );
}
export default function Tasks() {
  const { data, error } = useApi<Task[]>("/tasks");
  const action = useAction();
  const [name, setName] = useState(taskNames[5]);
  const [count, setCount] = useState(10);
  const [size, setSize] = useState(128);
  const [recipient, setRecipient] = useState("research@example.test");
  const [selected, setSelected] = useState("");
  return (
    <>
      <PageTitle
        title="Task executions"
        description="Six real workload types with durable dispatch, bounded retries, and correlation IDs."
      />
      <Notice error={error || action.error} message={action.message} />
      <Panel title="Submit task">
        <form
          className="toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(
              () =>
                api("/tasks", "POST", {
                  name,
                  count,
                  size,
                  recipient,
                  idempotency_key: crypto.randomUUID(),
                }),
              "Task accepted for durable dispatch",
            );
          }}
        >
          <label>
            Task type
            <select value={name} onChange={(e) => setName(e.target.value)}>
              {taskNames.map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            Records
            <input
              type="number"
              min="1"
              max="100"
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </label>
          {name === "resize_image" && (
            <label>
              Output pixels
              <input
                type="number"
                min="16"
                max="1024"
                value={size}
                onChange={(e) => setSize(Number(e.target.value))}
              />
            </label>
          )}
          {name === "send_email" && (
            <label>
              Mailpit recipient
              <input
                type="email"
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
                required
              />
            </label>
          )}
          <button className="primary" disabled={action.busy}>
            Submit task
          </button>
        </form>
      </Panel>
      <Panel title="Execution ledger">
        <DataTable
          rows={newestFirst(data ?? [])}
          columns={[
            {
              key: "name",
              name: "Task",
              render: (r) => (
                <button className="link" onClick={() => {
                  setSelected(r.id);
                  requestAnimationFrame(() => {
                    document.getElementById("task-inspector")?.scrollIntoView({ behavior: "smooth", block: "start" });
                  });
                }}>
                  {String(r.name)} ↗
                </button>
              ),
            },
            {
              key: "status",
              name: "State",
              render: (r) => <Badge value={r.status} />,
            },
            { key: "queue", name: "Queue" },
            { key: "worker", name: "Worker" },
            { key: "retry_count", name: "Failures" },
            { key: "idempotent", name: "Idempotent" },
            {
              key: "created_at",
              name: "Accepted",
              render: (r) => date(r.created_at),
            },
          ]}
        />
      </Panel>
      {selected && <div id="task-inspector"><TaskInspector id={selected} /></div>}
    </>
  );
}
