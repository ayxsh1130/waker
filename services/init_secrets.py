import os
import secrets
from pathlib import Path

directory = Path("/run/autopilot")
directory.mkdir(parents=True, exist_ok=True)
path = directory / "control_token"
if not path.exists():
    path.write_text(secrets.token_urlsafe(48))
os.chmod(path, 0o640)
os.chown(path, 10001, 10001)
print("Internal control credential ready")

# A separate volume keeps monitoring credentials apart from control credentials.
metrics_directory = Path("/run/waker-metrics")
metrics_directory.mkdir(parents=True, exist_ok=True)
metrics_path = metrics_directory / "token"
if not metrics_path.exists():
    metrics_path.write_text(secrets.token_urlsafe(48))
os.chmod(metrics_path, 0o444)
print("Metrics credential ready")
