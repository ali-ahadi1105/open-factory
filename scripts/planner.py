"""Requirements Decomposition and Phased Issue Planner.

Analyzes high-level specs, creates a Directed Acyclic Graph (DAG) of phases,
and publishes structured GitHub issues with automated dependency labels:
- Phase 1 (no blockers): gets `openhands` (trigger) + `phase:1`
- Phase 2+ (blocked): gets `blocked` + `phase:N` + `blocked-by:#<issue_id>`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from github_client import GitHubClient, GitHubError


@dataclass
class PlanTask:
    phase: int
    title: str
    summary: str
    acceptance_criteria: list[str] = field(default_factory=list)
    depends_on_phases: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def parse_markdown_spec(content: str) -> list[PlanTask]:
    """Parse structured markdown containing `## Phase X: Title` sections."""
    lines = content.splitlines()
    tasks: list[PlanTask] = []
    current_phase: int | None = None
    current_title = ""
    current_summary: list[str] = []
    current_criteria: list[str] = []
    current_deps: list[int] = []
    current_labels: list[str] = []
    in_criteria = False

    def commit_task():
        nonlocal current_phase, current_title, current_summary, current_criteria, current_deps, current_labels, in_criteria
        if current_phase is not None and current_title:
            tasks.append(
                PlanTask(
                    phase=current_phase,
                    title=current_title.strip(),
                    summary="\n".join(current_summary).strip(),
                    acceptance_criteria=list(current_criteria),
                    depends_on_phases=list(current_deps),
                    labels=list(current_labels),
                )
            )
        current_phase = None
        current_title = ""
        current_summary = []
        current_criteria = []
        current_deps = []
        current_labels = []
        in_criteria = False

    phase_header_re = re.compile(r"^##\s+Phase\s+(\d+)[:\-]?\s*(.*)", re.IGNORECASE)
    deps_re = re.compile(r"^\*{0,2}(?:Depends on|Blockers?|Requires):\*{0,2}\s*(.*)", re.IGNORECASE)

    for line in lines:
        header_match = phase_header_re.match(line.strip())
        if header_match:
            commit_task()
            current_phase = int(header_match.group(1))
            current_title = header_match.group(2).strip() or f"Phase {current_phase}"
            continue

        if current_phase is None:
            continue

        stripped = line.strip()
        dep_match = deps_re.match(stripped)
        if dep_match:
            dep_text = dep_match.group(1)
            found_phases = re.findall(r"(?:Phase\s*)?(\d+)", dep_text, re.IGNORECASE)
            for dp in found_phases:
                p_num = int(dp)
                if p_num != current_phase and p_num not in current_deps:
                    current_deps.append(p_num)
            continue

        if stripped.lower().startswith("### acceptance criteria") or stripped.lower().startswith("**acceptance criteria**"):
            in_criteria = True
            continue

        if stripped.lower().startswith("### summary") or stripped.lower().startswith("### description"):
            in_criteria = False
            continue

        if in_criteria:
            if stripped.startswith("- [ ]") or stripped.startswith("- [x]") or stripped.startswith("* [ ]"):
                crit = stripped.lstrip("-* ").replace("[ ]", "").replace("[x]", "").strip()
                if crit:
                    current_criteria.append(crit)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                crit = stripped.lstrip("-* ").strip()
                if crit:
                    current_criteria.append(crit)
        else:
            if stripped.startswith("Labels:"):
                raw_labels = stripped.replace("Labels:", "").split(",")
                current_labels.extend([l.strip() for l in raw_labels if l.strip()])
            else:
                current_summary.append(line)

    commit_task()

    # Fallback: if no formal "## Phase X" headers found, split bullet points into sequential phases
    if not tasks:
        tasks = _fallback_split_into_phases(content)

    return tasks


def _fallback_split_into_phases(content: str) -> list[PlanTask]:
    """If input is raw text or bullets without phase headers, break into phases."""
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    tasks: list[PlanTask] = []
    phase = 1
    for p in paragraphs:
        # Check if paragraph has bullet points
        lines = p.splitlines()
        first_line = lines[0].lstrip("#*- 0123456789.)").strip()
        if not first_line:
            continue
        rest = "\n".join(lines[1:]).strip() if len(lines) > 1 else first_line
        deps = [phase - 1] if phase > 1 else []
        tasks.append(
            PlanTask(
                phase=phase,
                title=first_line[:80],
                summary=rest,
                acceptance_criteria=[f"Implement: {first_line[:60]}"],
                depends_on_phases=deps,
            )
        )
        phase += 1
    return tasks


def parse_json_plan(content: str) -> list[PlanTask]:
    data = json.loads(content)
    raw_tasks = data.get("tasks", data) if isinstance(data, dict) else data
    tasks = []
    for item in raw_tasks:
        tasks.append(
            PlanTask(
                phase=item["phase"],
                title=item["title"],
                summary=item.get("summary", item.get("description", "")),
                acceptance_criteria=item.get("acceptance_criteria", []),
                depends_on_phases=item.get("depends_on_phases", []),
                labels=item.get("labels", []),
            )
        )
    return tasks


def validate_dag(tasks: list[PlanTask]) -> None:
    phase_map = {t.phase: t for t in tasks}
    if len(phase_map) != len(tasks):
        raise ValueError("Duplicate phase numbers found in plan")

    for task in tasks:
        for dep in task.depends_on_phases:
            if dep not in phase_map:
                raise ValueError(f"Phase {task.phase} depends on non-existent Phase {dep}")
            if dep >= task.phase:
                raise ValueError(f"Cyclic or forward dependency: Phase {task.phase} depends on Phase {dep}")


def generate_mermaid_diagram(tasks: list[PlanTask], issue_map: dict[int, int] | None = None) -> str:
    lines = ["```mermaid", "flowchart TD"]
    for task in tasks:
        issue_id_str = f" (#{issue_map[task.phase]})" if issue_map and task.phase in issue_map else ""
        node_id = f"P{task.phase}"
        safe_title = task.title.replace('"', "'")
        lines.append(f'    {node_id}["Phase {task.phase}: {safe_title}{issue_id_str}"]')

    for task in tasks:
        for dep in task.depends_on_phases:
            lines.append(f"    P{dep} --> P{task.phase}")

    lines.append("```")
    return "\n".join(lines)


def build_issue_body(
    task: PlanTask,
    dep_issue_numbers: list[int],
    trigger_label: str = "openhands",
) -> str:
    parts = []
    parts.append(f"## Summary\n{task.summary}\n")

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria")
        for crit in task.acceptance_criteria:
            parts.append(f"- [ ] {crit}")
        parts.append("")

    parts.append("---")
    parts.append("### 🔗 Software Factory Phase & Dependency Status")
    parts.append(f"- **Phase Number:** {task.phase}")

    if dep_issue_numbers:
        dep_links = ", ".join([f"#{num}" for num in dep_issue_numbers])
        parts.append(f"- **Blocked by:** {dep_links}")
        parts.append(
            "- **Status:** ⏳ `blocked` — OpenHands will automatically start once prerequisite PRs are merged."
        )
    else:
        parts.append("- **Blocked by:** None (Root Task)")
        parts.append(
            f"- **Status:** 🚀 `ready` — Marked with `{trigger_label}` trigger label for immediate execution."
        )

    # Invisible metadata tag for deterministic parser
    metadata = {
        "factory_phase": task.phase,
        "blocked_by": dep_issue_numbers,
    }
    parts.append(f"\n<!-- software-factory-metadata: {json.dumps(metadata)} -->")
    return "\n".join(parts)


def execute_plan(
    repo: str,
    tasks: list[PlanTask],
    client: GitHubClient | None = None,
    trigger_label: str = "openhands",
    blocked_label: str = "blocked",
    dry_run: bool = False,
) -> dict[str, Any]:
    validate_dag(tasks)
    # Sort tasks by phase ascending
    sorted_tasks = sorted(tasks, key=lambda t: t.phase)

    phase_to_issue: dict[int, int] = {}
    created_issues: list[dict[str, Any]] = []

    for task in sorted_tasks:
        # Resolve dependencies to actual GitHub issue numbers
        dep_issue_numbers = [phase_to_issue[p] for p in task.depends_on_phases if p in phase_to_issue]

        labels = [f"phase:{task.phase}"]
        labels.extend(task.labels)

        if dep_issue_numbers:
            labels.append(blocked_label)
            for dep_num in dep_issue_numbers:
                labels.append(f"blocked-by:#{dep_num}")
        else:
            labels.append(trigger_label)
            labels.append("status:ready")

        issue_title = f"[Phase {task.phase}] {task.title}"
        issue_body = build_issue_body(task, dep_issue_numbers, trigger_label=trigger_label)

        if dry_run:
            simulated_issue_num = 1000 + task.phase
            phase_to_issue[task.phase] = simulated_issue_num
            created_issues.append({
                "phase": task.phase,
                "number": simulated_issue_num,
                "title": issue_title,
                "labels": labels,
                "blocked_by": dep_issue_numbers,
                "status": "triggered" if not dep_issue_numbers else "blocked",
                "body_preview": issue_body[:200] + "...",
            })
        else:
            assert client is not None
            resp = client.create_issue(
                repo=repo,
                title=issue_title,
                body=issue_body,
                labels=labels,
            )
            issue_num = resp["number"]
            issue_url = resp.get("html_url", "")
            phase_to_issue[task.phase] = issue_num
            created_issues.append({
                "phase": task.phase,
                "number": issue_num,
                "url": issue_url,
                "title": issue_title,
                "labels": labels,
                "blocked_by": dep_issue_numbers,
                "status": "triggered" if not dep_issue_numbers else "blocked",
            })

    return {
        "repo": repo,
        "dry_run": dry_run,
        "total_phases": len(tasks),
        "phase_to_issue": phase_to_issue,
        "issues": created_issues,
        "mermaid_dag": generate_mermaid_diagram(sorted_tasks, phase_to_issue),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Software Factory Requirements & Issue Planner")
    parser.add_argument("--repos", required=True, help="Target GitHub repository (owner/repo)")
    parser.add_argument("--spec", help="Raw requirement text or specification")
    parser.add_argument("--spec-file", help="Path to markdown or text file containing requirements")
    parser.add_argument("--plan-file", help="Path to JSON plan file with pre-structured phases")
    parser.add_argument("--trigger-label", default="openhands", help="Label that activates OpenHands (default: openhands)")
    parser.add_argument("--blocked-label", default="blocked", help="Label for queued/blocked phases (default: blocked)")
    parser.add_argument("--dry-run", action="store_true", help="Preview plan and issues without calling GitHub API")
    args = parser.parse_args()

    content = ""
    is_json = False
    if args.plan_file:
        content = Path(args.plan_file).read_text(encoding="utf-8")
        is_json = True
    elif args.spec_file:
        content = Path(args.spec_file).read_text(encoding="utf-8")
    elif args.spec:
        content = args.spec
    else:
        # Read from stdin if piped
        if not sys.stdin.isatty():
            content = sys.stdin.read()
        else:
            print(json.dumps({"error": "Provide requirements via --spec, --spec-file, --plan-file, or stdin"}, indent=2))
            return 1

    try:
        tasks = parse_json_plan(content) if is_json else parse_markdown_spec(content)
        if not tasks:
            print(json.dumps({"error": "Could not extract any tasks or phases from input"}, indent=2))
            return 1

        client = None if args.dry_run else GitHubClient()
        result = execute_plan(
            repo=args.repos,
            tasks=tasks,
            client=client,
            trigger_label=args.trigger_label,
            blocked_label=args.blocked_label,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (GitHubError, ValueError) as err:
        print(json.dumps({"error": str(err)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
