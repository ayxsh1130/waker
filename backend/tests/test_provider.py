import httpx
import pytest

from app.core.config import Settings
from app.llm.provider import ModelProvider, ProviderUnavailable


def cfg(**kwargs):
    return Settings(
        llm_provider="compatible", llm_base_url="http://model.test/v1", llm_model="test-model", **kwargs
    )


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "app.llm.provider.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_actual_request_contract_and_usage(monkeypatch):
    requests = []
    usage = []

    def handler(request):
        import json

        body = json.loads(request.content)
        requests.append(body)
        assert body["tool_choice"]["function"]["name"] == "investigation_decision"
        decision = {
            "kind": "tool",
            "hypothesis": "Check task",
            "reason": "Read observed exception",
            "tool": "get_task_details",
            "arguments": {},
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "investigation_decision",
                                        "arguments": json.dumps(decision),
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    mock_client(monkeypatch, handler)
    result = ModelProvider(cfg(), usage.append).complete([], {"get_task_details": "Read task"})
    assert result.decision.tool == "get_task_details" and result.input_tokens == 11
    assert len(usage) == len(requests) == 1 and usage[0].status == "SUCCEEDED"


def test_invalid_output_retries_are_bounded_and_metered(monkeypatch):
    usage = []
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not a function"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    mock_client(monkeypatch, handler)
    with pytest.raises(ProviderUnavailable, match="two attempts"):
        ModelProvider(cfg(), usage.append).complete([], {})
    assert len(calls) == len(usage) == 2 and all(u.status == "INVALID_OUTPUT" for u in usage)


def test_missing_provider_does_not_make_http_calls(monkeypatch):
    mock_client(monkeypatch, lambda request: pytest.fail("No configured provider must mean no request"))
    with pytest.raises(ProviderUnavailable, match="not configured"):
        ModelProvider(Settings(llm_provider="none")).complete([], {})


def test_http_error_is_redacted_with_unknown_usage(monkeypatch):
    usage = []
    mock_client(monkeypatch, lambda request: httpx.Response(401, json={"secret": "never expose"}))
    with pytest.raises(ProviderUnavailable) as exc:
        ModelProvider(cfg(), usage.append).complete([], {})
    assert "never expose" not in str(exc.value) and usage[0].input_tokens is None


def test_ollama_uses_local_structured_generation(monkeypatch):
    import json

    def handler(request):
        body = json.loads(request.content)
        assert "format" in body and not body["stream"] and request.url.path == "/api/chat"
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "kind": "tool",
                            "hypothesis": "Inspect queue",
                            "reason": "Measure depth",
                            "tool": "get_queue_metrics",
                        }
                    )
                },
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )

    mock_client(monkeypatch, handler)
    result = ModelProvider(Settings(llm_provider="ollama", ollama_base_url="http://local.test")).complete(
        [], {"get_queue_metrics": "Measure queue"}
    )
    assert result.output_tokens == 5
