export function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
export function number(value: unknown, digits = 1) {
  return typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "—";
}
export function date(value: unknown) {
  return typeof value === "string" ? new Date(value).toLocaleString() : "—";
}
export function label(value: unknown) {
  return show(value).replaceAll("_", " ").toLowerCase();
}
export function newestFirst<T extends { created_at?: unknown }>(
  rows: T[],
): T[] {
  return [...rows].sort(
    (a, b) =>
      new Date(String(b.created_at ?? "")).getTime() -
      new Date(String(a.created_at ?? "")).getTime(),
  );
}
