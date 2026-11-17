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
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure open-factory scripts are on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
DASHBOARD_DIR = Path(__file__).resolve().parent
STATIC_DIR = DASHBOARD_DIR / "static"
PROJECTS_FILE = DASHBOARD_DIR / "projects.json"
ACCOUNTS_FILE = DASHBOARD_DIR / "accounts.json"
OPENHANDS_CONFIG_FILE = DASHBOARD_DIR / "openhands_config.json"

def load_dotenv(env_path: Path | None = None):
    """Zero-dependency .env loader that populates os.environ."""
    if env_path is None:
        env_path = DASHBOARD_DIR.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass

load_dotenv()

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

def load_accounts() -> list[dict[str, Any]]:
    if not ACCOUNTS_FILE.is_file():
        # Seed initial account from default system token if available
        try:
            token = get_token()
            client = GitHubClient(token=token)
            user_info = client.request("GET", "/user")
            initial_acc = {
                "login": user_info.get("login"),
                "name": user_info.get("name") or user_info.get("login"),
                "avatar_url": user_info.get("avatar_url"),
                "token": token,
                "label": "پیش‌فرض سیستم (Default)",
                "is_active": True,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            save_accounts([initial_acc])
            return [initial_acc]
        except Exception:
            return []
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_accounts(accounts: list[dict[str, Any]]):
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


def get_active_account() -> dict[str, Any] | None:
    accounts = load_accounts()
    for acc in accounts:
        if acc.get("is_active"):
            return acc
    if accounts:
        return accounts[0]
    return None


def get_client(account_login: str | None = None) -> GitHubClient:
    accounts = load_accounts()
    if account_login:
        for acc in accounts:
            if acc["login"].lower() == account_login.lower():
                return GitHubClient(token=acc["token"])
        raise HTTPException(status_code=404, detail=f"Account '{account_login}' not found in configured accounts.")
    
    active = get_active_account()
    if active and active.get("token"):
        return GitHubClient(token=active["token"])

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


# --- OpenHands Runtime & Engine Helpers ---

CLOUD_CONVERSATIONS_RUNTIME_MAP: dict[str, dict[str, Any]] = {}
RECENT_CONVERSATIONS: dict[str, dict[str, Any]] = {}

def get_default_local_key() -> str:
    key_file = Path.home() / ".openhands" / "agent-canvas" / "api-key.txt"
    if key_file.is_file():
        try:
            val = key_file.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    return os.environ.get("OPENHANDS_API_KEY") or os.environ.get("LOCAL_BACKEND_API_KEY", "")


def load_openhands_config() -> dict[str, Any]:
    default_cfg = {
        "mode": "local",
        "local": {
            "base_url": "http://127.0.0.1:8000",
            "api_key": get_default_local_key(),
        },
        "cloud": {
            "base_url": "https://app.all-hands.dev",
            "api_key": os.environ.get("OPENHANDS_CLOUD_API_KEY", ""),
        }
    }
    if not OPENHANDS_CONFIG_FILE.is_file():
        save_openhands_config(default_cfg)
        return default_cfg
    try:
        data = json.loads(OPENHANDS_CONFIG_FILE.read_text(encoding="utf-8"))
        if "mode" not in data or data["mode"] not in ("local", "cloud"):
            data["mode"] = "local"
        if "local" not in data:
            data["local"] = default_cfg["local"]
        if "cloud" not in data:
            data["cloud"] = default_cfg["cloud"]
        env_mode = os.environ.get("OPENHANDS_MODE")
        if env_mode in ("local", "cloud"):
            data["mode"] = env_mode
        cloud_key = os.environ.get("OPENHANDS_CLOUD_API_KEY")
        if cloud_key:
            data["cloud"]["api_key"] = cloud_key.strip()
        cloud_url = os.environ.get("OPENHANDS_CLOUD_BASE_URL")
        if cloud_url:
            data["cloud"]["base_url"] = cloud_url.rstrip("/")
        env_key = os.environ.get("OPENHANDS_API_KEY") or os.environ.get("LOCAL_BACKEND_API_KEY")
        if env_key:
            data["local"]["api_key"] = env_key.strip()
        elif not data["local"].get("api_key"):
            data["local"]["api_key"] = get_default_local_key()
        return data
    except Exception:
        return default_cfg


def save_openhands_config(cfg: dict[str, Any]):
    OPENHANDS_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_openhands_client_config() -> tuple[str, str, str]:
    """Returns (mode, base_url, api_key)."""
    cfg = load_openhands_config()
    mode = cfg.get("mode", "local")
    if mode == "cloud":
        target = cfg.get("cloud", {})
        base_url = (target.get("base_url") or "https://app.all-hands.dev").rstrip("/")
        api_key = (target.get("api_key") or "").strip()
    else:
        target = cfg.get("local", {})
        base_url = (target.get("base_url") or "http://127.0.0.1:8000").rstrip("/")
        api_key = (target.get("api_key") or "").strip() or get_default_local_key()
    return mode, base_url, api_key


def openhands_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 6.0
) -> Any:
    mode, base_url, api_key = get_openhands_client_config()
    req_timeout = 3.5 if method == "GET" and timeout > 3.5 else timeout

    candidate_urls = [base_url]
    if "127.0.0.1:8000" in base_url:
        candidate_urls.append(base_url.replace("127.0.0.1:8000", "localhost:8000"))
        candidate_urls.append(base_url.replace("127.0.0.1:8000", "127.0.0.1:18000"))
    elif "localhost:8000" in base_url:
        candidate_urls.append(base_url.replace("localhost:8000", "127.0.0.1:8000"))
        candidate_urls.append(base_url.replace("localhost:8000", "localhost:18000"))

    query = ""
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            query = f"?{query}"

    headers = {
        "Accept": "application/json",
    }
    if api_key:
        headers["X-Session-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    data_bytes = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(body).encode("utf-8")

    last_exc = None
    for cand_base in candidate_urls:
        url = f"{cand_base.rstrip('/')}{path}{query}"
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                content = resp.read().decode("utf-8")
                if not content:
                    return {}
                stripped = content.strip()
                if stripped.startswith("<") or "<!doctype html" in stripped[:100].lower():
                    # HTML fallback returned by SPA routing, treat as 404 endpoint not found
                    raise HTTPException(status_code=404, detail=f"Endpoint {path} returned HTML instead of JSON")
                return json.loads(content)
        except HTTPException:
            raise
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                detail = err_json.get("detail", err_json.get("message", err_body))
            except Exception:
                detail = err_body
            raise HTTPException(status_code=err.code, detail=f"OpenHands API Error ({err.code}): {detail}")
        except Exception as exc:
            last_exc = exc
            continue

    raise HTTPException(status_code=502, detail=f"Cannot connect to OpenHands ({mode} at {base_url}): {last_exc}")


def extract_text_content(content_obj: Any) -> str:
    if not content_obj:
        return ""
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, list):
        parts = []
        for item in content_obj:
            if isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(content_obj, dict):
        if "text" in content_obj:
            return str(content_obj["text"])
        if "content" in content_obj:
            return extract_text_content(content_obj["content"])
        return str(content_obj)
    return str(content_obj)


ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x1B\\|\x07)|\x1B[()][AB012])', re.DOTALL)

