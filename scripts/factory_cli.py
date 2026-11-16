#!/usr/bin/env python3
"""Open Factory CLI & Bootstrapper.

Developer-friendly CLI to bootstrap, run, and manage the autonomous software factory:
- Preflight checks (Python, uv, Node/npx, gh, Docker)
- Zero-friction GitHub authentication (via gh CLI or interactive PAT)
- OpenHands skill injection (automatically installs & enables ali-factory in OpenHands)
- Local/Cloud OpenHands engine management
- Dashboard server management (port 8080)
- Browser auto-launcher
- Project status and real-time diagnostics
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DASHBOARD_DIR = REPO_ROOT / "dashboard"
LOGS_DIR = REPO_ROOT / ".factory_logs"
OPENHANDS_DIR = Path.home() / ".openhands"
OPENHANDS_SKILLS_DIR = OPENHANDS_DIR / "skills"
OPENHANDS_INSTALLED_DIR = OPENHANDS_SKILLS_DIR / "installed"
OPENHANDS_SETTINGS_FILE = OPENHANDS_DIR / "settings.json"

def load_dotenv(env_path: Path | None = None):
    """Zero-dependency .env loader that populates os.environ."""
    if env_path is None:
        env_path = REPO_ROOT / ".env"
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

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
BLUE = "\033[34m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"


def print_banner():
    banner = f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗
║               🚀 OPEN FACTORY BOOTSTRAPPER                  ║
║         Autonomous Software Factory & Agent Console          ║
╚══════════════════════════════════════════════════════════════╝{RESET}
"""
    print(banner)


def ensure_dirs():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    OPENHANDS_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    OPENHANDS_INSTALLED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
def run_preflight_checks() -> dict[str, bool]:
    print(f"{BOLD}[1/5] Checking Developer Environment & Prerequisites...{RESET}")
    results = {}

    # Python version >= 3.10
    py_ok = sys.version_info >= (3, 10)
    results["python"] = py_ok
    if py_ok:
        print(f"  {GREEN}✓{RESET} Python {sys.version.split()[0]}")
    else:
        print(f"  {RED}✗{RESET} Python >= 3.10 required (found {sys.version.split()[0]})")

    # uv package manager
    uv_path = shutil.which("uv")
    results["uv"] = bool(uv_path)
    if uv_path:
        print(f"  {GREEN}✓{RESET} uv package manager ({uv_path})")
    else:
        print(f"  {YELLOW}⚠{RESET} uv not found. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh")

    # gh CLI
    gh_path = shutil.which("gh")
    results["gh"] = bool(gh_path)
    if gh_path:
        print(f"  {GREEN}✓{RESET} GitHub CLI ({gh_path})")
    else:
        print(f"  {YELLOW}⚠{RESET} GitHub CLI (gh) not found (optional, recommended for auto-login)")

    # Node / npx (for local agent-canvas)
    npx_path = shutil.which("npx")
    results["npx"] = bool(npx_path)
    if npx_path:
        print(f"  {GREEN}✓{RESET} Node.js npx runner ({npx_path})")
    else:
        print(f"  {YELLOW}⚠{RESET} npx not found. Required for local OpenHands Agent Canvas.")

    # Docker
    docker_path = shutil.which("docker")
    docker_running = False
    if docker_path:
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
            docker_running = (res.returncode == 0)
        except Exception:
            docker_running = False

    results["docker"] = docker_running
    if docker_running:
        print(f"  {GREEN}✓{RESET} Docker daemon running")
    elif docker_path:
        print(f"  {YELLOW}⚠{RESET} Docker installed but daemon is not running (needed for sandbox containers)")
    else:
        print(f"  {DIM}○{RESET} Docker not found (optional if using external/cloud runners)")

    return results


