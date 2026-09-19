import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, CircleDashed } from "lucide-react";
import type { Row } from "../types";
import { show, label, number } from "./format";
export function Badge({ value }: { value: unknown }) {
  const status = show(value);
  const good = [
    "HEALTHY",
    "ONLINE",
    "RECOVERED",
    "SUCCEEDED",
    "COMPLETED",
    "VERIFIED",
    "APPROVED",
    "ALLOW",
    "RESET",
  ].includes(status);
  const bad = [
    "FAILED",
    "UNAVAILABLE",
    "OFFLINE",
    "DENIED",
    "UNCERTAIN",
    "QUARANTINED",
  ].includes(status);
  return (
    <span className={`badge ${good ? "good" : bad ? "bad" : "neutral"}`}>
      {label(value)}
    </span>
  );
}
export function PageTitle({
  eyebrow = "Operations",
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function Panel({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function Empty({
  text = "No records yet. Run a workload to start collecting observations.",
}: {
  text?: string;
}) {
  return (
    <div className="empty">
      <CircleDashed size={26} />
      <p>{text}</p>
    </div>
  );
}
export function Notice({
  error,
  message,
}: {
  error?: string;
  message?: string;
}) {
  return (
    <>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="notice" role="status">
          {message}
        </div>
      )}
    </>
  );
}
export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
export function Stat({
  title,
  value,
  unit,
  detail,
}: {
  title: string;
  value: unknown;
  unit?: string;
  detail: string;
}) {
  return (
    <div className="stat">
      <span>{title}</span>
      <strong>
        {number(value)}
        <small>{unit}</small>
      </strong>
      <p>{detail}</p>
    </div>
  );
}
export function DataTable({
  rows,
  columns,
  empty,
}: {
  rows: Row[];
  columns: { key: string; name: string; render?: (row: Row) => ReactNode }[];
  empty?: string;
}) {
  if (!rows.length) return <Empty text={empty} />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((c) => (
                <td key={c.key}>
                  {c.render ? c.render(row) : show(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function RecordLink({ id, text }: { id: unknown; text?: unknown }) {
  return (
    <Link className="record-link" to={"/incidents/" + show(id)}>
      {show(text ?? String(id).slice(0, 8))}
      <ArrowUpRight size={14} />
    </Link>
  );
}
