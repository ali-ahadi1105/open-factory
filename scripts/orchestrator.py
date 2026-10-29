"""Software Factory Phase Orchestrator and State Transition Engine.

Monitors GitHub issues and pull requests, detects when prerequisite phases
complete (via PR merge or issue close), and automatically unblocks dependent
phases by applying the `openhands` trigger label.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from github_client import GitHubClient, GitHubError

METADATA_RE = re.compile(r"<!--\s*software-factory-metadata:\s*({.*?})\s*-->")
BLOCKED_LABEL_RE = re.compile(r"^blocked-by:#?(\d+)$", re.IGNORECASE)
BODY_BLOCKER_RE = re.compile(r"(?:Blocked by|Depends on):\s*([#\d,\s]+)", re.IGNORECASE)


def extract_blockers(issue: dict[str, Any]) -> list[int]:
    """Extract list of blocker issue numbers from metadata, labels, or body text."""
    blockers: set[int] = set()

    # 1. Check embedded metadata
    body = issue.get("body") or ""
    meta_match = METADATA_RE.search(body)
    if meta_match:
        try:
            data = json.loads(meta_match.group(1))
            for b in data.get("blocked_by", []):
                blockers.add(int(b))
        except (json.JSONDecodeError, ValueError):
            pass

    # 2. Check labels (e.g. blocked-by:#123)
    labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
    for lbl in labels:
        m = BLOCKED_LABEL_RE.match(lbl)
        if m:
            blockers.add(int(m.group(1)))

    # 3. Check markdown body fallback
    if not blockers and body:
        m = BODY_BLOCKER_RE.search(body)
        if m:
            nums = re.findall(r"#?(\d+)", m.group(1))
            for n in nums:
                blockers.add(int(n))

    return sorted(blockers)


def check_and_transition(
    repo: str,
    client: GitHubClient,
    trigger_label: str = "openhands",
    blocked_label: str = "blocked",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan open issues in repository and unblock issues whose dependencies are closed."""
    open_issues = client.list_issues(repo, state="open")
    transitions: list[dict[str, Any]] = []
    issue_status_cache: dict[int, dict[str, Any]] = {}

    def get_cached_issue(num: int) -> dict[str, Any]:
        if num not in issue_status_cache:
            try:
                issue_status_cache[num] = client.get_issue(repo, num)
            except GitHubError:
                issue_status_cache[num] = {"state": "unknown"}
        return issue_status_cache[num]

    # Pre-populate cache with known open issues
    for iss in open_issues:
        issue_status_cache[iss["number"]] = iss

    for issue in open_issues:
        issue_num = issue["number"]
        labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
        is_marked_blocked = blocked_label in labels or any(BLOCKED_LABEL_RE.match(l) for l in labels)

        blockers = extract_blockers(issue)
        if not blockers:
            continue

        if not is_marked_blocked and trigger_label in labels:
            # Already unblocked and triggered
            continue

        # Check all blockers
        all_resolved = True
        resolved_details = []
        pending_details = []

        for b_num in blockers:
            blocker_info = get_cached_issue(b_num)
            b_state = blocker_info.get("state")
            b_title = blocker_info.get("title", f"Issue #{b_num}")
            if b_state == "closed":
                resolved_details.append(f"#{b_num} ({b_title})")
            else:
                all_resolved = False
                pending_details.append(f"#{b_num} ({b_title} - state: {b_state})")

        if all_resolved:
            transitions.append({
                "issue_number": issue_num,
                "title": issue.get("title"),
                "resolved_blockers": blockers,
                "action": "unblock_and_trigger",
            })

            if not dry_run:
                # Remove blocked labels
                client.remove_label(repo, issue_num, blocked_label)
                for lbl in labels:
                    if BLOCKED_LABEL_RE.match(lbl):
                        client.remove_label(repo, issue_num, lbl)

                # Add trigger label & ready status
                client.add_labels(repo, issue_num, [trigger_label, "status:ready"])

                # Post update comment
                blocker_summary = "\n".join([f"- {item}" for item in resolved_details])
                comment_body = (
                    f"### 🚀 All Dependencies Resolved!\n\n"
                    f"All prerequisite issues have been completed and closed:\n"
                    f"{blocker_summary}\n\n"
                    f"**Automated Actions Taken:**\n"
                    f"- Removed `{blocked_label}` status.\n"
                    f"- Added `{trigger_label}` trigger label.\n\n"
                    f"OpenHands Software Factory will now automatically begin implementation of this phase."
                )
                client.create_comment(repo, issue_num, comment_body)

    return {
        "repo": repo,
        "scanned_issues": len(open_issues),
        "transitions_count": len(transitions),
        "transitions": transitions,
    }


