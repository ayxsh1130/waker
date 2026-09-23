import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Sample } from "../types";
import { Empty, Panel } from ".//ui";
const grid = "#e7dcc5",
  text = "#8a7d64";
export function SeriesChart({
  title,
  data,
  field,
}: {
  title: string;
  data: Sample[];
  field:
    | "tasks_per_minute"
    | "active_incidents"
    | "failure_rate"
    | "mean_latency_seconds"
    | "queue_depth";
}) {
  const points = data.map((d) => ({
    ...d,
    time: new Date(d.at).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
    queue_depth: Object.values(d.queue_depths).reduce((a, b) => a + b, 0),
  }));
  return (
    <Panel title={title}>
      {!points.length ? (
        <Empty text="Waiting for the first monitor sample." />
      ) : (
        <div className="chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points}>
              <CartesianGrid stroke={grid} vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: text, fontSize: 11 }}
                minTickGap={40}
              />
              <YAxis tick={{ fill: text, fontSize: 11 }} width={42} />
              <Tooltip
                contentStyle={{
                  background: "#fffdf9",
                  border: "1px solid " + grid,
                  borderRadius: 8,
                }}
              />
              <Area
                type="monotone"
                dataKey={field}
                stroke="#e85d2c"
                fill="#e85d2c"
                fillOpacity={0.1}
                connectNulls={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
export function ComparisonChart({
  title,
  rows,
}: {
  title: string;
  rows: { name: string; value: number | null }[];
}) {
  return (
    <Panel title={title}>
      {!rows.some((r) => r.value !== null) ? (
        <Empty text="No measured values for this comparison." />
      ) : (
        <div className="chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={rows}
              layout="vertical"
              margin={{ left: 5, right: 15 }}
            >
              <CartesianGrid stroke={grid} horizontal={false} />
              <XAxis type="number" tick={{ fill: text, fontSize: 11 }} />
              <YAxis
                type="category"
                dataKey="name"
                width={110}
                tick={{ fill: text, fontSize: 10 }}
              />
              <Tooltip
                contentStyle={{
                  background: "#fffdf9",
                  border: "1px solid " + grid,
                }}
              />
              <Bar
                dataKey="value"
                fill="#e85d2c"
                radius={[0, 4, 4, 0]}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