def clean_terminal_text(text: str) -> str:
    if not text:
        return ""
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', cleaned)
    # Remove CLI spinners (Braille pattern symbols)
    cleaned = re.sub(r'[\u2800-\u28ff]+', '', cleaned)
    # Remove lingering vte / bash prompt artifacts
    cleaned = re.sub(r'^\d+;C\d+;vte\.shell\.preexec!?\s*', '', cleaned)
    return cleaned.strip()


def parse_openhands_event(raw_ev: dict[str, Any]) -> dict[str, Any] | None:
    kind = raw_ev.get("kind", "")
    eid = raw_ev.get("id") or str(uuid.uuid4())
    ts = raw_ev.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    source = raw_ev.get("source", "unknown")
    llm_msg = raw_ev.get("llm_message") or {}

    # 1. Thought / Reasoning
    thought_raw = (
        raw_ev.get("thought")
        or raw_ev.get("reasoning_content")
        or llm_msg.get("reasoning_content")
    )
    thought_text = extract_text_content(thought_raw).strip()

    # 2. Action Event
    if kind == "ActionEvent":
        action = raw_ev.get("action") or {}
        tool_name = raw_ev.get("tool_name", "")
        action_kind = action.get("kind", "") if isinstance(action, dict) else ""
        cmd = ""
        if isinstance(action, dict):
            cmd = action.get("command", "") or action.get("path", "") or action.get("message", "")
        summary = raw_ev.get("summary") or ""

        is_term = (action_kind == "TerminalAction" or tool_name == "terminal")
        is_fin = (action_kind == "FinishAction" or tool_name == "finish")
        is_edit = (action_kind == "FileEditAction" or tool_name in ("file_editor", "edit"))

        evt_type = "command" if is_term else ("finish" if is_fin else ("edit" if is_edit else "action"))
        evt_title = "اجرای دستور ترمینال" if is_term else ("تکمیل فاز / وظیفه" if is_fin else (f"ابزار {tool_name}" if tool_name else (summary or "اقدام ایجنت")))

        return {
            "id": eid,
            "timestamp": ts,
            "kind": kind,
            "type": evt_type,
            "source": source,
            "title": evt_title,
            "thought": thought_text,
            "content": cmd,
            "tool_name": tool_name,
            "summary": summary,
            "is_error": False,
        }

    # 3. Observation Event (Terminal stdout, tool return, error)
    if kind == "ObservationEvent":
        obs = raw_ev.get("observation") or {}
        tool_name = raw_ev.get("tool_name", "")
        is_error = False
        content_text = ""
        if isinstance(obs, dict):
            is_error = bool(obs.get("is_error", False))
            content_text = extract_text_content(obs.get("content", ""))
        else:
            content_text = str(obs)

        cleaned_text = clean_terminal_text(content_text)
        if not cleaned_text and not is_error:
            cleaned_text = "دستور بدون خروجی متنی در ترمینال پایان یافت."

        return {
            "id": eid,
            "timestamp": ts,
            "kind": kind,
            "type": "error" if is_error else "output",
            "source": source,
            "title": f"خطای اجرای {tool_name or 'دستور'}" if is_error else f"خروجی {tool_name or 'دستور'}",
            "thought": "",
            "content": cleaned_text,
            "tool_name": tool_name,
            "summary": "",
            "is_error": is_error,
        }

    # 4. Message Event (User or Agent chat message)
    if kind == "MessageEvent" or raw_ev.get("type") in ("message", "user_message", "agent_message") or ("role" in raw_ev and "content" in raw_ev):
        role = llm_msg.get("role") or raw_ev.get("role") or raw_ev.get("source", "user")
        raw_content = llm_msg.get("content") or raw_ev.get("content") or ""
        content_text = extract_text_content(raw_content).strip()
        is_user = (role in ("user", "human") or raw_ev.get("type") == "user_message" or raw_ev.get("source") == "user")
        return {
            "id": eid,
            "timestamp": ts,
            "kind": kind or "MessageEvent",
            "type": "user_message" if is_user else "agent_message",
            "source": role,
            "title": "پیام شما (کاربر)" if is_user else "پاسخ ایجنت",
            "thought": thought_text,
            "content": content_text,
            "tool_name": "",
            "summary": "",
            "is_error": False,
        }

    # 5. Fallback if thought text is present
    if thought_text:
        return {
            "id": eid,
            "timestamp": ts,
            "kind": kind,
            "type": "thought",
            "source": source,
            "title": "تفکر و تحلیل ایجنت (Reasoning)",
            "thought": thought_text,
            "content": thought_text,
            "tool_name": "",
            "summary": "",
            "is_error": False,
        }

    return None


