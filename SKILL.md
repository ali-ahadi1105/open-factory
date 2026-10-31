---
name: ali-factory
description: >
  Set up an autonomous OpenHands "ali-factory" on GitHub with requirements planning (DAG decomposition), automated label and phase transition orchestration, and PR auto-healing (CI / review feedback loop).
triggers:
  - /ali-factory
  - ali-factory
  - ali factory
  - /factory-plan
  - plan feature
  - autonomous software factory
---

# Ali Factory (Autonomous Software Factory v2)

Build an autonomous GitHub software factory with automated requirements planning,
phased DAG issue management, automated PR healing (CI test failure & review feedback loop),
and zero-overhead GitHub Actions orchestration.

Two phases: **Phase 1** gets a working factory after one question. **Phase 2**
extends it in short rounds, only as far as the user wants. After Phase 1 and
after every round, the **Checkpoint** shows what exists and lets the user stop.

Code lives on GitHub. Tickets can come from GitHub issues (default), Jira or
Linear: only the issue to PR station changes (see "Ticket source"). GitLab and
Bitbucket code hosts are not part of this flow.

## Stations

| Station | Catalog id | Phase | Needs agent profile |
|---|---|---|---|
| Issue to PR | `github-issue-to-pr`, or the Jira or Linear station | 1 | yes (GitHub issues only) |
| PR reviewer | `github-pr-reviewer` | 1 | yes |
| Issue triage | `github-issue-triage`, or `linear-triage-assistant` | 1 | yes (GitHub issues only) |
| Phase orchestrator | `github-phase-orchestrator` | 1 or add-on | no |
| PR auto-healer | `github-pr-healer` | 1 or add-on | no |
| AGENTS.md maintainer | `github-agents-md-maintainer` | add-on | no |
| Auto-merge watchdog | `github-delivery-watchdog` | add-on | no |
| Stale-CI PR closer | `github-stale-ci-pr-closer` | add-on | no |
| QA changes | `qa-changes` | add-on | no |

## Helper script

`scripts/factory.py` in this skill's directory does every catalog and API step
and prints one JSON document; it exits non-zero with `{"error": ...}` on
failure. Its usage is below: do not read its source, and do not load the
`openhands-automation` skill for Phase 1.

Set `OPENHANDS_HOST` to the **Ingress** URL in `<RUNTIME_SERVICES>` on a local
Agent Canvas (it serves both `/api/automation` and `/api`; the automation
backend URL does not), or to `<HOST>` on OpenHands Cloud. Run every command as:

```bash
GITHUB_PERSONAL_ACCESS_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN" \
OPENHANDS_AUTOMATION_API_KEY="$OPENHANDS_AUTOMATION_API_KEY" \
OPENHANDS_API_KEY="$OPENHANDS_API_KEY" \
python3 <this skill's directory>/scripts/factory.py <command> ...
```

`$F` below stands for that whole line. Keep the secret names in it: the agent
server passes a saved secret into a command only when the command names it.
The script reads the token and never prints it.

