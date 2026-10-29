"""Software Factory Phase Orchestrator automation worker.

Periodically executed by OpenHands automation runtime to inspect repositories
and automatically unlock downstream phases as pull requests are completed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add script directory to sys.path so bundled modules are imported
sys.path.insert(0, str(Path(__file__).parent))

from github_client import GitHubClient, GitHubError
from orchestrator import check_and_transition


def main() -> int:
    config_path = Path("config.json")
    if not config_path.is_file():
        print(json.dumps({"error": "config.json not found in working directory"}), flush=True)
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    token_secret_name = config.get("github_token_secret", "GITHUB_PERSONAL_ACCESS_TOKEN")
    token = os.environ.get(token_secret_name)
    if not token:
        print(json.dumps({"error": f"Required GitHub token {token_secret_name} not found in environment"}), flush=True)
        return 1

    client = GitHubClient(token=token)
    repos = config.get("repos", [])
    if isinstance(repos, str):
        repos = [r.strip() for r in repos.split(",") if r.strip()]

    trigger_label = config.get("trigger_label", "openhands")
    results = []

    for repo in repos:
        try:
            res = check_and_transition(repo, client=client, trigger_label=trigger_label)
            results.append(res)
            print(json.dumps({"repo": repo, "status": "ok", "transitions": res["transitions_count"]}), flush=True)
        except Exception as exc:
            results.append({"repo": repo, "error": str(exc)})
            print(json.dumps({"repo": repo, "error": str(exc)}), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
