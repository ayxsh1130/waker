import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  Activity,
  Bell,
  BookOpen,
  Boxes,
  ChevronRight,
  Cpu,
  FlaskConical,
  Gauge,
  GitBranch,
  HeartPulse,
  LayoutDashboard,
  ListTodo,
  Menu,
  Network,
  Radio,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldCheck,
  X,
} from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { useApi, useLive } from "./hooks/useApi";
import { api, refreshData } from "./services/api";
import { Notice } from "./components/ui";
const Access = lazy(() => import("./pages/Access"));
const Overview = lazy(() => import("./pages/Overview"));
const Tasks = lazy(() => import("./pages/Tasks"));
const Faults = lazy(() => import("./pages/Faults"));
const Research = lazy(() => import("./pages/Research"));
const IncidentDetail = lazy(() =>
  import("./pages/Incidents").then((m) => ({ default: m.IncidentDetail })),
);
const IncidentList = lazy(() =>
  import("./pages/Incidents").then((m) => ({ default: m.IncidentList })),
);
const Approvals = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Approvals })),
);
const Health = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Health })),
);
const History = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.History })),
);
const Metrics = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Metrics })),
);
const Queues = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Queues })),
);
const Settings = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Settings })),
);
const Workers = lazy(() =>
  import("./pages/Resources").then((m) => ({ default: m.Workers })),
);
const links = [
  ["overview", "Overview", LayoutDashboard],
  ["incidents", "Incidents", Bell],
  ["investigation", "Investigation", GitBranch],
  ["history", "Incident memory", BookOpen],
  ["tasks", "Tasks", ListTodo],
  ["workers", "Workers", Cpu],
  ["queues", "Queues", Boxes],
  ["metrics", "Metrics", Gauge],
  ["faults", "Fault laboratory", FlaskConical],
  ["approvals", "Approvals", ShieldCheck],
  ["health", "System health", HeartPulse],
  ["research", "Research", Network],
  ["settings", "Settings", SettingsIcon],
  ["access", "Accounts & applications", ShieldCheck],
] as const;
type SessionData = {
  auth_enabled: boolean;
  authenticated: boolean;
  local_access: boolean;
  user: { id: string; email: string; role: string } | null;
};
function Workspace({ session }: { session: SessionData }) {
  const connected = useLive();
  const [menu, setMenu] = useState(false);
  const location = useLocation();
  const cfg = useApi<{ remediation_mode: string }>("/settings");
  const current = links.find(([path]) =>
    location.pathname.startsWith("/" + path),
  );
  return (
    <div className="workspace">
      <aside className={menu ? "sidebar open" : "sidebar"}>
        <Link className="brand" to="/overview">
          <span className="brand-icon">
            <Activity size={24} />
          </span>
          <div>
            Waker<small>Operations intelligence</small>
          </div>
        </Link>
        <div className="workspace-label">
          <span className="tiny-dot" />
          Local environment<span className="key">DEV</span>
        </div>
        <nav aria-label="Main navigation">
          {links.filter(([path]) => session.auth_enabled || path !== "access").map(([path, name, Icon], i) => (
            <div key={path}>
              {i === 4 && <div className="nav-group">Infrastructure</div>}
              {i === 8 && <div className="nav-group">Control & research</div>}
              <NavLink to={"/" + path} onClick={() => setMenu(false)}>
                <Icon size={17} />
                {name}
              </NavLink>
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className={"tiny-dot " + (connected ? "live" : "")} />
          {connected ? "Live observations" : "Reconnecting · refresh every 30s"}
          <small>Local research prototype · v1.0</small>
        </div>
      </aside>
      {menu && (
        <button
          className="scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-toggle"
              aria-label="Toggle navigation"
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X size={18} /> : <Menu size={18} />}
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{current?.[1] ?? "Incident detail"}</strong>
          </div>
          <div className="top-actions">
            <span
              className={
                "mode " +
                (cfg.data?.remediation_mode === "execute" ? "execute" : "")
              }
            >
              <ShieldCheck size={14} />
              {cfg.data?.remediation_mode?.replace("_", " ") ?? "Loading mode"}
            </span>
            <button
              className="icon-button"
              onClick={refreshData}
              aria-label="Refresh data"
            >
              <RefreshCw size={16} />
            </button>
            <span>{session.user?.role}</span>
            {session.auth_enabled && <button onClick={() => { void api("/session", "DELETE").then(refreshData).catch(() => refreshData()); }}>Sign out</button>}
          </div>
        </header>
        <main>
          <Suspense fallback={<div className="empty">Loading workspace…</div>}>
            <Routes>
              <Route path="/overview" element={<Overview />} />
              <Route path="/incidents" element={<IncidentList />} />
              <Route path="/incidents/:id" element={<IncidentDetail />} />
              <Route
                path="/investigation"
                element={<IncidentList investigation />}
              />
              <Route path="/history" element={<History />} />
              <Route path="/tasks" element={<Tasks />} />
              <Route path="/workers" element={<Workers />} />
              <Route path="/queues" element={<Queues />} />
              <Route path="/metrics" element={<Metrics />} />
              <Route path="/faults" element={<Faults />} />
              <Route path="/approvals" element={<Approvals />} />
              <Route path="/health" element={<Health />} />
              <Route path="/research" element={<Research />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/access" element={<Access role={session.user?.role ?? "viewer"} />} />
              <Route path="*" element={<Navigate to="/overview" replace />} />
            </Routes>
          </Suspense>
        </main>
        <footer>
          <Radio size={13} />
          Observed data from your local environment
          <span>Evidence before action.</span>
        </footer>
      </div>
    </div>
  );
}
function Session() {
  const session = useApi<SessionData>("/session");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!session.data) return <div className="login"><h1>Waker</h1><p>Connecting to the operations API…</p><Notice error={session.error} /><button onClick={refreshData}>Retry connection</button></div>;
  if (!session.data.authenticated) return (
    <div className="login">
      <Activity size={36} /><h1>Sign in to Waker</h1>
      <form onSubmit={async (event) => {
        event.preventDefault(); setBusy(true); setError("");
        try { await api("/session", "POST", { email, password }); setPassword(""); refreshData(); }
        catch (e) { setError(e instanceof Error ? e.message : "Sign-in failed"); }
        finally { setBusy(false); }
      }}>
        <label>Email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} maxLength={254} required /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} maxLength={128} required /></label>
        <Notice error={error} /><button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="muted">Use your personal account. Ask your administrator if you need access.</p>
    </div>
  );
  if (!session.data.local_access) return <main className="login" style={{ maxWidth: "1000px" }}>
    <h1>Waker</h1><p>Signed in as {session.data.user?.email}. Your applications are listed below.</p>
    <button onClick={() => { void api("/session", "DELETE").then(refreshData).catch(() => refreshData()); }}>Sign out</button>
    <Suspense fallback={<p>Loading applications…</p>}><Access role={session.data.user?.role ?? "viewer"} /></Suspense>
  </main>;
  return <Workspace session={session.data} />;
}
export default function App() {
  return (
    <BrowserRouter>
      <Session />
    </BrowserRouter>
  );
}
