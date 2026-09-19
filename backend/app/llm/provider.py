import json
import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app.core.safety import redact
from app.core.schemas import AgentDecision

DECISION_CONTRACT = """Call investigation_decision with a complete JSON object.
The top-level fields kind, hypothesis, and reason are mandatory on EVERY call.
For a diagnostic tool choice, kind MUST be "tool", tool MUST name a permitted tool,
arguments contains that tool's parameters, and diagnosis must be null or omitted.
Example shape (substitute a permitted tool):
{"kind":"tool","hypothesis":"Check the task failure","reason":"Read its observed exception","tool":"get_task_details","arguments":{},"diagnosis":null}
For a final diagnosis, kind MUST be "diagnosis", tool must be null or omitted,
and diagnosis must contain the complete DiagnosisOutput object required by the schema.
Do not put kind inside arguments or diagnosis. Do not omit kind.
If no diagnostic tools are permitted, submit a diagnosis; do not copy the tool example.
Evidence references must come from the supplied evidence. Never invent observations."""


STRUCTURED_CONTRACT = """Return one JSON decision matching the response schema.
Include every required field, including nullable fields.
For kind=tool, select a permitted tool and set diagnosis=null.
For kind=diagnosis, set tool=null and provide all diagnosis fields:
root_cause, summary, affected_component, confidence, supporting_evidence,
contradicting_evidence, alternative_hypotheses, recommended_action.
Use null for unused task_id, worker, path and query arguments;
use start_line=1 and limit=20 when unused.
If no tools are permitted, return a diagnosis.
Reference only supplied evidence IDs. Never invent observations."""


def strict_decision_schema(allowed_tools):
    """Close every wire object; retain richer Pydantic validation locally."""
    schema = AgentDecision.model_json_schema()

    def close(node):
        if isinstance(node, list):
            for item in node:
                close(item)
        elif isinstance(node, dict):
            for key in (
                "default",
                "title",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
                "minimum",
                "maximum",
            ):
                node.pop(key, None)

            for value in node.values():
                close(value)

            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))

    close(schema)

    schema["properties"]["tool"] = (
        {
            "anyOf": [
                {"type": "string", "enum": list(allowed_tools)},
                {"type": "null"},
            ]
        }
        if allowed_tools
        else {"type": "null"}
    )

    if not allowed_tools:
        schema["properties"]["kind"]["enum"] = ["diagnosis"]

    return schema