# --- Models ---

class OpenHandsConfigUpdate(BaseModel):
    mode: str = Field(..., description="'local' or 'cloud'")
    local_base_url: str | None = None
    local_api_key: str | None = None
    cloud_base_url: str | None = None
    cloud_api_key: str | None = None
    local: dict[str, Any] | None = None
    cloud: dict[str, Any] | None = None


class OpenHandsTestRequest(BaseModel):
    mode: str = "local"
    base_url: str = "http://127.0.0.1:8000"
    api_key: str = ""


class OpenHandsConversationCreate(BaseModel):
    initial_message: str | None = None
    repository: str | None = None
    title: str | None = None


class AgentChatRequest(BaseModel):
    message: str = Field(..., description="Message/prompt/hint to send to the agent")
    run: bool = Field(True, description="Whether to resume agent execution if stopped")


class AddAccountRequest(BaseModel):
    token: str = Field(..., description="GitHub Personal Access Token (classic ghp_... or fine-grained github_pat_...)")
    label: str | None = Field(None, description="Optional alias, e.g. 'Personal', 'Work', 'Deploy Bot'")
    set_active: bool = Field(True, description="Whether to make this account the active account")


class LaunchRequest(BaseModel):
    repo_name: str = Field(..., description="Target repo name (e.g. 'my-app' or 'owner/my-app')")
    spec_text: str = Field(..., description="Markdown project specification with ## Phase N sections")
    account_login: str | None = Field(None, description="Optional GitHub account login to use for launching")
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
Depends on: none
Create Next.js 14 project structure with TypeScript and Tailwind CSS.
- Responsive app shell with Collapsible Sidebar, Header, and Main Content canvas.
- Theme support (Dark/Light mode).
- Unit tests for navigation components with Jest and React Testing Library.

## Phase 2: Hierarchical Document Store & CRUD
Depends on: Phase 1
Implement full client-side state management layer with Zustand and LocalStorage persistence.
- Data models for Documents, Folders, and Metadata.
- Full CRUD operations: create, read, update, soft-delete, and restore from trash.
- Parent-child nesting operations and breadcrumb generators.
- Unit tests for store operations and edge cases.

## Phase 3: Interactive Modular Editor
Depends on: Phase 2
Build rich modular content editor with keyboard shortcuts and slash commands.
- Slash command menu (/h1, /todo, /bullet, /code, /quote).
- Markdown auto-formatting and keyboard navigation.
- Real-time auto-saving with debouncing to store.
- Component tests for editor interactions.

## Phase 4: Full-Text Search, Cover Images & Export
Depends on: Phase 3
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
Depends on: none
Set up Python FastAPI application with clean architecture.
- SQLAlchemy 2.0 async engine and SQLite/PostgreSQL support.
- Pydantic v2 schemas and base database models.
- Health check endpoints (/health, /ready, /metrics).
- Unit tests with pytest and testcontainers/sqlite.

## Phase 2: User Authentication & Tenant Management
Depends on: Phase 1
Implement secure authentication and user authorization layer.
- User registration, password hashing with bcrypt, and JWT token issuing.
- Role-based access control (Admin, Member, Viewer).
- Auth middleware and dependency injection for current_user.
- Pytest suite covering token expiry, invalid credentials, and roles.

## Phase 3: Core Domain CRUD & Background Tasks
Depends on: Phase 2
Implement core resource management and asynchronous processing.
- Full REST endpoints for Tasks and Workspaces (filtering, pagination, sorting).
- Background worker tasks for webhook dispatching and email notifications.
- Input validation and informative RFC 7807 error responses.
- API integration tests covering full lifecycle.

## Phase 4: OpenAPI Docs, Rate Limiting & Production Docker
Depends on: Phase 3
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
Depends on: none
Set up clean modular Python CLI application.
- Entrypoint structure with argparse and command dispatchers.
- Configuration file loader (.devpulse.json / YAML) with fallback defaults.
- Clean exit codes and stream routing (stdout for data, stderr for logs).
- Unit tests with pytest verifying argument parsing and flags.

## Phase 2: Repository Scanner & Metric Collector
Depends on: Phase 1
Build static analysis and git metric extraction engine.
- Git history analyzer (commits per author, churn rate, stale branches).
- Codebase statistics calculator (file types, SLOC, TODO comments).
- Fast concurrent directory scanning using multiprocessing or threads.
- Comprehensive tests using temporary git repositories.

