#!/usr/bin/env python3
"""Software Factory Web Dashboard & Launch Service.

Provides a clean, intuitive web UI and REST API for:
- One-click project launching from ideas/specs
- Live multi-phase pipeline graph visualizer
- Autonomous PR auto-healing & phase advancement triggers
- System health & OpenHands runtime diagnostics
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure open-factory scripts are on sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
DASHBOARD_DIR = Path(__file__).resolve().parent
STATIC_DIR = DASHBOARD_DIR / "static"
PROJECTS_FILE = DASHBOARD_DIR / "projects.json"

sys.path.insert(0, str(SCRIPTS_DIR))

from github_client import GitHubClient, GitHubError, get_token
from planner import parse_markdown_spec, execute_plan, validate_dag, PlanTask
from orchestrator import check_and_transition, get_pipeline_graph, install_orchestrator_workflow
from healer import scan_and_heal, diagnose_pr, heal_pr

app = FastAPI(title="OpenHands Software Factory Dashboard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Helper functions ---

def get_client() -> GitHubClient:
    try:
        return GitHubClient()
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"GitHub token error: {exc}")


def load_tracked_projects() -> list[dict[str, Any]]:
    if not PROJECTS_FILE.is_file():
        return []
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_tracked_projects(projects: list[dict[str, Any]]):
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


def ensure_project_tracked(repo: str, name: str = "", description: str = ""):
    projects = load_tracked_projects()
    for p in projects:
        if p["repo"].lower() == repo.lower():
            return
    projects.insert(0, {
        "repo": repo,
        "name": name or repo.split("/")[-1],
        "description": description or f"Software Factory project {repo}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    save_tracked_projects(projects)


# --- Models ---

class LaunchRequest(BaseModel):
    repo_name: str = Field(..., description="Target repo name (e.g. 'my-app' or 'owner/my-app')")
    spec_text: str = Field(..., description="Markdown project specification with ## Phase N sections")
    create_github_repo: bool = Field(True, description="Create repository on GitHub if it does not exist")
    is_private: bool = Field(False, description="Whether to make new repository private")
    install_orchestrator: bool = Field(True, description="Install GitHub Actions auto-orchestrator workflow")
    enable_stations: bool = Field(True, description="Enable OpenHands automated stations")


class TrackRequest(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/repo' format")
    name: str | None = None
    description: str | None = None


# --- Templates Data ---

TEMPLATES = [
    {
        "id": "nextjs-saas",
        "title": "Next.js 14 Modern Web Application",
        "description": "Fullstack web app with Tailwind CSS, Zustand data layer, and responsive UI",
        "category": "Web Fullstack",
        "icon": "layout",
        "spec": """# Modern Web Workspace Application

## Phase 1: Foundation, Layout & Navigation
Create Next.js 14 project structure with TypeScript and Tailwind CSS.
- Responsive app shell with Collapsible Sidebar, Header, and Main Content canvas.
- Theme support (Dark/Light mode).
- Unit tests for navigation components with Jest and React Testing Library.

## Phase 2: Hierarchical Document Store & CRUD
Implement full client-side state management layer with Zustand and LocalStorage persistence.
- Data models for Documents, Folders, and Metadata.
- Full CRUD operations: create, read, update, soft-delete, and restore from trash.
- Parent-child nesting operations and breadcrumb generators.
- Unit tests for store operations and edge cases.

## Phase 3: Interactive Modular Editor
Build rich modular content editor with keyboard shortcuts and slash commands.
- Slash command menu (/h1, /todo, /bullet, /code, /quote).
- Markdown auto-formatting and keyboard navigation.
- Real-time auto-saving with debouncing to store.
- Component tests for editor interactions.

## Phase 4: Full-Text Search, Cover Images & Export
Finalize workspace productivity features.
- Full-text search modal with fuzzy query matching and recent searches.
- Cover image selector with gradient presets and Unsplash URLs.
- Document export (Markdown and JSON download).
- Comprehensive end-to-end integration tests.
""",
    },
    {
        "id": "fastapi-service",
        "title": "Python FastAPI Cloud Microservice",
        "description": "High-performance REST API with SQLite/Postgres, JWT Auth, and Docker packaging",
        "category": "Backend API",
        "icon": "server",
        "spec": """# Cloud Task & Automation Microservice

