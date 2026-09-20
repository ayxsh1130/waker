import ast
import json
from pathlib import Path

import pytest

from app.agents.workflow import create_investigation
from app.core.config import settings
from app.core.safety import redact, source_path
from app.core.schemas import DiagnosisOutput, TaskInput, ToolArguments
from app.db.base import now
from app.investigation.verification import verify_diagnosis
from app.tools.registry import DiagnosticTools
from app.workers.publisher import create_task


@pytest.mark.parametrize(
    "configuration,rag,dynamic",
    [
        ("RULE_BASED", False, True),
        ("LLM_ONLY", False, False),
        ("LLM_RAG", False, False),
        ("AGENT_TOOLS", False, True),
        ("AGENT_RAG", True, True),
        ("FULL_SYSTEM", True, True),
    ],
)
def test_baseline_tools_are_actually_removed(db, incident, configuration, rag, dynamic):
    inv = create_investigation(db, incident, configuration)
    tools = DiagnosticTools(db, incident, inv)
    assert ("search_historical_incidents" in tools.allowed) == rag
    assert ("get_task_details" in tools.allowed) == dynamic
    if not dynamic:
        with pytest.raises(ValueError):
            tools.run("get_task_details", ToolArguments())


def test_foreign_scope_task_cannot_be_read(db, incident):
    other = create_task(
        db, TaskInput(name="data_processing_task", idempotency_key="different-000"), scope="other"
    )
    tools = DiagnosticTools(db, incident, create_investigation(db, incident, "FULL_SYSTEM"))
    evidence = tools.run("get_task_details", ToolArguments(task_id=other.id))
    assert not evidence.available and other.id not in json.dumps(evidence.content)


def test_failed_tool_is_explicit_unavailable(db, incident, monkeypatch):
    tools = DiagnosticTools(db, incident, create_investigation(db, incident, "AGENT_TOOLS"))
    monkeypatch.setattr(
        "app.tools.probes.queue_metrics", lambda: (_ for _ in ()).throw(TimeoutError("secret=bad"))
    )
    evidence = tools.run("get_queue_metrics", ToolArguments())
    assert not evidence.available and "bad" not in json.dumps(evidence.content)


def test_source_is_bounded_and_excludes_evaluation(tmp_path):
    root = tmp_path / "source"
    (root / "safe.py").write_text("print(1)")
    (root / "faults").mkdir()
    (root / "faults" / "labels.py").write_text("secret=1")
    outside = tmp_path / "other.py"
    outside.write_text("secret=1")
    (root / "link.py").symlink_to(outside)
    assert source_path(root, "safe.py").name == "safe.py"
    for name in ["../other.py", "faults/labels.py", "link.py", ".env", "safe.py\\other"]:
        with pytest.raises((ValueError, FileNotFoundError)):
            source_path(root, name)


def test_agent_modules_do_not_import_evaluation_models():
    root = Path(__file__).parents[1] / "app"
    for folder in ["agents", "tools", "retrieval", "llm", "investigation"]:
        for path in (root / folder).glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith(("app.faults", "app.experiments"))
                    assert not {"FaultInjection", "ExperimentRun", "ExperimentMetric"} & {
                        n.name for n in node.names
                    }


def output(ids, cause="API_TIMEOUT", component="dependency", contradictions=None):
    return DiagnosisOutput(
        root_cause=cause,
        summary="An observed dependency deadline was exceeded.",
        affected_component=component,
        confidence=0.9,
        supporting_evidence=ids,
        contradicting_evidence=contradictions or [],
        alternative_hypotheses=[],
        recommended_action="RETRY_TASK",
    )


EVIDENCE = [
    {"id": "one", "kind": "get_task_details", "content": {"exception": "ReadTimeout"}, "available": True},
    {
        "id": "two",
        "kind": "get_external_service_status",
        "timestamp": now().isoformat(),
        "content": {"probes": [{"error_type": "ReadTimeout"}]},
        "available": True,
    },
]


def test_verifier_accepts_current_independent_observations():
    assert verify_diagnosis(output(["one", "two"]), EVIDENCE).verified


@pytest.mark.parametrize(
    "diagnosis,evidence",
    [
        (output(["one", "invented"]), EVIDENCE),
        (output(["one"]), EVIDENCE),
        (output(["one", "two"], component="worker"), EVIDENCE),
        (output(["one", "two"], contradictions=["one"]), EVIDENCE),
        (
            output(["history"]),
            [
                {
                    "id": "history",
                    "kind": "search_historical_incidents",
                    "content": {"document": "ReadTimeout"},
                    "available": True,
                }
            ],
        ),
        (output(["one", "two"]), [EVIDENCE[0], {**EVIDENCE[1], "available": False}]),
    ],
)
def test_verifier_rejects_unsupported_diagnosis(diagnosis, evidence):
    assert not verify_diagnosis(diagnosis, evidence).verified


def test_redaction_removes_labels_and_secrets():
    result = redact(
        {
            "ground_truth": "cause",
            "nested": {"fault_type": "cause", "api_key": "sk-secret"},
            "message": "postgresql://user:password@host/db token=unsafe",
        }
    )
    encoded = json.dumps(result)
    assert (
        "cause" not in encoded
        and "sk-secret" not in encoded
        and "unsafe" not in encoded
        and "user:password" not in encoded
    )


def test_source_search_requires_literal_query(tmp_path):
    from app.tools.source import search_source

    root = Path(settings().local_repository_path)
    (root / "main.py").write_text("needle = 1\nnot_a_match = 2")
    result = search_source("needle", 10)
    assert "needle" in json.dumps(result)
    assert "not_a_match" not in json.dumps(result)
