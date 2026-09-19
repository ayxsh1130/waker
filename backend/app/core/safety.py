import re
from datetime import datetime
from pathlib import Path

SECRET_KEYS = re.compile(r"password|secret|token|authorization|api.?key", re.I)
LABEL_KEYS = re.compile(r"ground.?truth|fault.?type|fault.?params", re.I)
SECRET_TEXT = re.compile(r'(?i)((?:password|token|api[_-]?key|authorization|secret)\s*[=:]\s*)[^\s,;"\']+')
URL_CREDS = re.compile(r"(\w+://)[^\s/@]+:[^\s/@]+@")


def redact(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if SECRET_KEYS.search(str(k)) else redact(v)
            for k, v in value.items()
            if not LABEL_KEYS.search(str(k))
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value[:100]]
    if isinstance(value, str):
        return URL_CREDS.sub(r"\1[REDACTED]@", SECRET_TEXT.sub(r"\1[REDACTED]", value))[:12000]
    return value


def source_path(root, relative):
    if not root or not relative or "\\" in relative or "\x00" in relative:
        raise ValueError("Invalid source path")
    base = Path(root).resolve(strict=True)
    target = (base / relative).resolve(strict=True)
    if not target.is_relative_to(base) or not target.is_file():
        raise ValueError("Source outside repository")
    banned = {"faults", "experiments", "tests", "alembic", "node_modules", "db", "core", "credentials"}
    if any(p.startswith(".") or p in banned for p in target.relative_to(base).parts):
        raise ValueError("Source path not available")
    if (
        target.suffix not in {".py", ".ts", ".tsx", ".js", ".java", ".go", ".rs"}
        or target.stat().st_size > 100000
    ):
        raise ValueError("Only bounded code files are available")
    return target