def provider_error(response, key):
    """Expose bounded error details, never failed_generation or credentials."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}

    code = str(error.get("code") or error.get("type") or "request_rejected")
    message = str(
        error.get("message") or "Provider returned no structured error message"
    )

    if key:
        code = code.replace(key, "[REDACTED]")
        message = message.replace(key, "[REDACTED]")

    return (
        code,
        f"HTTP {response.status_code} "
        f"({redact(code)[:80]}): {redact(message)[:600]}",
    )


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class UsageEvent:
    input_tokens: int | None
    output_tokens: int | None
    latency: float
    status: str


@dataclass
class Completion:
    decision: AgentDecision
    input_tokens: int | None
    output_tokens: int | None
    latency: float


class ModelProvider:
    def __init__(self, cfg, on_usage=None):
        self.cfg = cfg
        self.on_usage = on_usage

    def report(self, tin, tout, start, status):
        if self.on_usage:
            self.on_usage(
                UsageEvent(tin, tout, time.monotonic() - start, status)
            )

    def complete(self, messages, allowed_tools):
        cfg = self.cfg

        if not cfg.llm_configured:
            raise ProviderUnavailable("LLM provider not configured")

        strict_json = cfg.llm_provider == "groq" and cfg.model_name in {
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
        }

        schema = (
            strict_decision_schema(allowed_tools)
            if strict_json
            else AgentDecision.model_json_schema()
        )
        contract = STRUCTURED_CONTRACT if strict_json else DECISION_CONTRACT

        tool = {
            "type": "function",
            "function": {
                "name": "investigation_decision",
                "description": (
                    "Return a complete decision including mandatory kind, "
                    "hypothesis, and reason. "
                    'Set kind="tool" for a permitted diagnostic tool, '
                    'or kind="diagnosis" for an evidence-linked final diagnosis. '
                    "Never omit kind."
                ),
                "parameters": schema,
            },
        }

        context = list(messages) + [
            {
                "role": "system",
                "content": (
                    contract
                    + "\nPermitted diagnostic tools: "
                    + json.dumps(allowed_tools)
                ),
            }
        ]

        if cfg.llm_provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            key = cfg.groq_api_key.get_secret_value()
        elif cfg.llm_provider == "ollama":
            url = cfg.ollama_base_url.rstrip("/") + "/api/chat"
            key = ""
        else:
            url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
            key = cfg.llm_api_key.get_secret_value()

        started = time.monotonic()
        total_in = total_out = 0
        known = True
        last_validation_error = ""
        rate_limit_wait = 0.0
        json_fallback = False

        with httpx.Client(
            timeout=cfg.llm_timeout_seconds,
            follow_redirects=False,
        ) as client:
            for _attempt in range(2):
                body = {
                    "model": cfg.model_name,
                    "messages": context,
                }

                if strict_json:
                    body.update(
                        temperature=cfg.llm_temperature,
                        max_tokens=1800,
                        response_format=(
                            {"type": "json_object"}
                            if json_fallback
                            else {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": "investigation_decision",
                                    "strict": True,
                                    "schema": schema,
                                },
                            }
                        ),
                    )
                elif cfg.llm_provider == "ollama":
                    body.update(
                        stream=False,
                        format=schema,
                        options={
                            "temperature": cfg.llm_temperature,
                            "num_predict": 1800,
                        },
                    )
                else:
                    body.update(
                        temperature=cfg.llm_temperature,
                        tools=[tool],
                        tool_choice={
                            "type": "function",
                            "function": {"name": "investigation_decision"},
                        },
                        max_tokens=1800,
                    )

                for rate_attempt in range(4):
                    request_start = time.monotonic()

                    try:
                        response = client.post(
                            url,
                            json=body,
                            headers=(
                                {"Authorization": "Bearer " + key}
                                if key
                                else {}
                            ),
                        )
                    except httpx.HTTPError as exc:
                        self.report(
                            None, None, request_start, "REQUEST_FAILED"
                        )
                        raise ProviderUnavailable(
                            "LLM request failed: " + type(exc).__name__
                        ) from exc

                    if (
                        cfg.llm_provider != "groq"
                        or response.status_code != 429
                    ):
                        break

                    known = False
                    self.report(None, None, request_start, "RATE_LIMITED")
                    _, detail = provider_error(response, key)

                    try:
                        retry_after = float(
                            response.headers.get("retry-after", "30")
                        )
                    except (TypeError, ValueError) as exc:
                        raise ProviderUnavailable(detail) from exc

                    # Honor the provider's delay without unbounded waiting.
                    if not 0 <= retry_after <= 59:
                        raise ProviderUnavailable(detail)

                    delay = retry_after + 1.0

                    if rate_attempt == 3 or rate_limit_wait + delay > 90:
                        raise ProviderUnavailable(detail)

                    time.sleep(delay)
                    rate_limit_wait += delay

                if not response.is_success:
                    code, detail = provider_error(response, key)

                    schema_rejected = strict_json and code in {
                        "json_validate_failed",
                        "output_parse_failed",
                    }
                    repairable = response.status_code == 400 and (
                        code == "tool_use_failed" or schema_rejected
                    )

                    self.report(
                        None,
                        None,
                        request_start,
                        "INVALID_OUTPUT" if repairable else "REQUEST_FAILED",
                    )

                    if not repairable:
                        raise ProviderUnavailable(detail)

                    # Generate a fresh response, never execute failed_generation.
                    # JSON-mode responses still require local validation.
                    if schema_rejected:
                        json_fallback = True
                        contract = (
                            "Return one JSON object, not a function-call wrapper. "
                            "Match this schema. Fields with defaults may be omitted; "
                            "all diagnosis fields remain required. Use only supplied "
                            "evidence and permitted tools.\n"
                            + json.dumps(
                                AgentDecision.model_json_schema(),
                                separators=(",", ":"),
                            )
                        )

                    known = False
                    last_validation_error = detail

                    context.append(
                        {
                            "role": "system",
                            "content": (
                                "The previous response failed validation. "
                                "Generate a fresh complete object.\n" + contract
                            ),
                        }
                    )
                    continue

                try:
                    data = response.json()
                except ValueError as exc:
                    self.report(None, None, request_start, "REQUEST_FAILED")
                    raise ProviderUnavailable(
                        "Provider returned invalid JSON"
                    ) from exc

                if not isinstance(data, dict):
                    self.report(None, None, request_start, "REQUEST_FAILED")
                    raise ProviderUnavailable(
                        "Provider returned an invalid response object"
                    )

                tin = tout = None

                try:
                    if cfg.llm_provider == "ollama":
                        raw = data.get("message", {}).get("content", "")
                        tin = data.get("prompt_eval_count")
                        tout = data.get("eval_count")
                    else:
                        usage = data.get("usage", {})
                        tin = usage.get("prompt_tokens")
                        tout = usage.get("completion_tokens")
                        message = (data.get("choices") or [{}])[0].get(
                            "message", {}
                        )

                        if strict_json:
                            choice = data["choices"][0]

                            if (
                                message.get("refusal")
                                or choice.get("finish_reason") == "length"
                            ):
                                raise ValueError(
                                    "Refused or incomplete structured response"
                                )

                            if message.get("tool_calls"):
                                raise ValueError("Unexpected native tool call")

                            raw = message.get("content", "")
                        else:
                            calls = message.get("tool_calls") or []

                            if (
                                len(calls) != 1
                                or calls[0].get("function", {}).get("name")
                                != "investigation_decision"
                            ):
                                raise ValueError("Unexpected function")

                            raw = calls[0]["function"].get("arguments", "")

                    known &= tin is not None and tout is not None
                    total_in += tin or 0
                    total_out += tout or 0

                    decision = AgentDecision.model_validate_json(raw)

                    if (
                        decision.kind == "tool"
                        and decision.tool not in allowed_tools
                    ):
                        raise ValueError("Tool not permitted")

                except (
                    ValidationError,
                    ValueError,
                    TypeError,
                    KeyError,
                    IndexError,
                    AttributeError,
                ):
                    known = False
                    self.report(tin, tout, request_start, "INVALID_OUTPUT")

                    context.append(
                        {
                            "role": "system",
                            "content": (
                                "Invalid output. Use only permitted tools.\n"
                                + contract
                            ),
                        }
                    )
                    continue

                self.report(tin, tout, request_start, "SUCCEEDED")

                return Completion(
                    decision,
                    total_in if known else None,
                    total_out if known else None,
                    time.monotonic() - started,
                )

        suffix = ": " + last_validation_error if last_validation_error else ""
        raise ProviderUnavailable(
            "Model output failed validation after two attempts" + suffix
        )