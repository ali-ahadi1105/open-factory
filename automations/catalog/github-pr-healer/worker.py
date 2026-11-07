"""Software Factory PR Auto-Healer worker.

Periodically executed by OpenHands automation runtime to scan open pull requests,
detect CI failures or change requests, and trigger an automated revision loop.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add script directory to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from github_client import GitHubClient, GitHubError
from healer import scan_and_heal


def get_token(secret_name: str) -> str | None:
    token = os.environ.get(secret_name) or os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if token:
        return token
    if os.environ.get("AGENT_SERVER_URL") and os.environ.get("SESSION_API_KEY"):
        try:
            from openhands.sdk.workspace import RemoteWorkspace
            workspace = RemoteWorkspace(
                host=os.environ["AGENT_SERVER_URL"],
                api_key=os.environ["SESSION_API_KEY"],
                working_dir=os.environ.get("WORKSPACE_BASE", "/workspace"),
            )
            try:
                secret = workspace.get_secrets([secret_name]).get(secret_name)
                if secret and secret.get_value():
                    return secret.get_value()
            finally:
                workspace.reset_client()
        except Exception:
            pass
    try:
        import subprocess
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def main() -> int:
    config_path = Path("config.json")
    if not config_path.is_file():
        print(json.dumps({"error": "config.json not found in working directory"}), flush=True)
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    token_secret_name = config.get("github_token_secret", "GITHUB_PERSONAL_ACCESS_TOKEN")
    token = get_token(token_secret_name)
    if not token:
        print(json.dumps({"error": f"Required GitHub token {token_secret_name} not found in environment"}), flush=True)
        return 1

    client = GitHubClient(token=token)
    repos = config.get("repos", [])
    if isinstance(repos, str):
        repos = [r.strip() for r in repos.split(",") if r.strip()]

    branch_prefix = config.get("branch_prefix", "openhands/issue")
    max_retries = int(config.get("max_retries", 3))

    results = []
    for repo in repos:
        try:
            res = scan_and_heal(repo, client=client, branch_prefix=branch_prefix, max_retries=max_retries)
            results.append(res)
            print(
                json.dumps({
                    "repo": repo,
                    "status": "ok",
                    "scanned": res["scanned_prs"],
                    "healed": res["healed"],
                    "escalated": res["escalated"],
                }),
                flush=True,
            )
        except Exception as exc:
            results.append({"repo": repo, "error": str(exc)})
            print(json.dumps({"repo": repo, "error": str(exc)}), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