def get_pipeline_graph(repo: str, client: GitHubClient) -> dict[str, Any]:
    """Retrieve all software factory issues and render their status graph."""
    # List both open and closed issues
    open_issues = client.list_issues(repo, state="open")
    closed_issues = client.list_issues(repo, state="closed")
    all_issues = open_issues + closed_issues

    factory_issues = []
    for issue in all_issues:
        labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
        has_phase = any(l.startswith("phase:") for l in labels)
        blockers = extract_blockers(issue)

        if has_phase or blockers:
            phase_val = None
            for l in labels:
                if l.startswith("phase:"):
                    try:
                        phase_val = int(l.split(":")[1])
                    except ValueError:
                        pass
            factory_issues.append({
                "number": issue["number"],
                "title": issue["title"],
                "phase": phase_val,
                "state": issue["state"],
                "labels": labels,
                "blockers": blockers,
                "url": issue.get("html_url", ""),
            })

    factory_issues.sort(key=lambda x: (x["phase"] or 999, x["number"]))
    return {"repo": repo, "factory_issues": factory_issues}


GITHUB_ACTION_WORKFLOW_TEMPLATE = r"""name: Software Factory Phase Orchestrator

on:
  pull_request:
    types: [closed]
  issues:
    types: [closed]
  workflow_dispatch:

permissions:
  contents: write
  issues: write
  pull-requests: read

jobs:
  advance-phase:
    # Trigger if an issue was closed or if a PR was closed AND merged
    if: (github.event_name == 'issues' && github.event.action == 'closed') || (github.event_name == 'pull_request' && github.event.pull_request.merged == true) || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Run Phase Orchestrator
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
        run: |
          python3 - << 'EOF'
          import os, json, re, urllib.request, urllib.error

          token = os.environ["GITHUB_TOKEN"]
          repo = os.environ["REPO"]
          base_url = f"https://api.github.com/repos/{repo}"
          headers = {
              "Authorization": f"Bearer {token}",
              "Accept": "application/vnd.github+json",
              "X-GitHub-Api-Version": "2022-11-28",
              "User-Agent": "software-factory-orchestrator-action"
          }

          def api(method, path, body=None):
              data = json.dumps(body).encode("utf-8") if body else None
              req = urllib.request.Request(f"{base_url}{path}", data=data, method=method, headers=headers)
              with urllib.request.urlopen(req) as r:
                  text = r.read().decode("utf-8")
                  return json.loads(text) if text.strip() else None

          # Fetch open issues
          open_issues = api("GET", "/issues?state=open&per_page=100")
          open_issues = [i for i in open_issues if "pull_request" not in i]

          cache = {}
          def get_issue(num):
              if num not in cache:
                  try:
                      cache[num] = api("GET", f"/issues/{num}")
                  except Exception:
                      cache[num] = {"state": "unknown"}
              return cache[num]

          for issue in open_issues:
              body = issue.get("body") or ""
              labels = [l["name"] if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
              blockers = set()
              
              # Extract from metadata
              meta = re.search(r"<!--\s*software-factory-metadata:\s*({.*?})\s*-->", body)
              if meta:
                  try:
                      for b in json.loads(meta.group(1)).get("blocked_by", []):
                          blockers.add(int(b))
                  except Exception:
                      pass
              
              # Extract from labels
              for l in labels:
                  m = re.match(r"^blocked-by:#?(\d+)$", l, re.IGNORECASE)
                  if m:
                      blockers.add(int(m.group(1)))

              if not blockers:
                  continue
              if "openhands" in labels and "blocked" not in labels:
                  continue

              all_done = True
              resolved = []
              for b in sorted(blockers):
                  b_info = get_issue(b)
                  if b_info.get("state") == "closed":
                      resolved.append(f"#{b} ({b_info.get('title', '')})")
                  else:
                      all_done = False

              if all_done:
                  print(f"Unblocking issue #{issue['number']}: {issue['title']}")
                  # Remove blocked label
                  if "blocked" in labels:
                      try:
                          api("DELETE", f"/issues/{issue['number']}/labels/blocked")
                      except Exception:
                          pass
                  for l in labels:
                      if re.match(r"^blocked-by:#?\d+$", l, re.IGNORECASE):
                          try:
                              api("DELETE", f"/issues/{issue['number']}/labels/{urllib.parse.quote(l)}")
                          except Exception:
                              pass
                  
                  # Add openhands trigger label
                  api("POST", f"/issues/{issue['number']}/labels", {"labels": ["openhands", "status:ready"]})
                  
                  # Post comment
                  summary = "\\n".join([f"- {r}" for r in resolved])
                  comment = (
                      f"### 🚀 All Dependencies Resolved!\\n\\n"
                      f"All prerequisite issues have been completed and closed:\\n"
                      f"{summary}\\n\\n"
                      f"**Automated Actions Taken:**\\n"
                      f"- Removed `blocked` status.\\n"
                      f"- Added `openhands` trigger label.\\n\\n"
                      f"OpenHands Software Factory will now automatically begin implementation of this phase."
                  )
                  api("POST", f"/issues/{issue['number']}/comments", {"body": comment})
          EOF
"""