# ---------------------------------------------------------------------------
# GitHub Authentication
# ---------------------------------------------------------------------------
def get_github_user_info(token: str) -> dict | None:
    """Validate a GitHub token and return the user profile dict."""
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "OpenFactory-CLI",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def setup_github_authentication(interactive: bool = True) -> str | None:
    print(f"\n{BOLD}[2/5] Setting up GitHub Authentication...{RESET}")
    accounts_file = DASHBOARD_DIR / "accounts.json"
    existing_accounts: list[dict] = []
    if accounts_file.exists():
        try:
            existing_accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
        except Exception:
            existing_accounts = []

    # 1. Check if there's already an active valid account
    for acc in existing_accounts:
        if acc.get("is_active") and acc.get("token"):
            tok = acc["token"]
            user = get_github_user_info(tok)
            if user:
                print(f"  {GREEN}✓{RESET} Active GitHub account: {BOLD}@{user.get('login')}{RESET} ({user.get('name') or 'Developer'})")
                os.environ["GITHUB_TOKEN"] = tok
                os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = tok
                return tok

    # 2. Try zero-touch auto-detection via `gh auth token`
    gh_token = None
    if shutil.which("gh"):
        try:
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                candidate = res.stdout.strip()
                user = get_github_user_info(candidate)
                if user:
                    gh_token = candidate
                    print(f"  {GREEN}✓{RESET} Auto-detected GitHub credentials via {CYAN}gh CLI{RESET}: {BOLD}@{user.get('login')}{RESET}")
                    # Save into accounts.json as default active
                    new_acc = {
                        "login": user.get("login"),
                        "name": user.get("name") or user.get("login"),
                        "avatar_url": user.get("avatar_url") or "",
                        "token": gh_token,
                        "label": "Auto-detected (gh CLI)",
                        "is_active": True,
                        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                    # Deactivate others
                    for a in existing_accounts:
                        a["is_active"] = False
                    existing_accounts.insert(0, new_acc)
                    accounts_file.write_text(json.dumps(existing_accounts, indent=2), encoding="utf-8")
                    os.environ["GITHUB_TOKEN"] = gh_token
                    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = gh_token
                    return gh_token
        except Exception:
            pass

    # 3. Check environment variables
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if env_token:
        user = get_github_user_info(env_token)
        if user:
            print(f"  {GREEN}✓{RESET} Using GitHub token from environment: {BOLD}@{user.get('login')}{RESET}")
            return env_token

    # 4. Interactive fallback if in terminal
    if interactive and sys.stdin.isatty():
        print(f"  {YELLOW}!{RESET} No active GitHub token detected.")
        print(f"    Please enter a GitHub Personal Access Token (classic with 'repo' scope):")
        try:
            token_input = input("    GitHub PAT: ").strip()
            if token_input:
                user = get_github_user_info(token_input)
                if user:
                    print(f"  {GREEN}✓{RESET} Token verified! Logged in as {BOLD}@{user.get('login')}{RESET}")
                    new_acc = {
                        "login": user.get("login"),
                        "name": user.get("name") or user.get("login"),
                        "avatar_url": user.get("avatar_url") or "",
                        "token": token_input,
                        "label": "Personal Access Token",
                        "is_active": True,
                        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                    for a in existing_accounts:
                        a["is_active"] = False
                    existing_accounts.insert(0, new_acc)
                    accounts_file.write_text(json.dumps(existing_accounts, indent=2), encoding="utf-8")
                    os.environ["GITHUB_TOKEN"] = token_input
                    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = token_input
                    return token_input
                else:
                    print(f"  {RED}✗{RESET} Token is invalid or expired.")
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")

    print(f"  {YELLOW}⚠{RESET} Running without GitHub authentication (read-only or local spec mode).")
    return None


# ---------------------------------------------------------------------------
# ali-factory Skill Injection into OpenHands
# ---------------------------------------------------------------------------
def sync_ali_factory_skill() -> bool:
    """Installs or syncs the ali-factory skill into OpenHands.
    
    Copies SKILL.md, README.md, commands/, scripts/, and templates/ into:
      1) ~/.openhands/skills/ali-factory/
      2) ~/.openhands/skills/installed/ali-factory/
    Updates ~/.openhands/skills/installed/.installed.json (enabled: true)
    Updates ~/.openhands/settings.json (load_user_skills: true, load_project_skills: true, enabled_skills)
    """
    print(f"\n{BOLD}[3/5] Injecting 'ali-factory' Skill into OpenHands...{RESET}")
    ensure_dirs()

    source_skill_file = REPO_ROOT / "SKILL.md"
    if not source_skill_file.exists():
        print(f"  {RED}✗{RESET} Source SKILL.md not found in {REPO_ROOT}")
        return False

    targets = [
        OPENHANDS_SKILLS_DIR / "ali-factory",
        OPENHANDS_INSTALLED_DIR / "ali-factory",
    ]

    files_to_copy = ["SKILL.md", "README.md"]
    dirs_to_copy = ["commands", "scripts", "templates"]

    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        # Copy files
        for fname in files_to_copy:
            src = REPO_ROOT / fname
            if src.exists():
                shutil.copy2(src, target / fname)
        # Copy dirs
        for dname in dirs_to_copy:
            src_dir = REPO_ROOT / dname
            dst_dir = target / dname
            if src_dir.exists():
                if dst_dir.exists():
                    shutil.rmtree(dst_dir)
                shutil.copytree(src_dir, dst_dir)

    print(f"  {GREEN}✓{RESET} Synced skill files to {CYAN}~/.openhands/skills/ali-factory/{RESET}")

    # Update ~/.openhands/skills/installed/.installed.json
    installed_json_path = OPENHANDS_INSTALLED_DIR / ".installed.json"
    installed_data = {"extensions": {}}
    if installed_json_path.exists():
        try:
            installed_data = json.loads(installed_json_path.read_text(encoding="utf-8"))
        except Exception:
            installed_data = {"extensions": {}}

    installed_data.setdefault("extensions", {})
    installed_data["extensions"]["ali-factory"] = {
        "name": "ali-factory",
        "version": "1.0.0",
        "description": "Set up an autonomous OpenHands 'ali-factory' on GitHub with requirements planning (DAG decomposition), automated label and phase transition orchestration, and PR auto-healing.",
        "enabled": True,
        "source": "local",
        "requested_ref": None,
        "resolved_ref": None,
        "repo_path": None,
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "install_path": str(OPENHANDS_INSTALLED_DIR / "ali-factory"),
    }
    installed_json_path.write_text(json.dumps(installed_data, indent=2), encoding="utf-8")
    print(f"  {GREEN}✓{RESET} Registered in OpenHands installed extensions ({CYAN}.installed.json{RESET})")

    # Update ~/.openhands/settings.json if it exists
    if OPENHANDS_SETTINGS_FILE.exists():
        try:
            settings_data = json.loads(OPENHANDS_SETTINGS_FILE.read_text(encoding="utf-8"))
            agent_settings = settings_data.setdefault("agent_settings", {})
            agent_ctx = agent_settings.setdefault("agent_context", {})
            agent_ctx["load_user_skills"] = True
            agent_ctx["load_project_skills"] = True

            misc_settings = settings_data.setdefault("misc_settings", {})
            app_pref = misc_settings.setdefault("app_preferences", {})
            enabled_skills = app_pref.setdefault("enabled_skills", [])
            if "ali-factory" not in enabled_skills:
                enabled_skills.append("ali-factory")

            # Remove from disabled if present
            disabled_skills = app_pref.setdefault("disabled_skills", [])
            if "ali-factory" in disabled_skills:
                disabled_skills.remove("ali-factory")

            OPENHANDS_SETTINGS_FILE.write_text(json.dumps(settings_data, indent=2), encoding="utf-8")
            print(f"  {GREEN}✓{RESET} Updated OpenHands settings ({CYAN}load_user_skills=True{RESET}, {CYAN}enabled_skills += ali-factory{RESET})")
        except Exception as e:
            print(f"  {YELLOW}⚠{RESET} Warning while updating settings.json: {e}")

    return True


def inject_skill_into_repo(repo_path_str: str) -> bool:
    """Injects ali-factory skill into a project repository (.openhands/skills/ali-factory)."""
    repo_path = Path(repo_path_str).resolve()
    if not repo_path.exists():
        print(f"{RED}Error: Repository path {repo_path} does not exist.{RESET}")
        return False

    target = repo_path / ".openhands" / "skills" / "ali-factory"
    target.mkdir(parents=True, exist_ok=True)

    files_to_copy = ["SKILL.md", "README.md"]
    dirs_to_copy = ["commands", "scripts", "templates"]

    for fname in files_to_copy:
        src = REPO_ROOT / fname
        if src.exists():
            shutil.copy2(src, target / fname)
    for dname in dirs_to_copy:
        src_dir = REPO_ROOT / dname
        dst_dir = target / dname
        if src_dir.exists():
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.copytree(src_dir, dst_dir)

    print(f"{GREEN}✓ Successfully injected 'ali-factory' into: {BOLD}{target}{RESET}")
    return True


# ---------------------------------------------------------------------------
# OpenHands Engine Management
# ---------------------------------------------------------------------------
def free_ports(*ports: int):
    """Cleanly terminates any lingering or orphaned processes bound to the given ports."""
    for port in ports:
        try:
            res = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                for pid_str in res.stdout.strip().split():
                    try:
                        pid = int(pid_str)
                        os.kill(pid, signal.SIGTERM)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            subprocess.run(["fuser", "-k", "-TERM", f"{port}/tcp"], capture_output=True, timeout=2)
        except Exception:
            pass
    time.sleep(0.5)


def check_openhands_health(base_url: str = "http://127.0.0.1:8000") -> bool:
    """Check if OpenHands API is reachable and alive."""
    endpoints = ["/server_info", "/health", "/alive", "/api/options", "/"]
    for ep in endpoints:
        url = f"{base_url.rstrip('/')}{ep}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpenFactory-CLI"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status in (200, 204, 301, 302, 307, 308, 401, 403):
                    return True
        except urllib.error.HTTPError as e:
            # 401 / 403 / 404 indicates server is responding
            if e.code in (401, 403, 404):
                return True
        except Exception:
            pass
    return False


def get_openhands_config() -> dict:
    cfg_file = DASHBOARD_DIR / "openhands_config.json"
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Default config
    default_cfg = {
        "mode": "local",
        "local": {
            "base_url": "http://127.0.0.1:8000",
            "api_key": ""
        },
        "cloud": {
            "base_url": "https://app.all-hands.dev",
            "api_key": ""
        }
    }
    cfg_file.write_text(json.dumps(default_cfg, indent=2), encoding="utf-8")
    return default_cfg


def launch_openhands_engine() -> bool:
    print(f"\n{BOLD}[4/5] Checking OpenHands Engine...{RESET}")
    config = get_openhands_config()
    mode = config.get("mode", "local")

    if mode == "cloud":
        cloud_url = config.get("cloud", {}).get("base_url", "https://app.all-hands.dev")
        print(f"  {CYAN}○{RESET} Configured for OpenHands Cloud ({cloud_url})")
        if not config.get("cloud", {}).get("api_key"):
            print(f"  {YELLOW}⚠{RESET} Cloud API key is empty. Configure it in dashboard or openhands_config.json.")
        return True

    # Local mode
    local_url = config.get("local", {}).get("base_url", "http://127.0.0.1:8000")
    if check_openhands_health(local_url):
        print(f"  {GREEN}✓{RESET} OpenHands Engine is already running ({local_url})")
        return True

    # Free conflicting stale ports (8000, 18000, 18001) before starting
    free_ports(8000, 18000, 18001)

    cmd = None
    if shutil.which("agent-canvas"):
        cmd = ["agent-canvas"]
    elif shutil.which("npx"):
        cmd = ["npx", "@openhands/agent-canvas"]
    else:
        print(f"  {RED}✗{RESET} Cannot launch OpenHands: neither 'agent-canvas' nor 'npx' is installed.")
        return False

    print(f"  {CYAN}▶{RESET} Starting OpenHands Agent Canvas on port 8000 in background...")
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

    oh_log_file = open(LOGS_DIR / "openhands.log", "a")
    proc = subprocess.Popen(
        cmd,
        stdout=oh_log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setpgrp,
        env=env,
    )
    (LOGS_DIR / "openhands.pid").write_text(str(proc.pid))

    # Wait for OpenHands to come alive (typically ~10-15s)
    print("  Waiting for OpenHands server to start", end="", flush=True)
    for _ in range(40):
        time.sleep(1)
        print(".", end="", flush=True)
        if check_openhands_health(local_url):
            print(f"\n  {GREEN}✓{RESET} OpenHands Engine started successfully ({local_url})!")
            return True

    print(f"\n  {YELLOW}⚠{RESET} OpenHands launched (PID {proc.pid}), still initializing. Check {LOGS_DIR / 'openhands.log'}")
    return True


# ---------------------------------------------------------------------------
# Dashboard Server Management
# ---------------------------------------------------------------------------
def check_dashboard_health(port: int = 8080) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/api/projects"
        req = urllib.request.Request(url, headers={"User-Agent": "OpenFactory-CLI"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_dashboard(port: int = 8080) -> bool:
    print(f"\n{BOLD}[5/5] Checking Open Factory Dashboard...{RESET}")
    if check_dashboard_health(port):
        print(f"  {GREEN}✓{RESET} Dashboard is already running on http://127.0.0.1:{port}")
        return True

    uv_cmd = shutil.which("uv")
    cmd = []
    if uv_cmd:
        cmd = ["uv", "run", "--with", "fastapi", "--with", "uvicorn", "python3", "dashboard/app.py"]
    else:
        try:
            import fastapi, uvicorn
        except ImportError:
            print(f"  {CYAN}▶{RESET} Installing required dependencies (fastapi, uvicorn)...")
            req_file = REPO_ROOT / "requirements.txt"
            if req_file.exists():
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)], check=False)
            else:
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "fastapi", "uvicorn"], check=False)
        cmd = [sys.executable, "dashboard/app.py"]

    print(f"  {CYAN}▶{RESET} Starting Dashboard server on port {port} in background...")
    dash_log_file = open(LOGS_DIR / "dashboard.log", "a")
    dash_env = os.environ.copy()
    dash_env["PORT"] = str(port)
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=dash_env,
        stdout=dash_log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setpgrp,
    )
    (LOGS_DIR / "dashboard.pid").write_text(str(proc.pid))
    (LOGS_DIR / "dashboard.port").write_text(str(port))

    # Wait for dashboard to become healthy
    print("  Waiting for dashboard server", end="", flush=True)
    for _ in range(15):
        time.sleep(0.5)
        print(".", end="", flush=True)
        if check_dashboard_health(port):
            print(f"\n  {GREEN}✓{RESET} Dashboard started successfully on {BOLD}http://localhost:{port}{RESET}!")
            return True

    print(f"\n  {YELLOW}⚠{RESET} Dashboard started (PID {proc.pid}). Check logs at {LOGS_DIR / 'dashboard.log'}")
    return True