| Command | Does |
|---|---|
| `$F preflight --repos a/b,c/d` | Which stations this deployment supports, the agent profile, the GitHub login, push access per repository. |
| `$F create --repos a/b --ids ID,ID [--set FIELD=VALUE] [--set ID.FIELD=VALUE]` | Fills each station's form (answers, then derived values, then manifest defaults), uploads the bundle and creates it disabled with the `software-factory` tag. `FIELD=VALUE` applies to every listed station with that field. Stations that already exist are reported under `existing`, not recreated, and get the tag added. |
| `$F tune --repos a/b --ids ID,ID [--set FIELD=VALUE] [--set ID.FIELD=VALUE]` | Applies new settings to an existing station by updating it in place. Use this to change a value that is fixed at creation. |
| `$F tag ID...` | Adds the `software-factory` tag to automations created outside the script, so the Automations list can filter to the factory. Lists any it could not tag under `not_tagged`, each with its `reason`. |
| `$F verify ID... [--no-wait ID...]` | Dispatches once, enables the ones whose run `COMPLETED` and disables the rest, reporting their `error_detail`. `--no-wait` enables without waiting. |
| `$F status ID...` | The latest run of each. |
| `$F prs --repos a/b [--branch-prefix P]` | Open pull requests whose branch starts with `P` (default `openhands/agents-md`, the AGENTS.md maintainer's). |
| `$F reset-token` | Deletes the saved `GITHUB_PERSONAL_ACCESS_TOKEN` (local Agent Canvas), so `request_secret` opens its form again. |
| `$F plan --repos a/b [--spec "..." \| --spec-file F \| --plan-file P] [--dry-run]` | Analyzes requirements, breaks them into phased DAG issues, marks Phase 1 with `openhands` and Phase 2+ with `blocked`. |
| `$F orchestrate --repos a/b [--dry-run]` | Scans open issues; when all blockers for a phase are closed/merged, lifts `blocked` and attaches `openhands`. |
| `$F heal --repos a/b [--max-retries 3] [--dry-run]` | Scans factory PRs; when CI fails or review requests changes, flags `software-factory/review: failure` to trigger OpenHands revision loop. |
| `$F watch --repos a/b [--interval 30]` | Continuously polls repository: advances unblocked phases AND automatically heals failing PRs. |
| `$F install-orchestrator --repos a/b [--branch main]` | Installs the GitHub Actions phase orchestrator & auto-healer workflow directly into the target repository. |
| `$F graph --repos a/b` | Displays the status and dependency relationships of all software factory issues in the repository. |

`create` and `verify` return each automation's `url`, its page in Agent Canvas.
Trust the script's output: do not open pages in the browser to check them, and
do not use the task tracker for this flow.

Add `--profile NAME` to `preflight` or `create` to use a profile other than the
active one.

## Rules

- **Supported stations**: create only those `preflight` reports `ok`; say why
  the others are skipped.
- **Derived values** (the script fills them; never ask): every
  `githubTokenSecret` is `GITHUB_PERSONAL_ACCESS_TOKEN`. The watchdog's
  `branchPrefix` must equal issue to PR's: when it was changed, pass
  `--set github-delivery-watchdog.branchPrefix=<same value>`.
- **Agent profile**: if `preflight` reports a profile error, skip the stations
  that need one and point the user to the station's catalog card in Agent
  Canvas (Automations), which has a profile picker.
- **Token**: never ask for its value in chat, echo it, or put it in a tool
  argument or file.
- **Failures**: if `create` fails, its output lists the stations it already
  created; report them with the error and ask whether to retry or delete them
  (`DELETE ${OPENHANDS_HOST}/api/automation/v1/{id}`).
- **Existing stations**: a station `create` reports under `existing` was already
  set up by an earlier run and was left unchanged. Say so rather than presenting
  it as new, and change it with `$F tune` if the user wants different settings.
- **Factory tag**: every station carries the `software-factory` tag, which the
  Automations list filters on. `create` adds it; for a station another skill
  creates (Jira or Linear issue to PR, Linear triage, QA changes), run
  `$F tag <id>` once it exists. A station listed under `not_tagged` works but
  has no tag, and its `reason` says why. `only its creator can change its tags`:
  another user created it, so tell the user they can ask that person to add the
  tag. `this automation service does not support tags yet`: the deployment's
  automation service predates tags; tell the user, and run `$F tag <id>` for
  those stations once it is upgraded.

## Phase 1 - Working factory

1. Ask, in one message: "Which GitHub repositories (`owner/repo`)? Where do
   tickets live: GitHub issues (default), Jira or Linear?" Say that everything
   else uses defaults that can be changed afterwards. If the answer names only
   repositories, use GitHub issues; do not ask again.
2. Run `$F preflight --repos <repos>`. Drop repositories without `push`, and
   say which.
3. If `github_login` is null, act on `token_error`:
   - `not_set`, and the `request_secret` tool is available: call it once with
     `name: "GITHUB_PERSONAL_ACCESS_TOKEN"` and a `reason` asking for Contents,
     Issues, Pull requests and Workflows read and write (classic PAT: `repo`,
     `workflow`), and saying a token can be created at
     https://github.com/settings/personal-access-tokens. End your turn, wait
     for the `[secret-request]` message, then rerun `preflight`.
   - `rejected`: GitHub refuses the saved token (invalid or expired), and
     `request_secret` cannot replace it: it answers `saved` at once for a name
     that exists. Say so in one sentence and ask "Shall I remove it and open
     the form for a new one?" On yes, run `$F reset-token`, then handle it as
     `not_set`. If `reset-token` fails, tell the user to create a token at
     https://github.com/settings/personal-access-tokens, replace it in
     Settings -> Secrets, and say when done.
   - `not_set` without the tool: tell the user to create a token at
     https://github.com/settings/personal-access-tokens and add it in
     Settings -> Secrets, then stop.
4. `$F create --repos <repos> --ids github-pr-reviewer`, adding
   `github-issue-to-pr,github-issue-triage` for GitHub issues. For Jira or
   Linear, also set up the issue to PR station as "Ticket source" describes;
   for Linear, set up triage with the `linear-triage` skill (Jira has none).
5. `$F verify <reviewer id> [<issue to PR id>] [<triage id>]`.
6. Go to the Checkpoint.

## Checkpoint

Write one short message, in this order:

1. One plain sentence on what runs and how to use it: "Your factory is running
   on `<repos>`. Add the `<trigger label>` label to a ticket to get a draft pull
   request; the reviewer reviews it and you merge it." (On Jira, add that the
   ticket body must name the `owner/repo`.)
