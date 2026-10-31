"""Comprehensive unit tests for OpenHands Software Factory v2 extensions."""

import json
import unittest
from pathlib import Path
import sys

# Add scripts directory
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from planner import PlanTask, parse_markdown_spec, parse_json_plan, validate_dag, execute_plan
from orchestrator import extract_blockers, check_and_transition


class TestPlanner(unittest.TestCase):
    def test_parse_structured_markdown(self):
        doc = """
# Feature Spec

## Phase 1: Models
### Summary
Create user schema.
### Acceptance Criteria
- [ ] Users table exists
- [ ] Migration passes

## Phase 2: API Endpoints
Depends on: Phase 1
### Summary
Build login and register.
### Acceptance Criteria
- [ ] POST /login works
"""
        tasks = parse_markdown_spec(doc)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].phase, 1)
        self.assertEqual(tasks[0].title, "Models")
        self.assertEqual(len(tasks[0].acceptance_criteria), 2)
        self.assertEqual(tasks[0].depends_on_phases, [])

        self.assertEqual(tasks[1].phase, 2)
        self.assertEqual(tasks[1].title, "API Endpoints")
        self.assertEqual(tasks[1].depends_on_phases, [1])

    def test_dag_cycle_validation(self):
        # Phase 1 depends on Phase 2 (forward/cyclic)
        bad_tasks = [
            PlanTask(phase=1, title="A", summary="A", depends_on_phases=[2]),
            PlanTask(phase=2, title="B", summary="B", depends_on_phases=[]),
        ]
        with self.assertRaises(ValueError):
            validate_dag(bad_tasks)

    def test_execute_plan_dry_run(self):
        doc = """
## Phase 1: Foundation
### Summary
Setup repo.

## Phase 2: Implementation
Depends on: Phase 1
### Summary
Do work.
"""
        tasks = parse_markdown_spec(doc)
        result = execute_plan("owner/test-repo", tasks, dry_run=True)
        self.assertEqual(result["total_phases"], 2)
        self.assertEqual(len(result["issues"]), 2)

        p1 = result["issues"][0]
        self.assertIn("openhands", p1["labels"])
        self.assertIn("status:ready", p1["labels"])
        self.assertEqual(p1["status"], "triggered")

        p2 = result["issues"][1]
        self.assertIn("blocked", p2["labels"])
        self.assertIn(f"blocked-by:#{p1['number']}", p2["labels"])
        self.assertEqual(p2["status"], "blocked")


class MockGitHubClient:
    def __init__(self, issues: list[dict]):
        self.issues_by_num = {i["number"]: dict(i) for i in issues}
        self.actions_log = []

    def list_issues(self, repo: str, state: str = "open", labels: list[str] | None = None):
        return [dict(i) for i in self.issues_by_num.values() if i.get("state") == state]

    def get_issue(self, repo: str, number: int):
        return dict(self.issues_by_num[number])

    def remove_label(self, repo: str, number: int, label: str):
        self.actions_log.append(("remove_label", number, label))
        if number in self.issues_by_num:
            lbls = self.issues_by_num[number].setdefault("labels", [])
            self.issues_by_num[number]["labels"] = [l for l in lbls if (l.get("name") if isinstance(l, dict) else l) != label]

    def add_labels(self, repo: str, number: int, labels: list[str]):
        self.actions_log.append(("add_labels", number, labels))
        if number in self.issues_by_num:
            lbls = self.issues_by_num[number].setdefault("labels", [])
            for l in labels:
                lbls.append({"name": l})

    def create_comment(self, repo: str, number: int, body: str):
        self.actions_log.append(("create_comment", number, body))


class TestOrchestrator(unittest.TestCase):
    def test_extract_blockers_from_labels_and_metadata(self):
        issue_with_labels = {
            "number": 2,
            "labels": [{"name": "phase:2"}, {"name": "blocked"}, {"name": "blocked-by:#101"}],
            "body": "Some task",
        }
        self.assertEqual(extract_blockers(issue_with_labels), [101])

        issue_with_meta = {
            "number": 3,
            "labels": [{"name": "blocked"}],
            "body": "Task <!-- software-factory-metadata: {\"blocked_by\": [102, 103]} -->",
        }
        self.assertEqual(extract_blockers(issue_with_meta), [102, 103])

    def test_transition_when_blocker_is_closed(self):
        # Issue 101 is closed, Issue 102 is blocked by 101
        mock_issues = [
            {"number": 101, "title": "Phase 1", "state": "closed", "labels": ["phase:1"]},
            {"number": 102, "title": "Phase 2", "state": "open", "labels": [{"name": "blocked"}, {"name": "blocked-by:#101"}]},
        ]
        mock_client = MockGitHubClient(mock_issues)
        result = check_and_transition("owner/repo", client=mock_client)

        self.assertEqual(result["transitions_count"], 1)
        transition = result["transitions"][0]
        self.assertEqual(transition["issue_number"], 102)
        self.assertEqual(transition["action"], "unblock_and_trigger")

        # Verify mock client had labels removed and added
        actions = [a[0] for a in mock_client.actions_log]
        self.assertIn("remove_label", actions)
        self.assertIn("add_labels", actions)
        self.assertIn("create_comment", actions)


