"""Readiness + real queued-task smoke test for a running Docker stack. Standard library only."""

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            args.base + "/api" + path, data=data, headers={"Content-Type": "application/json"} if data else {}
        )
        with opener.open(req, timeout=15) as response:
            return json.load(response)

    if os.getenv("AUTOPILOT_APP_TOKEN"):
        request("/session", {"token": os.environ["AUTOPILOT_APP_TOKEN"]})
    config = request("/settings")
    assert config["remediation_mode"] == "dry_run", "Smoke run expects dry_run"
    workers = request("/workers")
    assert any(w["name"] == "default@autopilot" and w["status"] == "ONLINE" for w in workers), (
        "Default worker is not online"
    )
    tasks = [
        request(
            "/tasks", {"name": name, "idempotency_key": "smoke-" + str(uuid.uuid4()), "count": 10, "size": 64}
        )
        for name in [
            "process_report",
            "resize_image",
            "process_database_record",
            "data_processing_task",
            "call_external_api",
        ]
    ]
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        rows = [request("/tasks/" + task["id"]) for task in tasks]
        if all(r["task"]["status"] == "SUCCEEDED" for r in rows):
            break
        if any(r["task"]["status"] in {"FAILED", "UNCERTAIN", "QUARANTINED"} for r in rows):
            raise RuntimeError("A smoke workload failed; inspect Tasks for evidence")
        time.sleep(2)
    else:
        raise TimeoutError("Queued tasks did not finish before timeout")
    assert all(any(log["message"] == "Execution succeeded" for log in r["logs"]) for r in rows)
    print(
        json.dumps(
            {
                "status": "PASS",
                "real_queued_tasks": len(rows),
                "task_ids": [r["task"]["id"] for r in rows],
                "scope": "Live backend, database, broker, worker, test dependency and artifact operations",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