2. If the AGENTS.md maintainer was added, the `AGENTS.md` pull requests from
   `$F prs --repos <repos>`, asking the user to merge them: they give the build
   agent the repository's build, test and lint commands. One opened before
   this setup started (`created_at`) is "still open from an earlier run", not
   new; the maintainer works once a week per repository. If there are none
   yet, say they are on their way.
3. A small table of the automations, each linked to its `url`:

   | Automation | Schedule | Status |
   |---|---|---|
   | [Issue to PR](<url>) | every 15 minutes | on |

   Status is `on`, or `off` with the reason in a few words. No automation ids.
4. The settings in effect, as a short list (the Tune table's current values).
   Mention that the reviewer also reviews any open, non-draft, green pull
   request with no review yet (at most 2 new per scan).
5. End with only a list of possible next steps, one line each, and nothing
   after it. Mark the first one not done yet as recommended:
   - **Try it**: I open a small test issue with the `<trigger label>` label in
     `<repo>`, only if you say so, and report the pull request.
   - **Tune**: change a setting from the list above.
   - **Add a station**: AGENTS.md maintainer, auto-merge watchdog, stale-CI closer (QA only
     if `preflight` reports it `ok`).
   - **Stop here**: the factory keeps running as it is.

## Phase 2 - Extend, one round at a time

Do only what the user picked. Ask only the questions for it, in one message,
each with its current value. Apply, then return to the Checkpoint.

### Tune

| Setting | Default | `--set` |
|---|---|---|
| Issue trigger label (GitHub issues) | `openhands` | `github-issue-to-pr.triggerLabel` |
| Draft or ready pull requests | `draft` | `pullRequestMode` (issue to PR, AGENTS.md maintainer) |
| Branch prefix (GitHub issues) | `openhands/issue` | `github-issue-to-pr.branchPrefix` (and the watchdog's, if present) |
| Which pull requests get reviewed | label `openhands-review`, reviewer `all-hands-bot` | `github-pr-reviewer.triggerLabel`, `triggerReviewer` |
| Review tone: concise, thorough, friendly | `concise` | `reviewTone` |
| Human reviewers requested after agent approval (two or more logins) | none | `maintainers` |
| Schedules and timezone | issue to PR `*/15 * * * *`, reviewer `*/5 * * * *`, triage `*/5 * * * *`, add-ons per manifest; `UTC` | `ID.schedule`, `timezone` |
| Agent profile | active profile | `--profile NAME` |

Apply:

- Schedule or timezone only:
  `PATCH ${OPENHANDS_HOST}/api/automation/v1/{id}` with `{"trigger": {...}}`.
- Jira or Linear station settings: change them as its skill describes.
- Anything else is fixed at creation: `$F tune --repos <repos> --ids <id>` with
  the new `--set` values (repeat every earlier non-default value), then
  `$F verify <id>`. `tune` updates the station in place, so there is no
  replacement to delete and no template-id collision.

### Add a station

Ask only the question below, then `$F create` it and `$F verify` it.

- **Auto-merge watchdog** (`github-delivery-watchdog`, GitHub issues only,
  since it merges only branches with the issue to PR prefix): confirm the user
  wants the agent to merge pull requests itself once review, tests and CI are
  green.
- **Stale-CI closer** (`github-stale-ci-pr-closer`): confirm it may warn on,
  then close, pull requests whose CI keeps failing.
- **AGENTS.md maintainer** (`github-agents-md-maintainer`): nothing. Verify it
  with `--no-wait`; its run opens one `AGENTS.md` pull request per repository.
- **QA changes**: which base branch (default `main`). Offer QA only if
  `preflight` reports it `ok` and the `openhands-automation` webhook
  reachability check passes. It has no bundle, so the script cannot create it:
  create it as the `openhands-automation` skill describes for an
  event-triggered prompt preset, with its manifest `prompt` and `filter`, one
  per repository (it takes a single `repository`). It runs only on pull request
  events, so leave it enabled and skip `verify`.

## Ticket source

- **GitHub issues**: `github-issue-to-pr`, created with the others in Phase 1.
- **Jira**: follow the `jira-issue-to-pr` skill with `git_provider` `github`
  and its defaults (label `create-pr`, `*/5 * * * *`). Ask only what it cannot
  look up (the Jira site if several, or the API-token details when Atlassian
  Rovo is not connected). Tell the user each ticket body must name its
  `owner/repo`.
- **Linear**: follow the `ticket-to-code-change` skill with GitHub as the
  provider. Ask its questions in one message, proposing the repositories above
  and `*/15 * * * *` as defaults.

Once its skill has created the station, run `$F tag <id>` and then
`$F verify <id>` on it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Create returns 422 | A value does not match the manifest `setup` block; fix the field at the path the error names. |
| Issue to PR fails at push | The token lacks Contents or Workflows write, or does not cover that repository. |
| Reviewer never reviews | The pull request has neither the trigger label nor a request for the trigger reviewer, and is not green. |
| Watchdog never merges | Its branch prefix differs from issue to PR's, or the pull request lacks an independent approval or green CI. |

## Autonomous Requirements Planning and Phased Execution

When the user specifies a high-level requirement (e.g. building a new feature, fixing a bug, or building a project from a spec):

1. **Requirements Planning (`$F plan`)**:
   - The agent analyzes the user prompt and breaks it into an ordered Directed Acyclic Graph (DAG) of implementation phases.
   - The agent executes:
     ```bash
     $F plan --repos <repo> --spec "<structured_or_raw_spec>"
     ```
   - **Phase 1 (root)** is assigned the trigger label `openhands` and status `status:ready`.
   - **Phase 2+ (dependents)** are assigned status `blocked` and `blocked-by:#<issue_id>`.
   - A visual Mermaid DAG diagram is displayed to the user.

2. **Automated State Transition & Label Management**:
   Choose one of two orchestrator modes:
   - **Option A (Zero-overhead GitHub Actions - Recommended)**:
     Install the orchestrator workflow into the repo once:
     ```bash
     $F install-orchestrator --repos <repo>
     ```
     GitHub Actions will listen to pull request merges and automatically remove `blocked`, attach `openhands`, and post progress comments on the next phase!
   - **Option B (Station / Polling)**:
     Create the `github-phase-orchestrator` station or run `$F orchestrate --repos <repo>` periodically.

3. **Hands-free Execution Loop**:
   - OpenHands implements Phase 1 (`github-issue-to-pr`).
   - PR is reviewed (`github-pr-reviewer`).
   - PR is merged once CI is green (`github-delivery-watchdog`).
   - Merging PR closes Phase 1 issue $\rightarrow$ Orchestrator unlocks Phase 2 by adding `openhands`.
   - The cycle continues until all phases are delivered!

4. **Automated PR Healing & CI Feedback Loop (`$F heal` / `github-pr-healer`)**:
   - **The Problem**: OpenHands's `IssueToPR` revision trigger specifically requires the commit status `software-factory/review` to be `failure` or `error`. Standard GitHub Actions CI test failures and human/bot reviews with `CHANGES_REQUESTED` never set this status natively, causing the pipeline to stall indefinitely.
   - **The Solution**: The PR Healer diagnoses open pull requests:
     - Scans for failed GitHub Actions checks (`failure`, `timed_out`, `action_required`) and PR reviews requesting changes (`CHANGES_REQUESTED` or unresolved inline comments).
     - Formats diagnostic findings and posts a clear action item comment on the PR.
     - Sets the commit status `software-factory/review` to `failure` on the PR head commit SHA, which immediately triggers OpenHands to revise and commit a fix!
     - Tracks retry attempts via `fix-attempt:N` and labels PR with `openhands-fix`.
   - **Loop Protection & Escalation**:
     - Up to `max_retries` (default: 3) are attempted automatically.
     - If fixes fail repeatedly beyond the threshold, the healer tags the PR with `needs-human-attention` and stops re-triggering to prevent infinite loops and token waste.
   - **Execution Modes**:
     - **Event-Driven (GitHub Actions)**: Included automatically in `factory-orchestrator.yml` (triggers on `workflow_run: completed` and `pull_request_review: submitted`).
     - **Station Mode**: Station `github-pr-healer` in OpenHands.
     - **CLI Commands**:
       ```bash
       # Heal all or specific PRs once
       $F heal --repos <repo> [--pr <pr_number>]

       # Concurrently monitor phase transitions and heal PRs in real-time
       $F watch --repos <repo> [--interval 30]
       ```

