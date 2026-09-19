import base64
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.safety import redact, source_path


def github_get(path):
    cfg = settings()
    if not cfg.github_token.get_secret_value() or not re.fullmatch(r"[\w.-]+/[\w.-]+", cfg.github_repository):
        return {"available": False, "reason": "GitHub integration is not configured"}
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        response = client.get(
            "https://api.github.com/repos/" + cfg.github_repository + path,
            headers={
                "Authorization": "Bearer " + cfg.github_token.get_secret_value(),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        return response.json()


def recent_commits():
    cfg = settings()
    root = Path(cfg.local_repository_path)
    if cfg.local_repository_path and root.is_dir():
        try:
            result = subprocess.run(
                ["git", "-C", str(root.resolve()), "log", "-8", "--format=%h %cI %s", "--", "."],
                capture_output=True,
                text=True,
                timeout=4,
                check=True,
            )
            return {"available": True, "commits": redact(result.stdout.splitlines())}
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    data = github_get("/commits?per_page=8")
    if isinstance(data, dict):
        return data
    return {
        "available": True,
        "commits": [
            {
                "sha": r["sha"],
                "message": redact(r["commit"]["message"][:500]),
                "date": r["commit"]["committer"]["date"],
            }
            for r in data[:8]
        ],
    }


def read_source(path, start, limit):
    cfg = settings()
    if cfg.local_repository_path and Path(cfg.local_repository_path).is_dir():
        contents = source_path(cfg.local_repository_path, path).read_text(encoding="utf-8")
    else:
        parts = PurePosixPath(path).parts
        if (
            not parts
            or "\\" in path
            or any(p.startswith(".") or p in {"faults", "experiments", "tests", "db", "core"} for p in parts)
        ):
            raise ValueError("Source path unavailable")
        if PurePosixPath(path).suffix not in {".py", ".ts", ".tsx", ".js", ".go", ".java", ".rs"}:
            raise ValueError("Code files only")
        data = github_get("/contents/" + quote(path, safe="/"))
        if not isinstance(data, dict) or data.get("available") is False:
            return {"available": False, "reason": "Source unavailable"}
        if data.get("size", 100001) > 100000 or data.get("encoding") != "base64":
            raise ValueError("Source file too large or unsupported")
        contents = base64.b64decode(data["content"]).decode("utf-8")
    lines = contents.splitlines()
    return {
        "available": True,
        "path": path,
        "start_line": start,
        "lines": [
            {"line": n + 1, "text": redact(lines[n])}
            for n in range(start - 1, min(len(lines), start - 1 + limit))
        ],
    }


def search_source(query, limit):
    root = Path(settings().local_repository_path)
    matches = []
    if not query or not root.is_dir():
        return {"available": False, "reason": "Local repository unavailable"}
    for index, path in enumerate(root.rglob("*")):
        if index >= 1500:
            break
        try:
            file = source_path(str(root), str(path.relative_to(root)))
        except (ValueError, OSError):
            continue
        for number, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if query.casefold() in line.casefold():
                matches.append(
                    {"path": str(file.relative_to(root.resolve())), "line": number, "text": redact(line)}
                )
                if len(matches) >= limit:
                    return {"available": True, "matches": matches, "truncated": True}
    return {"available": True, "matches": matches}
