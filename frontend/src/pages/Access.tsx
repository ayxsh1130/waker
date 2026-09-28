import { useState } from "react";
import { Notice, PageTitle, Panel } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, refreshData } from "../services/api";

type Account = { id: string; email: string; role: string; active: boolean };
type Application = { id: string; name: string; kind: string; active: boolean };
type Credential = { id: string; name: string; scopes: string[]; expires_at: string; revoked_at: string | null; last_seen_at: string | null };

function Password() {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const action = useAction();
  return <Panel title="Change your password"><form className="form-grid" onSubmit={e => {
    e.preventDefault();
    void action.run(async () => { await api("/session/password", "POST", { current_password: current, new_password: password }); setCurrent(""); setPassword(""); refreshData(); }, "Password changed. Sign in again.");
  }}>
    <label>Current password<input type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} required maxLength={128} /></label>
    <label>New password<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} required minLength={15} maxLength={128} /></label>
    <button disabled={action.busy}>Change password and sign out</button>
    <Notice error={action.error} message={action.message} />
  </form></Panel>;
}

function AccountRow({ account }: { account: Account }) {
  const action = useAction();
  return <tr><td>{account.email}</td><td>
    <select aria-label={`Role for ${account.email}`} value={account.role} disabled={action.busy} onChange={e => { void action.run(() => api(`/accounts/${account.id}`, "POST", { role: e.target.value, active: account.active }), "Role updated; existing sessions revoked."); }}>
      <option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Administrator</option>
    </select>
  </td><td>{account.active ? "Active" : "Disabled"}</td><td>
    <button disabled={action.busy} onClick={() => { void action.run(() => api(`/accounts/${account.id}`, "POST", { role: account.role, active: !account.active }), "Account updated."); }}>{account.active ? "Disable" : "Enable"}</button>
    <button disabled={action.busy} onClick={() => { void action.run(() => api(`/accounts/${account.id}/revoke-sessions`, "POST"), "All sessions revoked."); }}>Revoke sessions</button>
    <Notice error={action.error} message={action.message} />
  </td></tr>;
}

function Accounts({ applications }: { applications: Application[] }) {
  const accounts = useApi<Account[]>("/accounts");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [selected, setSelected] = useState<string[]>([]);
  const action = useAction();
  return <Panel title="People">
    <p>Viewers can read assigned workloads. Operators can investigate and approve permitted actions. Administrators manage people, applications and fault experiments.</p>
    <Notice error={accounts.error} />
    <div className="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{accounts.data?.map(a => <AccountRow key={a.id} account={a} />)}</tbody></table></div>
    <form className="form-grid" onSubmit={e => { e.preventDefault(); void action.run(async () => {
      await api("/accounts", "POST", { email, password, role, application_ids: selected }); setPassword(""); setEmail(""); setSelected([]);
    }, "Account created. Share the initial password through a secure channel."); }}>
      <label>Email<input type="email" value={email} onChange={e => setEmail(e.target.value)} required maxLength={254} /></label>
      <label>Initial password<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} required minLength={15} maxLength={128} /></label>
      <label>Role<select value={role} onChange={e => setRole(e.target.value)}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Administrator</option></select></label>
      <fieldset><legend>Application access (administrators can access all)</legend>{applications.map(app => <label key={app.id}><input type="checkbox" checked={selected.includes(app.id)} onChange={e => setSelected(e.target.checked ? [...selected, app.id] : selected.filter(id => id !== app.id))} /> {app.name}</label>)}</fieldset>
      <button disabled={action.busy}>Create account</button><Notice error={action.error} message={action.message} />
    </form>
  </Panel>;
}

function ApplicationControls({ application }: { application: Application }) {
  const accounts = useApi<Account[]>("/accounts");
  const members = useApi<string[]>(`/applications/${application.id}/members`);
  const credentials = useApi<Credential[]>(`/applications/${application.id}/credentials`);
  const action = useAction();
  const [token, setToken] = useState("");
  const [name, setName] = useState("Connector");
  return <details><summary>Manage access and credentials</summary>
    <Notice error={accounts.error || members.error || credentials.error || action.error} message={action.message} />
    <p>Application ID: <code>{application.id}</code></p>
    <fieldset disabled={action.busy || !members.data}><legend>Assigned people</legend>
      {accounts.data?.filter(a => a.role !== "admin").map(account => <label key={account.id}><input type="checkbox" checked={members.data?.includes(account.id) ?? false} onChange={e => {
        const next = e.target.checked ? [...(members.data ?? []), account.id] : (members.data ?? []).filter(id => id !== account.id);
        void action.run(() => api(`/applications/${application.id}/members`, "POST", { account_ids: next }), "Access updated.");
      }} /> {account.email} ({account.role})</label>)}
    </fieldset>
    {application.kind !== "local" && <>
      <p>Connector credentials are application-scoped and can authorize workload event ingestion.</p>
      <form className="form-grid" onSubmit={e => { e.preventDefault(); void action.run(async () => {
        const result = await api<{ token: string }>(`/applications/${application.id}/credentials`, "POST", { name, scopes: ["events:write"], expires_in_days: 30 }); setToken(result.token);
      }, "Credential created with a 30-day expiry."); }}>
        <label>Credential name<input value={name} onChange={e => setName(e.target.value)} maxLength={100} required /></label>
        <button disabled={action.busy}>Create connector credential</button>
      </form>
      {token && <div role="status"><p>Copy this credential now. It is shown only once.</p><pre className="json">{token}</pre><button onClick={() => setToken("")}>Hide credential</button></div>}
      <div className="table-wrap"><table><thead><tr><th>Credential</th><th>Expires</th><th>Last contact</th><th>Action</th></tr></thead><tbody>{credentials.data?.map(c => <tr key={c.id}><td>{c.name}</td><td>{new Date(c.expires_at).toLocaleString()}</td><td>{c.last_seen_at ? new Date(c.last_seen_at).toLocaleString() : "No contact"}</td><td>{c.revoked_at ? "Revoked" : <button disabled={action.busy} onClick={() => { setToken(""); void action.run(() => api(`/applications/${application.id}/credentials/${c.id}`, "DELETE"), "Credential revoked."); }}>Revoke</button>}</td></tr>)}</tbody></table></div>
    </>}
  </details>;
}

export default function Access({ role }: { role: string }) {
  const applications = useApi<Application[]>("/applications");
  const [name, setName] = useState("");
  const action = useAction();
  return <div className="access-page">
    <PageTitle title="Accounts & applications" description="Manage sign-in and access to your workloads." />
    <Notice error={applications.error} />
    <Panel title="Applications">
      {applications.data?.map(app => <section key={app.id} style={{ padding: "16px 0" }}><h3>{app.name}</h3><p>{app.kind === "local" ? "Local workload · operational" : "Registered · workload connection pending"}</p>{role === "admin" && <ApplicationControls application={app} />}</section>)}
      {applications.data?.length === 0 && <p>No applications assigned. Ask your administrator for access.</p>}
      {role === "admin" && <form className="form-grid" onSubmit={e => { e.preventDefault(); void action.run(async () => { await api("/applications", "POST", { name }); setName(""); }, "Application registered."); }}>
        <label>Application name<input value={name} onChange={e => setName(e.target.value)} required maxLength={100} /></label><button disabled={action.busy}>Register application</button><Notice error={action.error} message={action.message} />
      </form>}
    </Panel>
    {role === "admin" && <Accounts applications={applications.data ?? []} />}
    <Password />
  </div>;
}