# ---------------------------------------------------------------------------
# Stop / Down Stack
# ---------------------------------------------------------------------------
def stop_process(pid_file_name: str, name: str):
    pid_file = LOGS_DIR / pid_file_name
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"  {GREEN}✓{RESET} Stopped {name} (PID {pid})")
        except ProcessLookupError:
            print(f"  {DIM}○{RESET} {name} was not running.")
        except Exception as e:
            print(f"  {YELLOW}⚠{RESET} Could not stop {name}: {e}")
        finally:
            pid_file.unlink(missing_ok=True)


def stop_stack():
    print(f"{BOLD}Stopping Open Factory stack...{RESET}")
    stop_process("dashboard.pid", "Dashboard Server")
    stop_process("openhands.pid", "OpenHands Agent Canvas")
    active_port = int(os.environ.get("PORT", 8080))
    if (LOGS_DIR / "dashboard.port").exists():
        try:
            active_port = int((LOGS_DIR / "dashboard.port").read_text().strip())
        except Exception:
            pass
    free_ports(active_port, 8080, 8000, 18000, 18001)
    print(f"{GREEN}✓ All services stopped.{RESET}")



# ---------------------------------------------------------------------------
# Status Command
# ---------------------------------------------------------------------------
def print_status():
    print_banner()
    print(f"{BOLD}STACK STATUS & DIAGNOSTICS:{RESET}\n")

    # 1. Dashboard
    active_port = int(os.environ.get("PORT", 8080))
    if (LOGS_DIR / "dashboard.port").exists():
        try:
            active_port = int((LOGS_DIR / "dashboard.port").read_text().strip())
        except Exception:
            pass
    dash_ok = check_dashboard_health(active_port)
    dash_pid = (LOGS_DIR / "dashboard.pid").read_text().strip() if (LOGS_DIR / "dashboard.pid").exists() else None
    if dash_ok:
        print(f"  • {BOLD}Dashboard Server:{RESET}      {GREEN}RUNNING{RESET} on http://localhost:{active_port} {DIM}(PID: {dash_pid or 'active'}){RESET}")
    else:
        print(f"  • {BOLD}Dashboard Server:{RESET}      {RED}STOPPED{RESET} (port {active_port})")

    # 2. OpenHands Engine
    oh_cfg = get_openhands_config()
    mode = oh_cfg.get("mode", "local")
    if mode == "local":
        local_url = oh_cfg.get("local", {}).get("base_url", "http://127.0.0.1:8000")
        oh_ok = check_openhands_health(local_url)
        oh_pid = (LOGS_DIR / "openhands.pid").read_text().strip() if (LOGS_DIR / "openhands.pid").exists() else None
        if oh_ok:
            print(f"  • {BOLD}OpenHands Engine:{RESET}      {GREEN}RUNNING (Local){RESET} on {local_url} {DIM}(PID: {oh_pid or 'active'}){RESET}")
        else:
            print(f"  • {BOLD}OpenHands Engine:{RESET}      {RED}STOPPED (Local){RESET} on {local_url}")
    else:
        cloud_url = oh_cfg.get("cloud", {}).get("base_url", "https://app.all-hands.dev")
        print(f"  • {BOLD}OpenHands Engine:{RESET}      {CYAN}CLOUD MODE{RESET} ({cloud_url})")

    # 3. ali-factory Skill
    skill_installed = (OPENHANDS_SKILLS_DIR / "ali-factory" / "SKILL.md").exists()
    settings_load_user = False
    if OPENHANDS_SETTINGS_FILE.exists():
        try:
            sdata = json.loads(OPENHANDS_SETTINGS_FILE.read_text(encoding="utf-8"))
            settings_load_user = sdata.get("agent_settings", {}).get("agent_context", {}).get("load_user_skills", False)
        except Exception:
            pass

    if skill_installed and settings_load_user:
        print(f"  • {BOLD}ali-factory Skill:{RESET}     {GREEN}INSTALLED & ACTIVE{RESET} in OpenHands (~/.openhands/skills/ali-factory)")
    elif skill_installed:
        print(f"  • {BOLD}ali-factory Skill:{RESET}     {YELLOW}FILES PRESENT{RESET} (load_user_skills is currently {settings_load_user})")
    else:
        print(f"  • {BOLD}ali-factory Skill:{RESET}     {RED}NOT INSTALLED{RESET} (run: ./factory skill sync)")

    # 4. GitHub Account
    accounts_file = DASHBOARD_DIR / "accounts.json"
    active_acc = None
    if accounts_file.exists():
        try:
            accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
            for a in accounts:
                if a.get("is_active"):
                    active_acc = a
                    break
        except Exception:
            pass

    if active_acc:
        print(f"  • {BOLD}GitHub Account:{RESET}        {GREEN}AUTHENTICATED{RESET} as @{active_acc.get('login')} {DIM}({active_acc.get('name')}){RESET}")
    else:
        print(f"  • {BOLD}GitHub Account:{RESET}        {YELLOW}NONE CONFIGURED{RESET} (run: ./factory up)")

    # 5. Tracked Projects
    projects_file = DASHBOARD_DIR / "projects.json"
    project_count = 0
    if projects_file.exists():
        try:
            projects = json.loads(projects_file.read_text(encoding="utf-8"))
            project_count = len(projects)
        except Exception:
            pass
    print(f"  • {BOLD}Tracked Projects:{RESET}      {CYAN}{project_count} project(s){RESET} registered in dashboard\n")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Open Factory CLI & Bootstrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # 'up' / 'start'
    default_port = int(os.environ.get("PORT", 8080))
    p_up = subparsers.add_parser("up", help="Bootstrap and start the complete Open Factory stack")
    p_up.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    p_up.add_argument("--port", type=int, default=default_port, help=f"Dashboard port (default: {default_port})")

    # 'down' / 'stop'
    subparsers.add_parser("down", help="Stop running Open Factory services")
    subparsers.add_parser("stop", help="Alias for 'down'")

    # 'status'
    subparsers.add_parser("status", help="Show real-time health and diagnostic status")

    # 'skill'
    p_skill = subparsers.add_parser("skill", help="Manage ali-factory skill injection")
    skill_sub = p_skill.add_subparsers(dest="skill_action", help="Skill action")
    skill_sub.add_parser("sync", help="Sync ali-factory skill to ~/.openhands/skills")
    p_inject = skill_sub.add_parser("inject", help="Inject ali-factory skill into target repository")
    p_inject.add_argument("repo_path", help="Path to target repository folder")

    # 'logs'
    p_logs = subparsers.add_parser("logs", help="View service logs")
    p_logs.add_argument("service", choices=["dashboard", "openhands"], default="dashboard", nargs="?")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output")

    args = parser.parse_args()

    # Default to 'up' if no command given
    cmd = args.command or "up"

    if cmd in ("up", "start"):
        print_banner()
        run_preflight_checks()
        setup_github_authentication(interactive=True)
        sync_ali_factory_skill()
        launch_openhands_engine()
        port = getattr(args, "port", 8080)
        launch_dashboard(port=port)

        dashboard_url = f"http://localhost:{port}"
        print(f"\n{GREEN}{BOLD}════════════════════════════════════════════════════════════════{RESET}")
        print(f" {GREEN}{BOLD}🎉 Open Factory is up and ready!{RESET}")
        print(f"    • Dashboard:  {BOLD}{dashboard_url}{RESET}")
        print(f"    • OpenHands:  {BOLD}http://localhost:8000{RESET}")
        print(f"    • Skill:      {BOLD}ali-factory (injected into OpenHands){RESET}")
        print(f"{GREEN}{BOLD}════════════════════════════════════════════════════════════════{RESET}\n")

        if not getattr(args, "no_browser", False):
            print(f"Opening browser at {dashboard_url}...")
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass

    elif cmd in ("down", "stop"):
        stop_stack()

    elif cmd == "status":
        print_status()

    elif cmd == "skill":
        action = getattr(args, "skill_action", None) or "sync"
        if action == "sync":
            sync_ali_factory_skill()
        elif action == "inject":
            inject_skill_into_repo(args.repo_path)

    elif cmd == "logs":
        log_file = LOGS_DIR / f"{args.service}.log"
        if not log_file.exists():
            print(f"{YELLOW}Log file {log_file} does not exist yet.{RESET}")
            return
        if getattr(args, "follow", False):
            try:
                subprocess.run(["tail", "-f", str(log_file)])
            except KeyboardInterrupt:
                pass
        else:
            try:
                subprocess.run(["tail", "-n", "100", str(log_file)])
            except Exception as e:
                print(log_file.read_text())


if __name__ == "__main__":
    main()
