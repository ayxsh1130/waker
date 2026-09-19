import json

from sqlalchemy import select

from app.agents.workflow import InvestigationRunner, create_investigation
from app.core.config import settings
from app.core.schemas import AgentDecision, DiagnosisOutput
from app.db.models import InvestigationStep, LLMUsage, Verification
from app.llm.provider import Completion


class ScriptedProvider:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def complete(self, messages, allowed):
        context = json.loads(messages[1]["content"])
        self.calls.append((context, allowed))
        step = self.script.pop(0)
        if step.startswith("get_"):
            decision = AgentDecision(
                kind="tool",
                hypothesis="Check observed failure",
                reason="Collect an independent operational observation",
                tool=step,
            )
        else:
            diagnosis = DiagnosisOutput(
                root_cause="API_TIMEOUT",
                summary="Dependency calls exceeded the measured HTTP deadline.",
                affected_component="dependency",
                confidence=0.9,
                supporting_evidence=[e["id"] for e in context["evidence"]],
                contradicting_evidence=[],
                alternative_hypotheses=[],
                recommended_action="RETRY_TASK",
            )
            decision = AgentDecision(
                kind="diagnosis",
                hypothesis="Dependency timeout explains the task error",
                reason="Use collected observations",
                diagnosis=diagnosis,
            )
        return Completion(decision, 100, 20, 0.01)


def test_full_system_reinvestigates_after_verification_failure(db, incident, probes):
    inv = create_investigation(db, incident, "FULL_SYSTEM")
    provider = ScriptedProvider(["get_task_details", "diagnosis", "get_external_service_status", "diagnosis"])
    runner = InvestigationRunner(db, incident, inv, provider=provider)
    diagnosis = runner.run()
    verifications = list(db.scalars(select(Verification).order_by(Verification.created_at)))
    assert [v.verified for v in verifications] == [False, True]
    assert diagnosis and inv.status == "COMPLETED" and incident.status == "VERIFIED"
    assert provider.calls[2][0]["verification_feedback"]
    assert len(list(db.scalars(select(InvestigationStep)))) == 2
    assert len(list(db.scalars(select(LLMUsage)))) == 4


def test_agent_baseline_has_no_verification(db, incident, probes):
    inv = create_investigation(db, incident, "AGENT_TOOLS")
    provider = ScriptedProvider(["get_task_details", "diagnosis"])
    InvestigationRunner(db, incident, inv, provider=provider).run()
    assert inv.status == "COMPLETED" and not db.scalar(select(Verification))
    assert "search_historical_incidents" not in provider.calls[0][1]


def test_llm_only_never_gets_tools(db, incident):
    inv = create_investigation(db, incident, "LLM_ONLY")
    provider = ScriptedProvider(["diagnosis"])
    InvestigationRunner(db, incident, inv, provider=provider).run()
    assert provider.calls[0][1] == {} and not db.scalar(select(InvestigationStep))


def test_missing_provider_is_explicitly_blocked(db, incident):
    inv = create_investigation(db, incident, "FULL_SYSTEM")
    runner = InvestigationRunner(db, incident, inv)
    assert runner.run() is None and inv.status == "BLOCKED"
    assert "not configured" in inv.error and not db.scalar(select(LLMUsage))


def test_rule_baseline_uses_fixed_checks_without_llm(db, incident, probes):
    inv = create_investigation(db, incident, "RULE_BASED")
    diagnosis = InvestigationRunner(db, incident, inv).run()
    assert diagnosis.root_cause == "API_TIMEOUT"
    assert len(list(db.scalars(select(InvestigationStep)))) == 8
    assert not db.scalar(select(LLMUsage)) and not db.scalar(select(Verification))


def test_maximum_tool_budget_forces_diagnosis(db, incident, probes, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "1")
    settings.cache_clear()
    inv = create_investigation(db, incident, "AGENT_TOOLS")
    provider = ScriptedProvider(["get_task_details", "diagnosis"])
    InvestigationRunner(db, incident, inv, provider=provider).run()
    assert provider.calls[-1][1] == {}


def test_initial_signal_contains_no_evaluation_labels(db, incident, probes):
    incident.symptoms = {
        "exception": "ReadTimeout",
        "ground_truth": "SECRET_CAUSE",
        "nested": {"fault_type": "SECRET_CAUSE"},
    }
    inv = create_investigation(db, incident, "LLM_ONLY")
    provider = ScriptedProvider(["diagnosis"])
    InvestigationRunner(db, incident, inv, provider=provider).run()
    assert "SECRET_CAUSE" not in json.dumps(provider.calls)
