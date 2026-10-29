# OpenHands Autonomous Software Factory (v2)

An upgraded, autonomous Software Factory for [OpenHands](https://github.com/All-Hands-AI/OpenHands) and GitHub.

In addition to the standard OpenHands station pipeline, this enhanced version introduces **autonomous requirements decomposition** and **automated label/phase transition orchestration**. You only need to describe your project requirements, bug fix, or feature request once—the factory breaks it down, creates dependent GitHub issues, and automatically drives execution from Phase 1 to completion.

---

## What It Sets Up

| Station | Catalog ID | Role | Phase |
|---|---|---|---|
| **Phase Orchestrator** | `github-phase-orchestrator` | Monitors issue dependencies & unlocks next phase on PR merge | 1 or add-on |
| **Issue to PR** | `github-issue-to-pr` | Implements labelled issues and opens pull requests | 1 |
| **PR Reviewer** | `github-pr-reviewer` | Reviews open pull requests and posts findings | 1 |
| **Issue Triage** | `github-issue-triage` | Triages incoming issues and labels them | 1 |
| **Auto-merge Watchdog** | `github-delivery-watchdog` | Automatically merges approved PRs with green CI | add-on |
| **AGENTS.md Maintainer** | `github-agents-md-maintainer` | Generates/maintains repository instructions for agents | add-on |
| **Stale-CI PR Closer** | `github-stale-ci-pr-closer` | Closes PRs whose CI consistently fails | add-on |
| **QA Changes** | `qa-changes` | Automated QA checks via webhooks | add-on |

---

## The Autonomous End-to-End Workflow

```text
[High-Level User Requirement / Spec]
               │
               ▼
┌───────────────────────────────────────────────┐
│ 1. Requirements Planner (factory.py plan)     │
│ - Decomposes spec into ordered phases (DAG)   │
│ - Creates GitHub issues with dependencies     │
│ - Labels Phase 1: `openhands` (Triggered)     │
│ - Labels Phase 2+: `blocked` + `blocked-by:#` │
└──────────────────────┬────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────┐
│ 2. Implementation & Delivery Cycle            │
│ - Issue to PR: implements Phase 1 and opens PR│
│ - PR Reviewer: reviews and approves code      │
│ - Watchdog: merges PR once CI tests pass      │
└──────────────────────┬────────────────────────┘
                       │
                       ▼ (PR Merged / Issue #1 Closed)
┌───────────────────────────────────────────────┐
│ 3. Automated Orchestrator (Actions or Station)│
│ - Detects completion of Phase 1 blocker       │
│ - Removes `blocked` label from Phase 2 issue  │
│ - Adds `openhands` trigger label to Phase 2   │
│ ➔ OpenHands automatically starts Phase 2!     │
└───────────────────────────────────────────────┘
```

---

## Quick Start & Commands

Set your environment variables:
```bash
export GITHUB_PERSONAL_ACCESS_TOKEN="ghp_..."
export OPENHANDS_HOST="http://localhost:3000"  # or your OpenHands instance
export OPENHANDS_AUTOMATION_API_KEY="..."       # or OPENHANDS_API_KEY
```

### 1. Requirements Planning (`plan`)
Convert a feature description or spec file into phased issues on GitHub:
```bash
# Preview plan without touching GitHub (dry-run)
python3 scripts/factory.py plan --repos owner/repo --spec-file requirement.md --dry-run

# Create issues on GitHub
python3 scripts/factory.py plan --repos owner/repo --spec-file requirement.md
```

### 2. Install GitHub Actions Orchestrator (Zero-Server Option)
Install the event-driven workflow into the target repository so GitHub automatically advances phases on PR merge:
```bash
python3 scripts/factory.py install-orchestrator --repos owner/repo
```

### 3. Check / Run Orchestrator Manually
```bash
# Check issues once and unblock any satisfied phases
python3 scripts/factory.py orchestrate --repos owner/repo

# View full pipeline graph and phase statuses
python3 scripts/factory.py graph --repos owner/repo
```

### 4. Create Standard OpenHands Stations
```bash
# Preflight checks
python3 scripts/factory.py preflight --repos owner/repo

# Create stations
python3 scripts/factory.py create --repos owner/repo --ids github-issue-to-pr,github-pr-reviewer,github-phase-orchestrator,github-delivery-watchdog

# Verify and enable
python3 scripts/factory.py verify <automation_ids>
```

---

## Directory Structure

```text
open-factory/
├── README.md                      # This document
├── SKILL.md                       # OpenHands skill definition
├── commands/
│   ├── software-factory.md        # /software-factory command
│   └── factory-plan.md            # /factory-plan command
├── scripts/
│   ├── factory.py                 # Core CLI helper
│   ├── planner.py                 # Requirements decomposition & DAG generation
│   ├── orchestrator.py            # State machine & phase transition engine
│   └── github_client.py           # Python stdlib GitHub API client
├── templates/
│   └── factory-orchestrator.yml   # Event-driven GitHub Actions workflow
└── automations/
    └── catalog/
        └── github-phase-orchestrator/ # OpenHands automation catalog station
            ├── manifest.json
            └── worker.py
```