class MockHealerGitHubClient(MockGitHubClient):
    def __init__(self, issues: list[dict], check_runs=None, workflow_runs=None, reviews=None, comments=None):
        super().__init__(issues)
        self._check_runs = check_runs or []
        self._workflow_runs = workflow_runs or []
        self._reviews = reviews or []
        self._comments = comments or []
        self.statuses_log = []

    def get_check_runs(self, repo: str, commit_sha: str):
        return self._check_runs

    def list_workflow_runs_for_commit(self, repo: str, commit_sha: str):
        return self._workflow_runs

    def list_reviews(self, repo: str, number: int):
        return self._reviews

    def list_review_comments(self, repo: str, number: int):
        return self._comments

    def create_commit_status(self, repo: str, commit_sha: str, state: str, context: str, description: str = "", target_url: str = ""):
        self.statuses_log.append({"sha": commit_sha, "state": state, "context": context, "description": description})
        return {"state": state, "context": context}


class TestHealer(unittest.TestCase):
    def test_diagnose_pr_ci_failed(self):
        from healer import diagnose_pr

        pr = {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "openhands/issue-42"},
            "labels": [],
        }
        mock_checks = [
            {"name": "test-suite", "status": "completed", "conclusion": "failure", "output": {"summary": "2 tests failed"}},
            {"name": "linter", "status": "completed", "conclusion": "success"},
        ]
        client = MockHealerGitHubClient(issues=[], check_runs=mock_checks)
        diag = diagnose_pr("owner/repo", pr, client)
        self.assertEqual(diag.status, "CI_FAILED")
        self.assertEqual(len(diag.failed_checks), 1)
        self.assertIn("test-suite", diag.reasons[0])

    def test_diagnose_pr_changes_requested(self):
        from healer import diagnose_pr

        pr = {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "openhands/issue-42"},
            "labels": [],
        }
        mock_reviews = [
            {"user": {"login": "senior-dev"}, "state": "CHANGES_REQUESTED", "body": "Please add validation on line 50."},
        ]
        client = MockHealerGitHubClient(issues=[], reviews=mock_reviews)
        diag = diagnose_pr("owner/repo", pr, client)
        self.assertEqual(diag.status, "CHANGES_REQUESTED")
        self.assertEqual(len(diag.reviews_feedback), 1)
        self.assertIn("senior-dev", diag.reasons[0])

    def test_heal_pr_triggers_revision(self):
        from healer import diagnose_pr, heal_pr

        pr = {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "openhands/issue-42"},
            "labels": [],
        }
        mock_checks = [
            {"name": "test-suite", "status": "completed", "conclusion": "failure", "output": {"summary": "ZeroDivisionError"}},
        ]
        client = MockHealerGitHubClient(issues=[{"number": 42, "labels": []}], check_runs=mock_checks)
        diag = diagnose_pr("owner/repo", pr, client)
        outcome = heal_pr("owner/repo", pr, diag, client, max_retries=3)

        self.assertEqual(outcome["action"], "revision_triggered")
        self.assertEqual(outcome["attempt"], 1)

        # Check commit status was set to failure for software-factory/review
        review_statuses = [s for s in client.statuses_log if s["context"] == "software-factory/review"]
        self.assertTrue(len(review_statuses) > 0)
        self.assertEqual(review_statuses[0]["state"], "failure")

        # Check attempt label was added
        added_labels = [a[2] for a in client.actions_log if a[0] == "add_labels"]
        self.assertTrue(any("fix-attempt:1" in l for l in added_labels))

    def test_heal_pr_escalates_when_exhausted(self):
        from healer import diagnose_pr, heal_pr, ESCALATION_LABEL

        pr = {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "openhands/issue-42"},
            "labels": [{"name": "fix-attempt:3"}],
        }
        mock_checks = [
            {"name": "test-suite", "status": "completed", "conclusion": "failure"},
        ]
        client = MockHealerGitHubClient(issues=[{"number": 42, "labels": [{"name": "fix-attempt:3"}]}], check_runs=mock_checks)
        diag = diagnose_pr("owner/repo", pr, client)
        self.assertEqual(diag.attempt_count, 3)

        outcome = heal_pr("owner/repo", pr, diag, client, max_retries=3)
        self.assertEqual(outcome["action"], "escalated_to_human")

        # Check escalation label was added
        added_labels = [a[2] for a in client.actions_log if a[0] == "add_labels"]
        self.assertTrue(any(ESCALATION_LABEL in l for l in added_labels))


if __name__ == "__main__":
    unittest.main()
