import { useState } from "react";
import { Download, FlaskConical } from "lucide-react";
import { useAction, useApi } from "../hooks/useApi";
import { api, base } from "../services/api";
import { configurations, faults } from "../types";
import type { Results, Row } from "../types";
import { ComparisonChart } from "../components/charts";
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
import { number } from "../components/format";
function toggled(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value];
}
export default function Research() {
  const experiments = useApi<Row[]>("/experiments");
  const action = useAction();
  const [name, setName] = useState("Baseline comparison");
  const [selectedConfigs, setConfigs] = useState(["RULE_BASED", "FULL_SYSTEM"]);
  const [selectedFaults, setFaults] = useState(["API_TIMEOUT", "HEALTHY"]);
  const [trials, setTrials] = useState(1);
  const [experiment, setExperiment] = useState("");
  const [config, setConfig] = useState("");
  const [fault, setFault] = useState("");
  const [model, setModel] = useState("");
  const [since, setSince] = useState("");
  const [selected, setSelected] = useState<Row>();
  const params = new URLSearchParams();
  if (experiment) params.set("experiment_id", experiment);
  if (config) params.set("configuration", config);
  if (fault) params.set("fault_type", fault);
  if (model) params.set("model", model);
  if (since) params.set("since", new Date(since).toISOString());
  const results = useApi<Results>("/experiments/results?" + params);
  const count = selectedConfigs.length * selectedFaults.length * trials;
  const ongoing = experiments.data?.some((e) =>
    ["QUEUED", "RUNNING"].includes(String(e.status)),
  );
  return (
    <>
      <PageTitle
        eyebrow="Evaluation"
        title="Research experiments"
        description="Compare six configurations against controlled faults, with frozen historical retrieval and recorded run metadata."
      />
      <Notice
        error={experiments.error || results.error || action.error}
        message={action.message}
      />
      <Panel
        title="Create experiment"
        aside={
          <span className="muted">{count} planned runs · maximum 120</span>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(async () => {
              const created = await api<Row>("/experiments/run", "POST", {
                name,
                configurations: selectedConfigs,
                faults: selectedFaults,
                trials,
              });
              setExperiment(created.id);
            }, "Experiment queued. Runs execute sequentially and clean up between trials.");
          }}
        >
          <div className="toolbar">
            <label>
              Experiment name
              <input
                required
                minLength={3}
                maxLength={150}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              Trials per configuration
              <input
                type="number"
                min="1"
                max="10"
                value={trials}
                onChange={(e) => setTrials(Number(e.target.value))}
              />
            </label>
          </div>
          <fieldset>
            <legend>Configurations</legend>
            <div className="checks">
              {configurations.map((c) => (
                <label key={c}>
                  <input
                    type="checkbox"
                    checked={selectedConfigs.includes(c)}
                    onChange={() => setConfigs(toggled(selectedConfigs, c))}
                  />
                  {c}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>Faults and healthy control</legend>
            <div className="checks">
              {faults.map((f) => (
                <label key={f}>
                  <input
                    type="checkbox"
                    checked={selectedFaults.includes(f)}
                    onChange={() => setFaults(toggled(selectedFaults, f))}
                  />
                  {f}
                </label>
              ))}
            </div>
          </fieldset>
          <button
            className="primary"
            disabled={action.busy || ongoing || count < 1 || count > 120}
          >
            <FlaskConical size={16} />
            Run experiment
          </button>
        </form>
        <p className="muted">
          LLM configurations without a configured provider are skipped. Dry runs
          produce no recovery-time measurement. Approvals pending at trial
          completion are not counted as executed recovery.
        </p>
      </Panel>
      <div className="toolbar filters">
        <label>
          Experiment
          <select
            value={experiment}
            onChange={(e) => setExperiment(e.target.value)}
          >
            <option value="">All experiments</option>
            {(experiments.data ?? []).map((e) => (
              <option key={e.id} value={e.id}>
                {String(e.name)} · {String(e.status)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Configuration
          <select value={config} onChange={(e) => setConfig(e.target.value)}>
            <option value="">All configurations</option>
            {configurations.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Fault
          <select value={fault} onChange={(e) => setFault(e.target.value)}>
            <option value="">All scenarios</option>
            {faults.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          Exact model
          <input
            placeholder="Any model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </label>
        <label>
          Created since
          <input
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
        </label>
        {experiment && (
          <div className="button-row">
            {["csv", "json"].map((format) => (
              <a
                key={format}
                className="button"
                href={
                  base +
                  "/api/experiments/" +
                  experiment +
                  "/export?format=" +
                  format
                }
              >
                <Download size={15} />
                {format.toUpperCase()}
              </a>
            ))}
          </div>
        )}
      </div>
      <div className="stats">
        <Stat
          title="Runs"
          value={results.data?.total_runs}
          detail="Current filters"
        />
        <Stat
          title="Measured runs"
          value={results.data?.summary.runs_with_metrics}
          detail="Runs included in evaluation"
        />
        <Stat
          title="Skipped"
          value={results.data?.skipped_runs}
          detail="Excluded from denominators"
        />
        <Stat
          title="Detection F1"
          value={results.data?.summary.detection.f1}
          detail="Fault detection, including healthy controls"
        />
      </div>
      <div className="two-col">
        {[
          ["diagnosis_accuracy", "Diagnosis accuracy (0–1)"],
          ["mttd_seconds", "Detection time, seconds"],
          ["mttr_seconds", "Confirmed recovery time, seconds"],
          ["investigation_seconds", "Investigation duration, seconds"],
        ].map(([key, title]) => (
          <ComparisonChart
            key={key}
            title={title}
            rows={(results.data?.comparisons ?? []).map((c) => ({
              name: c.configuration,
              value: c.metrics[key]?.mean ?? null,
            }))}
          />
        ))}
      </div>
      <Panel title="Configuration statistics">
        {results.data?.comparisons.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Configuration / metric</th>
                  <th>n</th>
                  <th>Mean</th>
                  <th>Median</th>
                  <th>Sample SD</th>
                </tr>
              </thead>
              <tbody>
                {results.data.comparisons.flatMap((c) =>
                  Object.entries(c.metrics).map(([key, stat]) => (
                    <tr key={c.configuration + key}>
                      <td>
                        <strong>{c.configuration}</strong>
                        <span className="subline">
                          {key.replaceAll("_", " ")}
                        </span>
                      </td>
                      <td>{stat.n}</td>
                      <td>{number(stat.mean, 4)}</td>
                      <td>{number(stat.median, 4)}</td>
                      <td>{number(stat.sample_sd, 4)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty text="No experiment metrics have been recorded. Charts never use prefilled results." />
        )}
      </Panel>
      <Panel title="Individual runs">
        <DataTable
          rows={results.data?.runs ?? []}
          columns={[
            {
              key: "configuration",
              name: "Configuration",
              render: (r) => (
                <button className="link" onClick={() => setSelected(r)}>
                  {String(r.configuration)}
                </button>
              ),
            },
            { key: "fault_type", name: "Scenario" },
            { key: "trial", name: "Trial" },
            {
              key: "status",
              name: "State",
              render: (r) => <Badge value={r.status} />,
            },
            {
              key: "incident_id",
              name: "Incident",
              render: (r) =>
                r.incident_id ? <RecordLink id={r.incident_id} /> : "—",
            },
            { key: "error", name: "Error" },
          ]}
        />
      </Panel>
      {selected && (
        <Panel
          title={"Run metadata · " + selected.id.slice(0, 8)}
          aside={<button onClick={() => setSelected(undefined)}>Close</button>}
        >
          <Json value={selected} />
        </Panel>
      )}
      <p className="muted">
        Accuracy is conditional on a produced diagnosis; detection recall
        reports missed faults separately. MTTR is measured from injection to
        confirmed post-action recovery. Unmeasured values are shown as —. Export
        downloads the full selected experiment, independent of display filters.
      </p>
    </>
  );
}