def install_orchestrator_workflow(repo: str, client: GitHubClient, branch: str = "main") -> dict[str, Any]:
    """Commit the GitHub Actions orchestrator workflow into the target repository."""
    workflow_path = ".github/workflows/factory-orchestrator.yml"
    commit_msg = "ci: install Software Factory automated phase orchestrator workflow"
    result = client.create_or_update_file(
        repo=repo,
        path=workflow_path,
        content=GITHUB_ACTION_WORKFLOW_TEMPLATE,
        commit_message=commit_msg,
        branch=branch,
    )
    return {
        "repo": repo,
        "installed_path": workflow_path,
        "commit_sha": result.get("commit", {}).get("sha"),
        "url": result.get("content", {}).get("html_url"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Software Factory Phase Orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Check issues once and transition newly unblocked phases")
    p_run.add_argument("--repos", required=True, help="Target GitHub repository (owner/repo)")
    p_run.add_argument("--trigger-label", default="openhands", help="Trigger label (default: openhands)")
    p_run.add_argument("--blocked-label", default="blocked", help="Blocked label (default: blocked)")
    p_run.add_argument("--dry-run", action="store_true", help="Check without modifying labels or commenting")

    p_watch = sub.add_parser("watch", help="Continuously poll repository and transition phases")
    p_watch.add_argument("--repos", required=True, help="Target GitHub repository (owner/repo)")
    p_watch.add_argument("--interval", type=int, default=30, help="Polling interval in seconds (default: 30)")
    p_watch.add_argument("--trigger-label", default="openhands")
    p_watch.add_argument("--blocked-label", default="blocked")

    p_graph = sub.add_parser("graph", help="Render status of all software factory issues in repo")
    p_graph.add_argument("--repos", required=True, help="Target GitHub repository (owner/repo)")

    p_install = sub.add_parser("install", help="Install GitHub Actions workflow in repository")
    p_install.add_argument("--repos", required=True, help="Target GitHub repository (owner/repo)")
    p_install.add_argument("--branch", default="main", help="Target branch (default: main)")

    args = parser.parse_args()
    client = GitHubClient()

    try:
        if args.command == "run":
            res = check_and_transition(
                repo=args.repos,
                client=client,
                trigger_label=args.trigger_label,
                blocked_label=args.blocked_label,
                dry_run=args.dry_run,
            )
            print(json.dumps(res, indent=2))
        elif args.command == "watch":
            print(f"Starting Software Factory orchestrator watcher on {args.repos} (interval {args.interval}s)...")
            while True:
                res = check_and_transition(
                    repo=args.repos,
                    client=client,
                    trigger_label=args.trigger_label,
                    blocked_label=args.blocked_label,
                )
                if res["transitions_count"] > 0:
                    print(f"[{time.strftime('%X')}] Transitioned {res['transitions_count']} issue(s):")
                    for t in res["transitions"]:
                        print(f"  - Issue #{t['issue_number']}: {t['title']} (unblocked)")
                time.sleep(args.interval)
        elif args.command == "graph":
            res = get_pipeline_graph(repo=args.repos, client=client)
            print(json.dumps(res, indent=2))
        elif args.command == "install":
            res = install_orchestrator_workflow(repo=args.repos, client=client, branch=args.branch)
            print(json.dumps(res, indent=2))
        return 0
    except (GitHubError, ValueError) as err:
        print(json.dumps({"error": str(err)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