## Phase 3: Rich Terminal Visualization & Interactive TUI
Depends on: Phase 2
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
        "github": {"connected": False, "user": None, "token_source": None, "accounts_count": 0, "active_login": None},
        "openhands": {"connected": False, "version": None, "usable_tools": []},
    }

    # 1. Test GitHub
    try:
        accounts = load_accounts()
        status["github"]["accounts_count"] = len(accounts)
        client = get_client()
        user_info = client.request("GET", "/user")
        status["github"]["connected"] = True
        status["github"]["token_source"] = "active_account" if accounts else "env_token"
        status["github"]["active_login"] = user_info.get("login")
        status["github"]["user"] = {
            "login": user_info.get("login"),
            "name": user_info.get("name") or user_info.get("login"),
            "avatar_url": user_info.get("avatar_url"),
            "public_repos": user_info.get("public_repos"),
            "html_url": user_info.get("html_url"),
        }
    except Exception as exc:
        status["github"]["error"] = str(exc)

    # 2. Test OpenHands
    mode, base_url, api_key = get_openhands_client_config()
    status["openhands"]["mode"] = mode
    status["openhands"]["base_url"] = base_url
    status["openhands"]["has_key"] = bool(api_key)
    try:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-Session-API-Key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"

        probe_endpoints = ["/server_info", "/api/v1/app-conversations?limit=1", "/alive", "/health"]
        connected = False
        last_exc = None
        for ep in probe_endpoints:
            try:
                req = urllib.request.Request(f"{base_url}{ep}", headers=headers)
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw) if raw else {}
                    status["openhands"]["connected"] = True
                    status["openhands"]["version"] = data.get("version") or data.get("sdk_version") or ("Cloud" if mode == "cloud" else "1.0.0")
                    status["openhands"]["title"] = data.get("title") or ("OpenHands Cloud" if mode == "cloud" else "OpenHands Agent")
                    status["openhands"]["usable_tools"] = data.get("usable_tools", [])
                    connected = True
                    break
            except Exception as e:
                last_exc = e
                continue
        if not connected:
            status["openhands"]["connected"] = False
            status["openhands"]["error"] = str(last_exc)
    except Exception as exc:
        status["openhands"]["connected"] = False
        status["openhands"]["error"] = str(exc)

    return status


# --- GitHub Accounts Management Endpoints ---

@app.get("/api/accounts")
def list_accounts():
    accounts = load_accounts()
    safe_accounts = []
    for acc in accounts:
        token = acc.get("token", "")
        masked = f"{token[:4]}...{token[-4:]}" if len(token) >= 8 else "***"
        safe_accounts.append({
            "login": acc.get("login"),
            "name": acc.get("name"),
            "avatar_url": acc.get("avatar_url"),
            "label": acc.get("label", ""),
            "masked_token": masked,
            "is_active": acc.get("is_active", False),
            "created_at": acc.get("created_at"),
            "public_repos": acc.get("public_repos", 0),
        })
    return safe_accounts


