# Celery Connector Contract

Status: Phase 2 design / first implementation

The Celery connector is the generic integration boundary between Waker and an independently deployed Celery application. Paperless is the first integration target; Paperless-specific task names, queues, and deployment details must remain outside Waker core.

## Trust boundary

```text
Independent Celery application
        |
        | broker event stream
        v
   Celery Connector
        |
        | HTTPS + scoped bearer credential
        v
   Waker ingestion API
        |
        v
 application-scoped evidence / incidents
```

The connector is an observer in the first milestone. It does not receive remediation authority from Waker.

## Application identity

Every connector installation is bound to exactly one Waker `application_id` and one connector credential. The credential cannot be used for another application.

Required connector scope for event ingestion: `events:write`.

Existing identity/heartbeat scopes remain supported for compatibility during the transition.

## Event envelope

Each event contains:

- `event_id`: connector-generated globally unique identifier; stable across retries.
- `application_id`: Waker application ID. The server verifies it against the credential binding.
- `event_type`: normalized Celery lifecycle or worker observation.
- `occurred_at`: timestamp from the Celery event, not the Waker receive time.
- `received_at`: assigned by Waker on successful ingestion.
- `task_id`: Celery task UUID when applicable; Waker uses the UUID as the task record identity for externally observed Celery tasks.
- `task_name`: native Celery task name when available.
- `worker_id`: Celery worker hostname when available.
- `queue`: queue name when available.
- `correlation_id`: application-provided/connector-observed correlation identifier when available.
- `trace_id`: trace identifier when available.
- `payload`: bounded, redacted event details. Raw credentials and unrestricted task arguments must not be forwarded.

## Supported normalized event types

Task events:

- `task.sent`
- `task.received`
- `task.started`
- `task.succeeded`
- `task.failed`
- `task.retried`
- `task.revoked`

Worker events:

- `worker.online`
- `worker.heartbeat`
- `worker.offline`

The connector may ignore unsupported Celery event types rather than forwarding arbitrary broker messages.

## Ordering and duplication

Celery events may be delayed, duplicated, or observed out of order. Waker must:

1. deduplicate by `event_id`;
2. retain `occurred_at` separately from ingestion time;
3. never treat receipt time as event time;
4. tolerate a later-arriving event with an older timestamp;
5. preserve the underlying event for investigation rather than silently overwriting it.

## Payload limits

Initial connector event body limit: 256 KiB. The connector should send only operational metadata needed for diagnosis. Document contents, credentials, arbitrary task arguments, and large result payloads are out of scope for this contract.

## Actions

The first connector milestone is read-only. No repair endpoint is exposed by this contract.

Future action contracts must bind an approved action to one application, exact target, exact parameters, policy version, and expiring approval before dispatch.
