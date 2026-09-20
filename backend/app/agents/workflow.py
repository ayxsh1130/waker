import json
from datetime import UTC
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.prompts import SYSTEM_V1
from app.agents.rules import diagnose_rule
from app.core.config import settings
from app.core.safety import redact
from app.core.schemas import AgentDecision, DiagnosisOutput, ToolArguments
from app.db.base import now, uid
from app.db.models import (
    Diagnosis,
    DiagnosisEvidence,
    Evidence,
    Investigation,
    InvestigationStep,
    LLMUsage,
    Verification,
)
from app.investigation.verification import verify_diagnosis
from app.llm.provider import ModelProvider, ProviderUnavailable
from app.observability.events import incident_event
from app.tools.registry import DYNAMIC_CONFIGS, DiagnosticTools, incident_projection


class State(TypedDict):
    steps: int
    calls: int
    loops: int
    decision: dict | None
    diagnosis: dict | None
    feedback: list[str]
    done: bool


def evidence_view(e):
    return {
        "id": e.id,
        "kind": e.kind,
        "content": e.content,
        "available": e.available,
        "component": e.component,
        "timestamp": e.created_at.replace(tzinfo=UTC).isoformat(),
    }


class InvestigationRunner:
    def __init__(self, db, incident, investigation, provider=None, history_ids=None):
        self.db, self.incident, self.investigation = db, incident, investigation
        self.cfg = settings()
        self.provider = provider or ModelProvider(self.cfg, on_usage=self.record_usage)
        self.tools = DiagnosticTools(db, incident, investigation, history_ids)
        self.collected = list(
            db.scalars(
                select(Evidence)
                .where(Evidence.incident_id == incident.id, Evidence.kind == "initial_signal")
                .limit(1)
            )
        )
        self.latest = None

    def timeline(self, hypothesis, reason, tool, parameters, observation, decision, evidence_ids=None):
        number = (
            self.db.scalar(
                select(InvestigationStep.number)
                .where(InvestigationStep.investigation_id == self.investigation.id)
                .order_by(InvestigationStep.number.desc())
                .limit(1)
            )
            or 0
        ) + 1
        self.db.add(
            InvestigationStep(
                investigation_id=self.investigation.id,
                number=number,
                hypothesis=redact(hypothesis),
                reason=redact(reason),
                tool=tool,
                parameters=redact(parameters),
                observation=redact(observation),
                decision=decision,
                evidence_ids=evidence_ids or [],
            )
        )
        incident_event(
            self.db, self.incident.id, "Investigation", {"step": number, "tool": tool, "decision": decision}
        )
        self.db.commit()

    def record_usage(self, completion):
        cfg = self.cfg
        cost = None
        if (
            completion.input_tokens is not None
            and completion.output_tokens is not None
            and cfg.llm_input_cost_per_million is not None
            and cfg.llm_output_cost_per_million is not None
        ):
            cost = (
                completion.input_tokens * cfg.llm_input_cost_per_million
                + completion.output_tokens * cfg.llm_output_cost_per_million
            ) / 1000000
        self.db.add(
            LLMUsage(
                investigation_id=self.investigation.id,
                provider=cfg.llm_provider,
                model=cfg.model_name,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                estimated_cost=cost,
                latency_seconds=completion.latency,
                status=getattr(completion, "status", "SUCCEEDED"),
            )
        )

    def observe(self, state):
        config = self.investigation.configuration
        if state["steps"] == 0 and config == "LLM_RAG":
            e = self.tools.run("search_historical_incidents", ToolArguments(), internal_baseline_rag=True)
            self.collected.append(e)
            self.timeline(
                "Past incidents may provide context",
                "Fixed retrieval for LLM_RAG baseline",
                "search_historical_incidents",
                {},
                e.content,
                "OBSERVATION",
                [e.id],
            )
        allowed = self.tools.allowed if config in DYNAMIC_CONFIGS else {}
        if state["calls"] >= self.cfg.agent_max_tool_calls or state["steps"] >= self.cfg.agent_max_steps - 1:
            allowed = {}
        context = {
            "incident": incident_projection(self.incident),
            "evidence": [evidence_view(e) for e in self.collected],
            "verification_feedback": state["feedback"],
            "remaining_tool_calls": self.cfg.agent_max_tool_calls - state["calls"],
        }
        if config == "FULL_SYSTEM":
            context["verification_requirements"] = (
                "Cite at least two available current evidence source types. "
                "API_TIMEOUT requires timeout evidence from task details/logs and "
                "get_external_service_status. Collect missing checks when tools are "
                "available; otherwise reassess only the evidence already collected. "
                "Use Evidence IDs, not IDs inside task/log content. Do not invent support."
                " WORKER_FAILURE requires the incident worker OFFLINE and a HEALTHY broker in "
                "fresh get_worker_status and get_redis_status checks; cite both IDs. "
                "An old heartbeat alert does not show current worker failure."
            )
        if state["diagnosis"]:
            context["previous_diagnosis"] = {
                key: state["diagnosis"][key]
                for key in ("root_cause", "supporting_evidence", "contradicting_evidence")
            }
        # Keep every collected evidence ID and source visible. Large traces used
        # to evict entire earlier observations, making multi-source citations
        # impossible even after verification feedback.
        originals = [evidence_view(e) for e in self.collected]
        content_limit = self.cfg.agent_max_context_chars
        while len(json.dumps(context, default=str)) > self.cfg.agent_max_context_chars:
            content_limit //= 2
            if content_limit < 64:
                raise ProviderUnavailable("Evidence context exceeds configured budget")
            context["incident"]["symptoms"] = str(context["incident"]["symptoms"])[:1000]
            context["evidence"] = []
            for original in originals:
                view = dict(original)
                content = json.dumps(original["content"], default=str)
                if len(content) > content_limit:
                    view["content"] = {
                        "truncated": True,
                        "excerpt": content[:content_limit],
                    }
                context["evidence"].append(view)
        completion = self.provider.complete(
            [
                {"role": "system", "content": SYSTEM_V1},
                {"role": "user", "content": json.dumps(context, default=str)},
            ],
            allowed,
        )
        if not isinstance(self.provider, ModelProvider):
            self.record_usage(completion)
        decision = completion.decision
        if decision.kind == "tool" and decision.tool not in allowed:
            raise ProviderUnavailable("Tool outside baseline or budget")
        return {**state, "steps": state["steps"] + 1, "decision": decision.model_dump(mode="json")}

    def collect(self, state):
        decision = AgentDecision.model_validate(state["decision"])
        e = self.tools.run(decision.tool, decision.arguments)
        self.collected.append(e)
        self.timeline(
            decision.hypothesis,
            decision.reason,
            decision.tool,
            decision.arguments.model_dump(exclude_none=True),
            e.content,
            "OBSERVATION",
            [e.id],
        )
        return {**state, "calls": state["calls"] + 1}

    def diagnose(self, state):
        output = AgentDecision.model_validate(state["decision"]).diagnosis
        valid_ids = {e.id for e in self.collected}
        if (set(output.supporting_evidence) | set(output.contradicting_evidence)) - valid_ids:
            raise ValueError("Diagnosis references uncollected evidence")
        diagnosis = Diagnosis(investigation_id=self.investigation.id, **output.model_dump(mode="json"))
        self.db.add(diagnosis)
        self.db.flush()
        for role, ids in [
            ("supporting", output.supporting_evidence),
            ("contradicting", output.contradicting_evidence),
        ]:
            for eid in set(ids):
                self.db.add(DiagnosisEvidence(diagnosis_id=diagnosis.id, evidence_id=eid, role=role))
        self.latest = diagnosis
        self.incident.status = "DIAGNOSED"
        incident_event(
            self.db,
            self.incident.id,
            "Diagnosis",
            {"diagnosis_id": diagnosis.id, "root_cause": output.root_cause},
        )
        self.db.commit()
        return {**state, "diagnosis": output.model_dump(mode="json")}

    def verify(self, state):
        result = verify_diagnosis(
            DiagnosisOutput.model_validate(state["diagnosis"]), [evidence_view(e) for e in self.collected]
        )
        self.db.add(Verification(diagnosis_id=self.latest.id, **result.model_dump()))
        self.incident.status = "VERIFIED" if result.verified else "UNVERIFIED"
        incident_event(self.db, self.incident.id, "Verification", result.model_dump())
        self.db.commit()
        retry = (
            not result.verified
            and state["loops"] < self.cfg.agent_max_reinvestigations
            and state["steps"] < self.cfg.agent_max_steps
        )
        return {
            **state,
            "loops": state["loops"] + 1,
            "feedback": result.missing_evidence + result.contradictions,
            "done": not retry,
        }

    def graph(self):
        graph = StateGraph(State)
        for name, node in [
            ("investigation", self.observe),
            ("evidence_collection", self.collect),
            ("diagnosis", self.diagnose),
            ("verification", self.verify),
        ]:
            graph.add_node(name, node)
        graph.add_edge(START, "investigation")
        graph.add_conditional_edges(
            "investigation",
            lambda s: "evidence_collection" if s["decision"]["kind"] == "tool" else "diagnosis",
        )
        graph.add_edge("evidence_collection", "investigation")
        if self.investigation.configuration == "FULL_SYSTEM":
            graph.add_edge("diagnosis", "verification")
            graph.add_conditional_edges("verification", lambda s: END if s["done"] else "investigation")
        else:
            graph.add_edge("diagnosis", END)
        return graph.compile()

    def rules(self):
        names = [
            "get_task_details",
            "get_task_logs",
            "get_related_tasks",
            "get_worker_status",
            "get_redis_status",
            "get_queue_metrics",
            "get_database_status",
            "get_external_service_status",
        ]
        for name in names:
            e = self.tools.run(name, ToolArguments())
            self.collected.append(e)
            self.timeline(
                "Evaluate known operational signatures",
                "Fixed rule-based evidence pass",
                name,
                {},
                e.content,
                "OBSERVATION",
                [e.id],
            )
        output = diagnose_rule([evidence_view(e) for e in self.collected])
        self.diagnose(
            {
                "decision": AgentDecision(
                    kind="diagnosis",
                    hypothesis="Rule-based classification",
                    reason="No LLM used",
                    diagnosis=output,
                ).model_dump(mode="json")
            }
        )

    def run(self):
        self.incident.status = "INVESTIGATING"
        self.db.commit()
        try:
            if self.investigation.configuration == "RULE_BASED":
                self.rules()
            else:
                self.graph().invoke(
                    {
                        "steps": 0,
                        "calls": 0,
                        "loops": 0,
                        "decision": None,
                        "diagnosis": None,
                        "feedback": [],
                        "done": False,
                    },
                    {"recursion_limit": 2 * self.cfg.agent_max_steps + 15},
                )
            self.investigation.status = "COMPLETED"
        except Exception as exc:
            self.investigation.status = "BLOCKED" if isinstance(exc, ProviderUnavailable) else "FAILED"
            self.investigation.error = (
                str(exc)[:250]
                if isinstance(exc, ProviderUnavailable)
                else "Investigation failed: " + type(exc).__name__
            )
            self.incident.status = self.investigation.status
            incident_event(
                self.db,
                self.incident.id,
                "Investigation",
                {"status": self.investigation.status, "error": self.investigation.error},
            )
        self.investigation.completed_at = now()
        self.db.commit()
        return self.latest


def create_investigation(db, incident, configuration, cutoff=None):
    cfg = settings()
    inv = Investigation(
        id=uid(),
        incident_id=incident.id,
        configuration=configuration,
        provider=cfg.llm_provider if configuration != "RULE_BASED" else "none",
        model=cfg.model_name if configuration != "RULE_BASED" else "",
        prompt_version=cfg.agent_prompt_version,
        workflow_version=cfg.workflow_version,
        history_cutoff=cutoff or now(),
    )
    db.add(inv)
    db.flush()
    return inv
