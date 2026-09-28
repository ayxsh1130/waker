# Waker Celery Connector

The connector observes an independently running Celery application's event stream and forwards normalized task/worker events to Waker over HTTPS.

Paperless is the first test target. The connector does not contain Paperless-specific task names.

## Configuration

- `WAKER_URL`: Waker base URL, e.g. `https://waker.example.com`
- `WAKER_APPLICATION_ID`: registered Waker application ID
- `WAKER_CONNECTOR_TOKEN`: scoped credential created for that application
- `CELERY_BROKER_URL`: the target application's broker URL
- `CONNECTOR_BATCH_SIZE`: reserved for future batching; current implementation sends one event at a time

The broker credential is a separate secret from the Waker credential.

## Run

```bash
python main.py
```

The connector only observes the Celery event stream. It does not submit or execute tasks.