## Phase 1: Project Scaffolding & Database Layer
Set up Python FastAPI application with clean architecture.
- SQLAlchemy 2.0 async engine and SQLite/PostgreSQL support.
- Pydantic v2 schemas and base database models.
- Health check endpoints (/health, /ready, /metrics).
- Unit tests with pytest and testcontainers/sqlite.

## Phase 2: User Authentication & Tenant Management
Implement secure authentication and user authorization layer.
- User registration, password hashing with bcrypt, and JWT token issuing.
- Role-based access control (Admin, Member, Viewer).
- Auth middleware and dependency injection for current_user.
- Pytest suite covering token expiry, invalid credentials, and roles.

## Phase 3: Core Domain CRUD & Background Tasks
Implement core resource management and asynchronous processing.
- Full REST endpoints for Tasks and Workspaces (filtering, pagination, sorting).
- Background worker tasks for webhook dispatching and email notifications.
- Input validation and informative RFC 7807 error responses.
- API integration tests covering full lifecycle.

## Phase 4: OpenAPI Docs, Rate Limiting & Production Docker
Production readiness and deployment packaging.
- OpenAPI schema refinement with request/response examples.
- Rate limiting middleware with Redis/in-memory fallback.
- Multi-stage Dockerfile optimized for security and small footprint.
- GitHub Actions CI workflow for linting, typing (mypy), and pytest.
""",
    },
    {
        "id": "cli-tool",
        "title": "Python Developer CLI Utility",
        "description": "Powerful command-line utility with argparse/click, rich terminal UI, and logging",
        "category": "Dev Tools",
        "icon": "terminal",
        "spec": """# DevPulse - Developer Workflow CLI

## Phase 1: Core Engine & Subcommand Dispatcher
Set up clean modular Python CLI application.
- Entrypoint structure with argparse and command dispatchers.
- Configuration file loader (.devpulse.json / YAML) with fallback defaults.
- Clean exit codes and stream routing (stdout for data, stderr for logs).
- Unit tests with pytest verifying argument parsing and flags.

## Phase 2: Repository Scanner & Metric Collector
Build static analysis and git metric extraction engine.
- Git history analyzer (commits per author, churn rate, stale branches).
- Codebase statistics calculator (file types, SLOC, TODO comments).
- Fast concurrent directory scanning using multiprocessing or threads.
- Comprehensive tests using temporary git repositories.

