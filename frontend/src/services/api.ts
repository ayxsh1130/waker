export const base =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  ) || "";
let csrfToken: string | null = null;

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const write = !["GET", "HEAD"].includes(method);
  if (write && path !== "/session" && !csrfToken) {
    await api("/session");
  }
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (write && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(base + "/api" + path, {
    method,
    credentials: "include",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 401 && csrfToken) {
      csrfToken = null;
      refreshData();
    }
    let detail = "Service request failed";
    try {
      const data = await response.json();
      detail =
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail);
    } catch {
      /* Non-JSON server error has no safe detail */
    }
    throw new Error(`${response.status} · ${detail}`);
  }
  const result = await response.json();
  if (path === "/session") csrfToken = result.csrf_token ?? null;
  return result as T;
}
export function refreshData() {
  window.dispatchEvent(new Event("autopilot:refresh"));
}
