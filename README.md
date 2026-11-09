# Open Factory (Autonomous Software Factory v2)

[![OpenHands](https://img.shields.io/badge/OpenHands-Agent%20SDK-6366f1.svg)](https://github.com/All-Hands-AI/OpenHands)
[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-green.svg)]()

> **An autonomous, end-to-end AI Software Factory powered by OpenHands and GitHub.**  
> Describe your feature or project spec once—Open Factory breaks it down into a dependency graph (DAG), creates GitHub issues, autonomously writes code and tests, auto-heals CI & review feedback, and drives delivery across phases to completion!

[🇬🇧 English Guide](./README.md) | [🇮🇷 راهنمای فارسی](./README.fa.md)

---


## ⚡ Quick Start (Zero-Config)

Developers can bootstrap the entire stack with a single command:

```bash
# 1. Clone repository
git clone https://github.com/ali-ahadi1105/open-factory.git
cd open-factory

# 2. Start the factory (Bootstraps prerequisites, GitHub auth, skill injection, engine & dashboard)
./factory up
```

That's it! `./factory up` automatically:
1. **Checks prerequisites** (`Python >= 3.10`, `uv`, `Node/npx`, `gh`, `Docker`).
2. **Detects GitHub credentials** via `gh auth token` (zero-touch) or interactive PAT input.
3. **Injects & registers the `ali-factory` skill** directly into OpenHands (`~/.openhands/skills/ali-factory/`).
4. **Launches OpenHands Engine** (Local Agent Canvas or validates Cloud configuration).
5. **Starts the Dashboard server** on `http://localhost:8080` and opens your browser.

---

## 🎮 Developer CLI Commands

| Command | Description |
|---|---|
| `./factory up` (or `./factory`) | Start everything: preflight checks, skill injection, OpenHands, and Dashboard |
| `./factory status` | Show real-time health, active GitHub account, OpenHands status, and skill injection state |
| `./factory down` (or `./factory stop`) | Cleanly terminate all running factory services and free occupied ports |
| `./factory skill sync` | Re-sync local `ali-factory` skill files to OpenHands |
| `./factory skill inject <repo-path>` | Inject the `ali-factory` skill directly into any target repository (`.openhands/skills/ali-factory`) |
| `./factory logs [dashboard\|openhands]` | View or follow service logs in real time (`-f` to follow) |

---

## 🚀 Key Features

### 1. Requirements Decomposition & Phased DAG
Describe your project or feature spec once in markdown (e.g. `spec.md`). Open Factory breaks it down into a directed acyclic graph (DAG) of GitHub issues:
- **Phase 1:** Labeled `openhands` (triggers immediate implementation).
- **Phases 2+:** Labeled `blocked` with explicit `blocked-by:#<issue_id>` dependencies.

### 2. Event-Driven Phase Orchestration
When Phase 1's pull request passes CI and is merged:
- The Phase Orchestrator automatically inspects dependent issues.
- It removes `blocked`, applies the `openhands` trigger label, and smoothly transitions execution to Phase 2.

### 3. Automated PR Healing Loop
If CI tests fail or a code review requests changes:
- The PR Auto-Healer gathers the error trace and review comments.
- It applies `openhands-fix` (up to 3 retries) instructing the agent to resolve the issues autonomously.
- If it cannot be resolved automatically, it escalates with `needs-human-attention`.

### 4. Live Agent Console & Interactive HITL Chat
- Real-time event streaming and thought-bubble inspection.
- Human-in-the-loop (HITL) interactive chat: send instructions or clarifications directly to OpenHands if an agent gets stuck.
- Emergency interrupt and resume controls.

### 5. Multi-Engine & Multi-Account Support
- Seamlessly toggle between **Local Agent Canvas** (`http://127.0.0.1:8000`) and **OpenHands Cloud** (`https://app.all-hands.dev`).
- Multi-account GitHub management with active token switching in the UI.

---

## 🏗️ Stations Overview

| Station | Catalog ID | Role | Phase |
|---|---|---|---|
| **Phase Orchestrator** | `github-phase-orchestrator` | Monitors issue dependencies & unlocks next phase on PR merge | 1 or add-on |
| **PR Auto-Healer** | `github-pr-healer` | Diagnoses CI failures & review change requests, triggers fix revisions | 1 or add-on |
| **Issue to PR** | `github-issue-to-pr` | Implements labelled issues and opens pull requests | 1 |
| **PR Reviewer** | `github-pr-reviewer` | Reviews open pull requests and posts findings | 1 |
| **Issue Triage** | `github-issue-triage` | Triages incoming issues and labels them | 1 |
| **Auto-merge Watchdog** | `github-delivery-watchdog` | Automatically merges approved PRs with green CI | add-on |
| **AGENTS.md Maintainer** | `github-agents-md-maintainer` | Generates/maintains repository instructions for agents | add-on |
| **Stale-CI PR Closer** | `github-stale-ci-pr-closer` | Closes PRs whose CI consistently fails | add-on |
| **QA Changes** | `qa-changes` | Automated QA checks via webhooks | add-on |

---

## 📁 Repository Structure

```text
open-factory/
├── factory                        # Root executable bootstrapper
├── README.md                      # Project documentation
├── SKILL.md                       # ali-factory skill definition (injected into OpenHands)
├── dashboard/                     # FastAPI backend & reactive Tailwind UI
│   ├── app.py                     # Dashboard server & OpenHands REST proxy
│   └── static/index.html          # Single-page dashboard & Live Agent Console
├── scripts/
│   ├── factory_cli.py             # CLI implementation, port manager & skill sync
│   ├── factory.py                 # Core automation and station catalog runner
│   ├── planner.py                 # Requirements decomposition & DAG generation
│   ├── orchestrator.py            # State machine & phase transition engine
│   ├── healer.py                  # CI & review diagnostic & auto-healing engine
│   └── github_client.py           # GitHub API client
├── commands/                      # Slash commands for OpenHands
├── templates/                     # GitHub Actions workflow templates
└── tests/                         # Unit tests (100% passing)
```

---

## 🧪 Testing

Run the full test suite with:

```bash
uv run --with fastapi --with uvicorn python3 -m unittest discover tests
```

---

## 📄 License
MIT License.