## Phase 3: Rich Terminal Visualization & Interactive TUI
Add rich terminal UI formatting.
- Colored outputs, progress bars, and ASCII tables using `rich`.
- Interactive prompts for guided actions and configuration wizards.
- Export results to JSON, CSV, and Markdown report files.
- Unit tests for formatter outputs.
""",
    },
]


# --- API Routes ---

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h3>Dashboard static files not found</h3>", status_code=404)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/system/status")
def get_system_status():
    status = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "github": {"connected": False, "user": None, "token_source": None},
        "openhands": {"connected": False, "version": None, "usable_tools": []},
    }

    # 1. Test GitHub
    try:
        token = get_token()
        status["github"]["token_source"] = "active_token"
        client = GitHubClient(token=token)
        user_info = client.request("GET", "/user")
        status["github"]["connected"] = True
        status["github"]["user"] = {
            "login": user_info.get("login"),
            "name": user_info.get("name"),
            "avatar_url": user_info.get("avatar_url"),
            "public_repos": user_info.get("public_repos"),
            "html_url": user_info.get("html_url"),
        }
    except Exception as exc:
        status["github"]["error"] = str(exc)

    # 2. Test OpenHands
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/server_info", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            status["openhands"]["connected"] = True
            status["openhands"]["version"] = data.get("version")
            status["openhands"]["title"] = data.get("title")
            status["openhands"]["usable_tools"] = data.get("usable_tools", [])
    except Exception as exc:
        status["openhands"]["error"] = str(exc)

    return status


@app.get("/api/templates")
def get_templates():
    return TEMPLATES


@app.get("/api/projects")
def list_projects():
    projects = load_tracked_projects()
    client = None
    try:
        client = get_client()
    except Exception:
        pass

    results = []
    for p in projects:
        repo = p["repo"]
        summary = {"repo": repo, "name": p.get("name", repo.split("/")[-1]), "description": p.get("description", "")}
        if client:
            try:
                # Fast check of factory issues
                open_issues = client.list_issues(repo, state="open")
                closed_issues = client.list_issues(repo, state="closed")
                all_issues = open_issues + closed_issues
                phase_issues = [
                    i for i in all_issues
                    if any((l.get("name") if isinstance(l, dict) else str(l)).startswith("phase:") for l in i.get("labels", []))
                ]
                total = len(phase_issues)
                closed_count = len([i for i in phase_issues if i.get("state") == "closed"])
                active = next((i for i in phase_issues if i.get("state") == "open" and any(
                    (l.get("name") if isinstance(l, dict) else str(l)) == "openhands" for l in i.get("labels", [])
                )), None)

                summary["total_phases"] = total
                summary["completed_phases"] = closed_count
                summary["progress_percent"] = int((closed_count / total * 100)) if total > 0 else 0
                summary["active_phase_title"] = active.get("title") if active else ("Completed" if total > 0 and closed_count == total else "Idle")
                summary["active_phase_number"] = active.get("number") if active else None
            except Exception:
                summary["total_phases"] = 0
                summary["completed_phases"] = 0
                summary["progress_percent"] = 0
                summary["active_phase_title"] = "Unknown"
        results.append(summary)
    return results


@app.post("/api/projects/track")
def track_project(req: TrackRequest):
    client = get_client()
    repo = req.repo.strip()
    # Verify repo exists
    try:
        info = client.request("GET", f"/repos/{repo}")
    except GitHubError:
        raise HTTPException(status_code=404, detail=f"Repository '{repo}' not found or not accessible on GitHub.")

    ensure_project_tracked(repo, name=req.name or info.get("name"), description=req.description or info.get("description"))
    return {"status": "ok", "repo": repo}


@app.post("/api/projects/launch")
def launch_project(req: LaunchRequest):
    client = get_client()
    raw_repo = req.repo_name.strip()
    user_info = client.request("GET", "/user")
    current_user = user_info.get("login")

    # Resolve full repo name
    if "/" in raw_repo:
        repo = raw_repo
    else:
        repo = f"{current_user}/{raw_repo}"

    # 1. Ensure or create repository on GitHub
    repo_created = False
    try:
        client.request("GET", f"/repos/{repo}")
    except GitHubError as err:
        if err.status == 404 and req.create_github_repo:
            # Create repository
            repo_only = repo.split("/")[-1]
            create_payload = {
                "name": repo_only,
                "private": req.is_private,
                "description": "Autonomous Software Factory project created via Dashboard",
                "auto_init": True,  # Creates initial README so main branch exists immediately
            }
            try:
                if repo.split("/")[0] == current_user:
                    client.request("POST", "/user/repos", data=create_payload)
                else:
                    org = repo.split("/")[0]
                    client.request("POST", f"/orgs/{org}/repos", data=create_payload)
                repo_created = True
                time.sleep(2)  # Wait for GitHub eventually-consistent repo creation
            except Exception as create_err:
                raise HTTPException(status_code=400, detail=f"Failed to create repository {repo}: {create_err}")
        else:
            raise HTTPException(status_code=404, detail=f"Repository {repo} does not exist and auto-create is disabled.")

    # 2. Parse specification markdown
    try:
        tasks = parse_markdown_spec(req.spec_text)
        if not tasks:
            raise ValueError("No phases found. Make sure to use '## Phase 1: Title' headers.")
        validate_dag(tasks)
    except Exception as spec_err:
        raise HTTPException(status_code=400, detail=f"Invalid specification format: {spec_err}")

    # 3. Execute plan (Create phased GitHub issues with dependencies)
    try:
        plan_result = execute_plan(repo=repo, tasks=tasks, client=client, trigger_label="openhands", blocked_label="blocked")
    except Exception as plan_err:
        raise HTTPException(status_code=500, detail=f"Failed to publish issues to GitHub: {plan_err}")

    # 4. Install orchestrator workflow into repository if requested
    orchestrator_installed = False
    if req.install_orchestrator:
        try:
            install_orchestrator_workflow(repo=repo, client=client, branch="main")
            orchestrator_installed = True
        except Exception:
            pass

    # 5. Connect OpenHands Stations if requested
    stations_connected = False
    if req.enable_stations:
        try:
            # Run factory create locally
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "factory.py"),
                "create",
                "--repos",
                repo,
                "--ids",
                "github-issue-to-pr,github-pr-reviewer,github-phase-orchestrator,github-pr-healer,github-delivery-watchdog",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                stations_connected = True
        except Exception:
            pass

    # 6. Save in tracked projects
    ensure_project_tracked(repo, name=repo.split("/")[-1], description=f"Launched via Software Factory ({len(tasks)} phases)")

    return {
        "status": "launched",
        "repo": repo,
        "repo_created": repo_created,
        "orchestrator_installed": orchestrator_installed,
        "stations_connected": stations_connected,
        "total_phases": len(tasks),
        "created_issues": plan_result.get("issues", []),
        "url": f"https://github.com/{repo}",
    }


@app.get("/api/projects/{owner}/{repo}/pipeline")
def get_project_pipeline(owner: str, repo: str):
    full_repo = f"{owner}/{repo}"
    client = get_client()

    # 1. Fetch Issues
    try:
        open_issues = client.list_issues(full_repo, state="open")
        closed_issues = client.list_issues(full_repo, state="closed")
        all_issues = open_issues + closed_issues
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch issues: {exc}")

    # 2. Fetch Pull Requests
    try:
        pull_requests = client.list_pulls(full_repo, state="all")
    except Exception:
        pull_requests = []

    # Map PRs by branch / issue
    pr_by_issue: dict[int, dict[str, Any]] = {}
    for pr in pull_requests:
        branch = pr.get("head", {}).get("ref", "")
        # e.g. openhands/issue-2
        m = re.match(r"^openhands/issue-(\d+)$", branch)
        if m:
            issue_num = int(m.group(1))
            pr_by_issue[issue_num] = {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "merged_at": pr.get("merged_at"),
                "html_url": pr.get("html_url"),
                "head_sha": pr.get("head", {}).get("sha", "")[:7],
                "draft": pr.get("draft", False),
            }

    # 3. Assemble Phase Pipeline
    factory_phases = []
    for issue in all_issues:
        labels = [l.get("name") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
        phase_label = next((l for l in labels if l.startswith("phase:")), None)
        if not phase_label:
            continue

        try:
            phase_num = int(phase_label.split(":")[1])
        except ValueError:
            phase_num = 999

        issue_num = issue["number"]
        is_closed = issue.get("state") == "closed"
        is_triggered = "openhands" in labels and not is_closed
        is_blocked = "blocked" in labels and not is_closed
        has_fix = "openhands-fix" in labels
        is_escalated = "needs-human-attention" in labels

        blockers = []
        for l in labels:
            bm = re.match(r"^blocked-by:#?(\d+)$", l)
            if bm:
                blockers.append(int(bm.group(1)))

        status_text = "COMPLETED" if is_closed else ("ESCALATED" if is_escalated else ("HEALING" if has_fix else ("IN_PROGRESS" if is_triggered else ("BLOCKED" if is_blocked else "QUEUED"))))

        factory_phases.append({
            "phase": phase_num,
            "issue_number": issue_num,
            "title": issue["title"],
            "state": issue["state"],
            "status": status_text,
            "is_closed": is_closed,
            "is_active": is_triggered or has_fix,
            "blockers": blockers,
            "labels": labels,
            "url": issue.get("html_url"),
            "pr": pr_by_issue.get(issue_num),
        })

    factory_phases.sort(key=lambda x: (x["phase"], x["issue_number"]))

    total = len(factory_phases)
    completed = len([p for p in factory_phases if p["is_closed"]])
    percent = int((completed / total * 100)) if total > 0 else 0

    return {
        "repo": full_repo,
        "total_phases": total,
        "completed_phases": completed,
        "progress_percent": percent,
        "phases": factory_phases,
    }


@app.post("/api/projects/{owner}/{repo}/orchestrate")
def run_orchestrator(owner: str, repo: str):
    full_repo = f"{owner}/{repo}"
    client = get_client()
    try:
        res = check_and_transition(repo=full_repo, client=client)
        return {"status": "ok", "result": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{owner}/{repo}/heal")
def run_healer(owner: str, repo: str):
    full_repo = f"{owner}/{repo}"
    client = get_client()
    try:
        res = scan_and_heal(repo=full_repo, client=client)
        return {"status": "ok", "result": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Starting OpenHands Software Factory Dashboard on http://localhost:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False, app_dir=str(DASHBOARD_DIR))
