import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  GitBranch,
  Radio,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Workflow,
} from "lucide-react";

const features = [
  {
    icon: Radio,
    title: "Detect",
    body: "Correlated operational signals from task failures, worker crashes, and dependency errors are grouped into a single incident automatically.",
  },
  {
    icon: GitBranch,
    title: "Investigate",
    body: "An agent gathers evidence — logs, metrics, historical incidents — and records a transparent, step-by-step hypothesis trail.",
  },
  {
    icon: Sparkles,
    title: "Diagnose",
    body: "A root cause, a confidence score, and a recommended action, backed by the specific evidence that supports or contradicts it.",
  },
  {
    icon: ShieldCheck,
    title: "Verify",
    body: "Deterministic checks confirm or reject the diagnosis before anything is allowed near a remediation action.",
  },
  {
    icon: Workflow,
    title: "Decide",
    body: "Policy evaluates the verified diagnosis against risk rules and chooses dry-run, automatic execution, or a human approval.",
  },
  {
    icon: UserCheck,
    title: "Approve",
    body: "Medium-risk actions wait for a one-use, expiring human decision. Nothing executes silently in production.",
  },
];

const steps = [
  {
    title: "A signal comes in",
    body: "A task fails, a worker drops its heartbeat, or a dependency starts erroring. Waker correlates the occurrence into an incident with a component and a severity.",
  },
  {
    title: "Evidence gets collected",
    body: "Logs, metrics, and similar historical incidents are pulled together into an evidence set the investigation can reason over.",
  },
  {
    title: "A diagnosis is proposed",
    body: "Root cause, confidence, and a recommended action — with the supporting and contradicting evidence laid out, not hidden in a black box.",
  },
  {
    title: "The diagnosis is verified",
    body: "A separate, deterministic pass checks the diagnosis against reality before it can authorize anything.",
  },
  {
    title: "Remediation is proposed, not assumed",
    body: "Policy decides dry-run, auto-execute, or approval-required based on risk. Evidence before action, always.",
  },
];

export default function Home() {
  return (
    <div className="home">
      <nav className="home-nav">
        <Link className="brand" to="/">
          <span className="brand-icon">
            <Activity size={24} />
          </span>
          <div>
            Waker<small>Operations intelligence</small>
          </div>
        </Link>
        <div className="home-nav-links">
          <a href="#pipeline">Pipeline</a>
          <a href="#how-it-works">How it works</a>
          <Link className="button primary" to="/overview">
            Open dashboard
          </Link>
        </div>
      </nav>

      <header className="home-hero">
        <div>
          <span className="home-eyebrow">
            <Sparkles size={12} /> Operations intelligence, running locally
          </span>
          <h1>
            Turn a failing task into a <em>verified</em> fix.
          </h1>
          <p className="lead">
            Waker watches your task queues, correlates failures into
            incidents, investigates root cause with evidence-backed
            hypotheses, and only proposes a remediation once it has been
            verified. Humans approve anything risky.
          </p>
          <div className="home-cta-row">
            <Link className="button primary" to="/overview">
              Open the dashboard <ArrowRight size={15} />
            </Link>
            <a className="button" href="#pipeline">
              See the pipeline
            </a>
          </div>
          <p className="home-cta-note">
            Runs entirely on your machine · dry-run by default · nothing ships
            without approval
          </p>
        </div>
        <div className="home-mock" aria-hidden="true">
          <div className="home-mock-bar">
            <span />
            <span />
            <span />
          </div>
          <div className="home-mock-stats">
            <div>
              <span>Active incidents</span>
              <strong>2</strong>
            </div>
            <div>
              <span>Throughput</span>
              <strong>41/min</strong>
            </div>
            <div>
              <span>Verified</span>
              <strong>92%</strong>
            </div>
          </div>
          <div className="home-mock-row">
            <span className="home-mock-dot bad" />
            <strong>Queue depth spike · payments</strong>
            <span className="tag">investigating</span>
          </div>
          <div className="home-mock-row">
            <span className="home-mock-dot neutral" />
            <strong>Worker heartbeat lost · worker-3</strong>
            <span className="tag">diagnosed</span>
          </div>
          <div className="home-mock-row">
            <span className="home-mock-dot good" />
            <strong>API timeout · resize_image</strong>
            <span className="tag">verified</span>
          </div>
        </div>
      </header>

      <div className="home-trust">
        <div>
          <CheckCircle2 size={15} /> Self-hosted, no data leaves your
          environment
        </div>
        <div>
          <CheckCircle2 size={15} /> Evidence-based diagnosis, not a black box
        </div>
        <div>
          <CheckCircle2 size={15} /> Deterministic verification before any
          action
        </div>
        <div>
          <CheckCircle2 size={15} /> Dry-run by default; execute mode is
          opt-in
        </div>
      </div>

      <section className="home-section" id="pipeline">
        <div className="home-section-head">
          <span className="eyebrow">The pipeline</span>
          <h2>Six stages, one lifecycle.</h2>
          <p>
            Every incident moves through the same evidence-first pipeline —
            visible end-to-end in the dashboard, not buried in logs.
          </p>
        </div>
        <div className="home-feature-grid">
          {features.map((f) => (
            <div className="home-feature" key={f.title}>
              <div className="home-feature-icon">
                <f.icon size={19} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="home-section" id="how-it-works">
        <div className="home-section-head">
          <span className="eyebrow">How it works</span>
          <h2>From first symptom to a verified fix.</h2>
        </div>
        <div className="home-steps">
          {steps.map((s, i) => (
            <div className="home-step" key={s.title}>
              <span className="home-step-number">{i + 1}</span>
              <div>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="home-banner">
        <div>
          <h2>Ready to see it live?</h2>
          <p>
            Open the dashboard to watch incidents move through detection,
            investigation, diagnosis, verification, and remediation in real
            time.
          </p>
        </div>
        <Link className="button primary" to="/overview">
          Open the dashboard <ArrowRight size={15} />
        </Link>
      </div>

      <footer className="home-footer">
        <span>Waker · Local research prototype · v1.0</span>
        <span>Evidence before action.</span>
      </footer>
    </div>
  );
}