@app.post("/api/accounts")
def add_account(req: AddAccountRequest):
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="توکن گیت‌هاب نمی‌تواند خالی باشد.")

    # Validate token by querying GitHub /user
    try:
        client = GitHubClient(token=token)
        user_info = client.request("GET", "/user")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"توکن نامعتبر است یا امکان اتصال به گیت‌هاب وجود ندارد: {exc}"
        )

    login = user_info.get("login")
    if not login:
        raise HTTPException(status_code=400, detail="پاسخ گیت‌هاب فاقد اطلاعات کاربری بود.")

    accounts = load_accounts()

    if req.set_active:
        for a in accounts:
            a["is_active"] = False

    existing = next((a for a in accounts if a["login"].lower() == login.lower()), None)
    if existing:
        existing["token"] = token
        existing["name"] = user_info.get("name") or login
        existing["avatar_url"] = user_info.get("avatar_url")
        existing["public_repos"] = user_info.get("public_repos", 0)
        if req.label:
            existing["label"] = req.label
        if req.set_active:
            existing["is_active"] = True
    else:
        accounts.append({
            "login": login,
            "name": user_info.get("name") or login,
            "avatar_url": user_info.get("avatar_url"),
            "public_repos": user_info.get("public_repos", 0),
            "token": token,
            "label": req.label or "",
            "is_active": req.set_active or len(accounts) == 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    save_accounts(accounts)
    return {
        "status": "ok",
        "login": login,
        "name": user_info.get("name") or login,
        "avatar_url": user_info.get("avatar_url"),
    }


@app.post("/api/accounts/{login}/activate")
def activate_account(login: str):
    accounts = load_accounts()
    found = False
    for a in accounts:
        if a["login"].lower() == login.lower():
            a["is_active"] = True
            found = True
        else:
            a["is_active"] = False
    if not found:
        raise HTTPException(status_code=404, detail=f"اکانت '{login}' یافت نشد.")
    save_accounts(accounts)
    return {"status": "ok", "active_login": login}


@app.delete("/api/accounts/{login}")
def delete_account(login: str):
    accounts = load_accounts()
    new_accounts = [a for a in accounts if a["login"].lower() != login.lower()]
    if len(new_accounts) == len(accounts):
        raise HTTPException(status_code=404, detail=f"اکانت '{login}' یافت نشد.")

    # If deleted account was active, activate the first remaining account
    if new_accounts and not any(a.get("is_active") for a in new_accounts):
        new_accounts[0]["is_active"] = True

    save_accounts(new_accounts)
    return {"status": "ok", "deleted": login}


@app.post("/api/accounts/{login}/test")
def test_account(login: str):
    try:
        client = get_client(account_login=login)
        user_info = client.request("GET", "/user")
        return {
            "status": "ok",
            "valid": True,
            "login": user_info.get("login"),
            "name": user_info.get("name"),
            "public_repos": user_info.get("public_repos"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"خطا در اعتبارسنجی اتصال اکانت: {exc}")


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
@app.post("/api/track")
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
@app.post("/api/launch")
def launch_project(req: LaunchRequest):
    client = get_client(account_login=req.account_login)
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
                    client.request("POST", "/user/repos", body=create_payload)
                else:
                    org = repo.split("/")[0]
                    client.request("POST", f"/orgs/{org}/repos", body=create_payload)
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
@app.get("/api/pipeline/{owner}/{repo}")
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

    # Find matching OpenHands conversations if available
    conv_by_phase: dict[int, str] = {}
    try:
        raw_convs = openhands_request("GET", "/api/conversations/search", params={"limit": 25, "sort_order": "CREATED_AT_DESC"}, timeout=2.0)
        c_items = raw_convs.get("items", []) if isinstance(raw_convs, dict) else (raw_convs if isinstance(raw_convs, list) else [])
        for c in c_items:
            title = c.get("title", "")
            cid = c.get("id")
            if not cid:
                continue
            pm = re.search(r"Phase\s*(\d+)", title, re.IGNORECASE)
            if pm:
                p_num = int(pm.group(1))
                if p_num not in conv_by_phase:
                    conv_by_phase[p_num] = cid
    except Exception:
        pass

    for p in factory_phases:
        p["conversation_id"] = conv_by_phase.get(p["phase"])

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
@app.post("/api/pipeline/{owner}/{repo}/orchestrate")
def run_orchestrator(owner: str, repo: str):
    full_repo = f"{owner}/{repo}"
    client = get_client()
    try:
        res = check_and_transition(repo=full_repo, client=client)
        return {"status": "ok", "result": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{owner}/{repo}/heal")
@app.post("/api/pipeline/{owner}/{repo}/heal")
def run_healer(owner: str, repo: str):
    full_repo = f"{owner}/{repo}"
    client = get_client()
    try:
        res = scan_and_heal(repo=full_repo, client=client)
        return {"status": "ok", "result": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --- OpenHands Engine & Interactive Console Endpoints ---

@app.get("/api/openhands/config")
def get_openhands_config():
    cfg = load_openhands_config()
    local_key = cfg.get("local", {}).get("api_key") or get_default_local_key()
    cloud_key = cfg.get("cloud", {}).get("api_key", "")
    return {
        "mode": cfg.get("mode", "local"),
        "local": {
            "base_url": cfg.get("local", {}).get("base_url", "http://127.0.0.1:8000"),
            "has_key": bool(local_key),
            "masked_key": f"{local_key[:4]}...{local_key[-4:]}" if len(local_key) >= 8 else ("***" if local_key else "")
        },
        "cloud": {
            "base_url": cfg.get("cloud", {}).get("base_url", "https://app.all-hands.dev"),
            "has_key": bool(cloud_key),
            "masked_key": f"{cloud_key[:4]}...{cloud_key[-4:]}" if len(cloud_key) >= 8 else ("***" if cloud_key else "")
        }
    }


@app.post("/api/openhands/config")
def update_openhands_config(req: OpenHandsConfigUpdate):
    cfg = load_openhands_config()
    if req.mode in ("local", "cloud"):
        cfg["mode"] = req.mode

    # Flat or nested local settings
    local_url = req.local_base_url or (req.local.get("base_url") if req.local else None)
    if local_url:
        cfg["local"]["base_url"] = local_url.rstrip("/")

    local_key = req.local_api_key if req.local_api_key is not None else (req.local.get("api_key") if req.local else None)
    if local_key is not None and local_key.strip():
        cfg["local"]["api_key"] = local_key.strip()

    # Flat or nested cloud settings
    cloud_url = req.cloud_base_url or (req.cloud.get("base_url") if req.cloud else None)
    if cloud_url:
        cfg["cloud"]["base_url"] = cloud_url.rstrip("/")

    cloud_key = req.cloud_api_key if req.cloud_api_key is not None else (req.cloud.get("api_key") if req.cloud else None)
    if cloud_key is not None and cloud_key.strip():
        cfg["cloud"]["api_key"] = cloud_key.strip()

    save_openhands_config(cfg)
    return get_openhands_config()


@app.post("/api/openhands/test")
def test_openhands_connection(req: OpenHandsTestRequest):
    headers = {"Accept": "application/json"}
    if req.api_key:
        headers["X-Session-API-Key"] = req.api_key
        headers["Authorization"] = f"Bearer {req.api_key}"

    endpoints = [
        "/api/v1/app-conversations/search?limit=1",
        "/api/v1/app-conversations?limit=1",
        "/api/conversations/search?limit=1",
        "/api/conversations?limit=1",
        "/alive",
        "/",
    ]
    last_err = None
    for ep in endpoints:
        url = f"{req.base_url.rstrip('/')}{ep}"
        try:
            r = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(r, timeout=4.0) as resp:
                return {"status": "ok", "url": req.base_url}
        except urllib.error.HTTPError as he:
            if he.code in (200, 204, 301, 302, 307, 404):
                return {"status": "ok", "url": req.base_url}
            if he.code in (401, 403):
                return {"status": "error", "detail": f"Authentication failed (HTTP {he.code}): Please check your API key"}
            last_err = f"HTTP {he.code}: {he.reason}"
        except Exception as e:
            last_err = str(e)
    return {"status": "error", "detail": f"Cannot connect to {req.base_url}: {last_err}"}


@app.get("/api/openhands/conversations")
def list_openhands_conversations(limit: int = 25):
    try:
        res = None
        endpoints = [
            ("GET", "/api/v1/app-conversations/search", {"limit": limit, "sort_order": "CREATED_AT_DESC"}),
            ("GET", "/api/v1/app-conversations", {"limit": limit}),
            ("GET", "/api/conversations/search", {"limit": limit, "sort_order": "CREATED_AT_DESC"}),
            ("GET", "/api/conversations", {"limit": limit}),
        ]
        for method, ep, params in endpoints:
            try:
                res = openhands_request(method, ep, params=params)
                if res is not None:
                    break
            except HTTPException as he:
                if he.status_code in (404, 405):
                    continue
                raise
            except Exception:
                continue

        if isinstance(res, list):
            items = res
        elif isinstance(res, dict):
            items = res.get("items") or res.get("conversations") or res.get("app_conversations") or []
        else:
            items = []

        conversations = []
        seen_ids = set()
        for c in items:
            cid = c.get("id") or c.get("app_conversation_id") or c.get("conversation_id")
            if not cid:
                continue
            seen_ids.add(cid)
            title = c.get("title") or f"Conversation {cid[:5]}"
            status = c.get("execution_status") or c.get("status") or "running"
            created_at = c.get("created_at")
            repo = c.get("selected_repository")
            conv_url = c.get("conversation_url")
            sess_key = c.get("session_api_key")

            if conv_url:
                CLOUD_CONVERSATIONS_RUNTIME_MAP[cid] = {
                    "conversation_url": conv_url,
                    "session_api_key": sess_key or "",
                    "title": title,
                    "status": status,
                    "created_at": created_at,
                    "selected_repository": repo,
                }

            conversations.append({
                "id": cid,
                "title": title,
                "status": status,
                "created_at": created_at,
                "selected_repository": repo,
            })

        # Prepend any recent conversations that haven't been indexed by the backend yet
        now = time.time()
        for r_id, r_info in list(RECENT_CONVERSATIONS.items()):
            if now - r_info.get("_timestamp", now) > 600:
                RECENT_CONVERSATIONS.pop(r_id, None)
                continue
            if r_id not in seen_ids:
                conversations.insert(0, {
                    "id": r_id,
                    "title": r_info.get("title") or "گفتگوی جدید",
                    "status": r_info.get("status") or "starting",
                    "created_at": r_info.get("created_at"),
                    "selected_repository": r_info.get("selected_repository"),
                })
                seen_ids.add(r_id)

        return {"conversations": conversations}
    except Exception as exc:
        return {"conversations": [], "warning": str(exc), "connected": False}


@app.post("/api/openhands/conversations")
def create_openhands_conversation(req: OpenHandsConversationCreate):
    mode, base_url, api_key = get_openhands_client_config()
    res = None
    last_err = None

    if mode == "local":
        # 1. Local OpenHands (agent-server / Agent Canvas)
        workspace_dir = req.repository or str(ROOT_DIR)
        active_prof_id = None
        try:
            settings_res = openhands_request("GET", "/api/settings", timeout=2.5)
            active_prof_id = settings_res.get("active_agent_profile_id")
        except Exception:
            pass

        if not active_prof_id:
            try:
                prof_res = openhands_request("GET", "/api/agent-profiles", timeout=2.5)
                active_prof_id = prof_res.get("active_agent_profile_id")
                if not active_prof_id and prof_res.get("profiles"):
                    active_prof_id = prof_res["profiles"][0].get("id")
            except Exception:
                pass

        local_body: dict[str, Any] = {
            "workspace": {
                "working_dir": workspace_dir
            }
        }
        if active_prof_id:
            local_body["agent_profile_id"] = active_prof_id

        if req.initial_message:
            local_body["initial_message"] = {
                "role": "user",
                "content": [{"type": "text", "text": req.initial_message}]
            }

        try:
            res = openhands_request("POST", "/api/conversations", body=local_body)
        except Exception as err:
            last_err = err

    if not res:
        # 2. Cloud or V1 OpenHands app-conversations endpoint
        v1_body: dict[str, Any] = {}
        if req.repository:
            v1_body["selected_repository"] = req.repository
        if req.initial_message:
            v1_body["initial_message"] = {
                "content": [{"type": "text", "text": req.initial_message}]
            }
            v1_body["args"] = {"prompt": req.initial_message}

        try:
            res = openhands_request("POST", "/api/v1/app-conversations", body=v1_body)
        except HTTPException as he:
            last_err = he
            if he.status_code in (404, 405):
                legacy_body: dict[str, Any] = {}
                if req.repository:
                    legacy_body["selected_repository"] = req.repository
                if req.initial_message:
                    legacy_body["initial_message"] = req.initial_message
                if req.title:
                    legacy_body["title"] = req.title

                for ep in ["/api/conversations/init", "/api/conversations/new", "/api/conversations"]:
                    try:
                        res = openhands_request("POST", ep, body=legacy_body)
                        if res:
                            break
                    except Exception as e:
                        last_err = e

    if not res:
        if last_err:
            raise last_err
        raise HTTPException(status_code=500, detail="OpenHands did not accept conversation initialization")

    conv_id = res.get("app_conversation_id") or res.get("conversation_id") or res.get("id")
    if conv_id:
        res["id"] = conv_id
        res["conversation_id"] = conv_id
        title = req.title or (req.initial_message[:30] if req.initial_message else "گفتگوی جدید")
        status = res.get("status") or res.get("execution_status") or "starting"
        created_at = res.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        res["title"] = title
        res["status"] = status
        res["created_at"] = created_at
        res["selected_repository"] = req.repository
        RECENT_CONVERSATIONS[conv_id] = {
            "id": conv_id,
            "title": title,
            "status": status,
            "created_at": created_at,
            "selected_repository": req.repository,
            "_timestamp": time.time(),
        }
        if res.get("conversation_url"):
            CLOUD_CONVERSATIONS_RUNTIME_MAP[conv_id] = {
                "conversation_url": res["conversation_url"],
                "session_api_key": res.get("session_api_key") or "",
                "title": title,
                "status": status,
                "created_at": created_at,
                "selected_repository": req.repository,
            }

    # If an initial message was supplied and not auto-run, trigger event
    if conv_id and req.initial_message and not res.get("last_user_message_id") and not res.get("leaf_event_id"):
        try:
            event_payload = {
                "role": "user",
                "content": [{"type": "text", "text": req.initial_message}],
                "run": True,
            }
            openhands_request("POST", f"/api/conversations/{conv_id}/events", body=event_payload)
        except Exception:
            pass

    return res


@app.get("/api/openhands/conversations/{conversation_id}")
def get_openhands_conversation(conversation_id: str):
    if conversation_id in RECENT_CONVERSATIONS:
        r = RECENT_CONVERSATIONS[conversation_id]
        return {
            "id": conversation_id,
            "title": r.get("title") or "گفتگو",
            "status": r.get("status") or "running",
            "created_at": r.get("created_at"),
            "selected_repository": r.get("selected_repository"),
        }
    if conversation_id in CLOUD_CONVERSATIONS_RUNTIME_MAP:
        r = CLOUD_CONVERSATIONS_RUNTIME_MAP[conversation_id]
        return {
            "id": conversation_id,
            "title": r.get("title") or "گفتگو",
            "status": r.get("status") or "running",
            "created_at": r.get("created_at"),
            "selected_repository": r.get("selected_repository"),
        }

    res = None
    for ep in [f"/api/conversations/{conversation_id}", f"/api/v1/app-conversations/{conversation_id}"]:
        try:
            res = openhands_request("GET", ep)
            if res:
                break
        except HTTPException as he:
            if he.status_code in (404, 405):
                continue
            raise
        except Exception:
            continue

    if not res:
        list_res = list_openhands_conversations(limit=50)
        for c in list_res.get("conversations", []):
            if c["id"] == conversation_id:
                return c
        return {
            "id": conversation_id,
            "title": "گفتگو",
            "status": "running",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "selected_repository": None,
        }

    cid = res.get("id") or res.get("app_conversation_id") or conversation_id
    return {
        "id": cid,
        "title": res.get("title") or "گفتگو",
        "status": res.get("execution_status") or res.get("status") or "running",
        "created_at": res.get("created_at"),
        "selected_repository": res.get("selected_repository"),
    }


@app.get("/api/openhands/conversations/{conversation_id}/events")
def get_openhands_events(conversation_id: str, limit: int = 100):
    mode, base_url, api_key = get_openhands_client_config()
    res = None

    # 1. Cloud mode runtime pod resolution
    if mode == "cloud":
        runtime_info = CLOUD_CONVERSATIONS_RUNTIME_MAP.get(conversation_id)
        if not runtime_info or not runtime_info.get("conversation_url"):
            list_openhands_conversations(limit=25)
            runtime_info = CLOUD_CONVERSATIONS_RUNTIME_MAP.get(conversation_id)

        if runtime_info and runtime_info.get("conversation_url"):
            pod_url = runtime_info["conversation_url"].rstrip("/")
            pod_key = runtime_info.get("session_api_key") or api_key
            headers = {"Accept": "application/json"}
            if pod_key:
                headers["X-Session-API-Key"] = pod_key
                headers["Authorization"] = f"Bearer {pod_key}"
            try:
                search_url = f"{pod_url}/events/search?limit={limit}&sort_order=TIMESTAMP_DESC"
                req = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    raw = resp.read().decode("utf-8")
                    if raw and not raw.strip().startswith("<"):
                        res = json.loads(raw)
            except Exception:
                pass
        else:
            return {
                "conversation_id": conversation_id,
                "status": "starting",
                "total_events": 0,
                "events": [],
                "notice": "سندباکس ایجنت در حال راه‌اندازی است..."
            }

    # 2. Local mode or standard routing
    if res is None:
        for ep in [
            f"/api/conversations/{conversation_id}/events/search",
            f"/api/conversations/{conversation_id}/events",
            f"/api/v1/app-conversations/{conversation_id}/events/search",
            f"/api/v1/app-conversations/{conversation_id}/events",
        ]:
            try:
                res = openhands_request(
                    "GET",
                    ep,
                    params={"limit": limit, "sort_order": "TIMESTAMP_DESC"},
                    timeout=3.0
                )
                if res is not None:
                    break
            except HTTPException as he:
                if he.status_code in (404, 405):
                    continue
                raise
            except Exception:
                continue

    raw_items = []
    if isinstance(res, dict):
        raw_items = res.get("items") or res.get("events") or []
    elif isinstance(res, list):
        raw_items = res
    raw_items.reverse()

    parsed_events = []
    for item in raw_items:
        p = parse_openhands_event(item)
        if p:
            parsed_events.append(p)

    status = "running"
    if isinstance(res, dict) and res.get("status"):
        status = res.get("status")

    return {
        "conversation_id": conversation_id,
        "status": status,
        "total_events": len(parsed_events),
        "events": parsed_events,
    }


@app.post("/api/openhands/conversations/{conversation_id}/chat")
def send_openhands_chat(conversation_id: str, req: AgentChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="پیام نمی‌تواند خالی باشد.")

    mode, base_url, api_key = get_openhands_client_config()
    payload = {
        "role": "user",
        "content": [{"type": "text", "text": req.message}],
        "run": req.run,
    }
    res = None
    runtime_info = None

    # 1. Cloud mode direct pod delivery
    if mode == "cloud":
        runtime_info = CLOUD_CONVERSATIONS_RUNTIME_MAP.get(conversation_id)
        if not runtime_info or not runtime_info.get("conversation_url"):
            for _ in range(3):
                list_openhands_conversations(limit=25)
                runtime_info = CLOUD_CONVERSATIONS_RUNTIME_MAP.get(conversation_id)
                if runtime_info and runtime_info.get("conversation_url"):
                    break
                time.sleep(1.0)

        if runtime_info and runtime_info.get("conversation_url"):
            pod_url = runtime_info["conversation_url"].rstrip("/")
            pod_key = runtime_info.get("session_api_key") or api_key
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if pod_key:
                headers["X-Session-API-Key"] = pod_key
                headers["Authorization"] = f"Bearer {pod_key}"
            try:
                chat_url = f"{pod_url}/events"
                data_bytes = json.dumps(payload).encode("utf-8")
                req_obj = urllib.request.Request(chat_url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req_obj, timeout=5.0) as resp:
                    raw = resp.read().decode("utf-8")
                    res = json.loads(raw) if raw and not raw.strip().startswith("<") else {"success": True}
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to deliver message to cloud pod: {e}")

    # 2. Local mode or fallback
    if res is None:
        for ep in [
            f"/api/conversations/{conversation_id}/events",
            f"/api/v1/app-conversations/{conversation_id}/events",
            f"/api/v1/app-conversations/{conversation_id}/send-message",
        ]:
            try:
                res = openhands_request("POST", ep, body=payload)
                if res is not None:
                    break
            except HTTPException as he:
                if he.status_code in (404, 405):
                    continue
                raise
            except Exception:
                continue

    # Crucial: Trigger run loop if requested
    if req.run and res is not None:
        if mode == "cloud" and runtime_info and runtime_info.get("conversation_url"):
            try:
                pod_url = runtime_info["conversation_url"].rstrip("/")
                pod_key = runtime_info.get("session_api_key") or api_key
                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                if pod_key:
                    headers["X-Session-API-Key"] = pod_key
                    headers["Authorization"] = f"Bearer {pod_key}"
                r_obj = urllib.request.Request(f"{pod_url}/run", data=b"{}", headers=headers, method="POST")
                urllib.request.urlopen(r_obj, timeout=3.0)
            except Exception:
                pass
        else:
            for run_ep in [
                f"/api/conversations/{conversation_id}/run",
                f"/api/v1/app-conversations/{conversation_id}/run",
            ]:
                try:
                    openhands_request("POST", run_ep, body={})
                    break
                except Exception:
                    pass

    return {"status": "sent", "response": res}


@app.post("/api/openhands/conversations/{conversation_id}/interrupt")
def interrupt_openhands_conversation(conversation_id: str):
    res = None
    for ep in [
        f"/api/conversations/{conversation_id}/interrupt",
        f"/api/v1/app-conversations/{conversation_id}/interrupt",
    ]:
        try:
            res = openhands_request("POST", ep, body={})
            if res is not None:
                break
        except HTTPException as he:
            if he.status_code in (404, 405):
                continue
            raise
    return {"status": "interrupted", "result": res}


@app.post("/api/openhands/conversations/{conversation_id}/run")
def resume_openhands_conversation(conversation_id: str):
    res = None
    for ep in [
        f"/api/conversations/{conversation_id}/run",
        f"/api/v1/app-conversations/{conversation_id}/run",
    ]:
        try:
            res = openhands_request("POST", ep, body={})
            if res is not None:
                break
        except HTTPException as he:
            if he.status_code in (404, 405):
                continue
            raise
    return {"status": "resumed", "result": res}


@app.get("/api/projects/{owner}/{repo}/conversation")
def get_project_conversation(owner: str, repo: str, phase: int | None = None):
    try:
        res = openhands_request("GET", "/api/conversations/search", params={"limit": 30, "sort_order": "CREATED_AT_DESC"})
        items = res.get("items", []) if isinstance(res, dict) else []

        target_phase_str = f"Phase {phase}" if phase is not None else None

        # 1. Match phase
        if target_phase_str:
            for c in items:
                title = c.get("title", "")
                if target_phase_str.lower() in title.lower():
                    return {"found": True, "conversation": c}

        # 2. Match repo name
        for c in items:
            title = c.get("title", "")
            selected_repo = c.get("selected_repository", "")
            if repo.lower() in title.lower() or (selected_repo and repo.lower() in selected_repo.lower()):
                return {"found": True, "conversation": c}

        # 3. Return latest if exists
        if items:
            return {"found": True, "conversation": items[0]}

        return {"found": False, "conversation": None}
    except Exception as exc:
        return {"found": False, "error": str(exc), "conversation": None}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Starting OpenHands Software Factory Dashboard on http://localhost:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False, app_dir=str(DASHBOARD_DIR))
