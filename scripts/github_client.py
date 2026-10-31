"""Deterministic GitHub API client using Python standard library (no extra dependencies).

Supports GitHub Personal Access Token (classic or fine-grained) via:
- GITHUB_PERSONAL_ACCESS_TOKEN
- GH_TOKEN
- GITHUB_TOKEN
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


def get_token() -> str:
    token = (
        os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not token:
        raise GitHubError("No GitHub token found in GITHUB_PERSONAL_ACCESS_TOKEN, GH_TOKEN, or GITHUB_TOKEN")
    return token.strip()


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com"):
        self.token = token or get_token()
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        raw_body: bytes | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "openhands-software-factory-v2",
        }

        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text.strip() else None
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")[:2000]
            raise GitHubError(f"GitHub API {method} {path} returned {err.code}: {detail}", status=err.code, detail=detail) from None
        except urllib.error.URLError as err:
            raise GitHubError(f"GitHub API {method} {path} connection error: {err.reason}") from None

    def paginate(self, path: str, params: dict | None = None, max_pages: int = 10) -> list[dict]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            params["page"] = page
            result = self.request("GET", path, params=params)
            if not isinstance(result, list):
                break
            items.extend(result)
            if len(result) < params["per_page"]:
                break
        return items

    # --- Issues ---

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        return self.request("POST", f"/repos/{repo}/issues", body=payload)

    def get_issue(self, repo: str, number: int) -> dict:
        return self.request("GET", f"/repos/{repo}/issues/{number}")

    def list_issues(self, repo: str, state: str = "open", labels: list[str] | None = None) -> list[dict]:
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)
        # GitHub /issues returns both issues and PRs (PRs have "pull_request" key)
        items = self.paginate(f"/repos/{repo}/issues", params=params)
        return [i for i in items if "pull_request" not in i]

    def update_issue(
        self,
        repo: str,
        number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        return self.request("PATCH", f"/repos/{repo}/issues/{number}", body=payload)

    def add_labels(self, repo: str, number: int, labels: list[str]) -> list[dict]:
        return self.request("POST", f"/repos/{repo}/issues/{number}/labels", body={"labels": labels})

    def remove_label(self, repo: str, number: int, label: str) -> None:
        try:
            self.request("DELETE", f"/repos/{repo}/issues/{number}/labels/{urllib.parse.quote(label)}")
        except GitHubError as err:
            # If label not present (404), safely ignore
            if err.status != 404:
                raise

    def create_comment(self, repo: str, number: int, body: str) -> dict:
        return self.request("POST", f"/repos/{repo}/issues/{number}/comments", body={"body": body})

    # --- Pull Requests ---

    def list_pulls(self, repo: str, state: str = "open") -> list[dict]:
        return self.paginate(f"/repos/{repo}/pulls", params={"state": state})

    def get_pull(self, repo: str, number: int) -> dict:
        return self.request("GET", f"/repos/{repo}/pulls/{number}")

    def list_reviews(self, repo: str, number: int) -> list[dict]:
        return self.paginate(f"/repos/{repo}/pulls/{number}/reviews")

    def list_review_comments(self, repo: str, number: int) -> list[dict]:
        return self.paginate(f"/repos/{repo}/pulls/{number}/comments")

    # --- CI Checks, Actions & Commit Statuses ---

    def get_check_runs(self, repo: str, commit_sha: str) -> list[dict]:
        res = self.request("GET", f"/repos/{repo}/commits/{commit_sha}/check-runs")
        return res.get("check_runs", []) if isinstance(res, dict) else []

    def get_commit_statuses(self, repo: str, commit_sha: str) -> list[dict]:
        res = self.request("GET", f"/repos/{repo}/commits/{commit_sha}/statuses")
        return res if isinstance(res, list) else []

    def create_commit_status(
        self,
        repo: str,
        commit_sha: str,
        state: str,
        context: str,
        description: str = "",
        target_url: str = "",
    ) -> dict:
        """Create a commit status (state: error, failure, pending, success)."""
        payload: dict[str, Any] = {
            "state": state,
            "context": context,
            "description": description[:140],
        }
        if target_url:
            payload["target_url"] = target_url
        return self.request("POST", f"/repos/{repo}/statuses/{commit_sha}", body=payload)

    def list_workflow_runs_for_commit(self, repo: str, commit_sha: str) -> list[dict]:
        res = self.request("GET", f"/repos/{repo}/actions/runs", params={"head_sha": commit_sha})
        return res.get("workflow_runs", []) if isinstance(res, dict) else []

    # --- Repository & Files ---

    def get_repo(self, repo: str) -> dict:
        return self.request("GET", f"/repos/{repo}")

    def get_file(self, repo: str, path: str, ref: str = "main") -> dict | None:
        try:
            return self.request("GET", f"/repos/{repo}/contents/{path.lstrip('/')}", params={"ref": ref})
        except GitHubError as err:
            if err.status == 404:
                return None
            raise

    def create_or_update_file(
        self,
        repo: str,
        path: str,
        content: str,
        commit_message: str,
        branch: str = "main",
    ) -> dict:
        existing = self.get_file(repo, path, ref=branch)
        payload: dict[str, Any] = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing and "sha" in existing:
            payload["sha"] = existing["sha"]
        return self.request("PUT", f"/repos/{repo}/contents/{path.lstrip('/')}", body=payload)
