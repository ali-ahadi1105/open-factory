import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import scripts.factory_cli as cli


class TestFactoryCli(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.test_dir, ignore_errors=True))

    def test_preflight_checks(self):
        results = cli.run_preflight_checks()
        self.assertIn("python", results)
        self.assertIn("uv", results)
        self.assertIn("gh", results)
        self.assertIn("npx", results)
        self.assertIn("docker", results)
        self.assertTrue(results["python"])

    def test_get_openhands_config(self):
        with patch.object(cli, "DASHBOARD_DIR", Path(self.test_dir)):
            cfg = cli.get_openhands_config()
            self.assertIn("mode", cfg)
            self.assertIn("local", cfg)
            self.assertIn("cloud", cfg)
            self.assertEqual(cfg["local"]["base_url"], "http://127.0.0.1:8000")

    def test_sync_ali_factory_skill(self):
        fake_home = Path(self.test_dir) / "home"
        fake_oh = fake_home / ".openhands"
        fake_skills = fake_oh / "skills"
        fake_installed = fake_skills / "installed"
        fake_settings = fake_oh / "settings.json"

        fake_installed.mkdir(parents=True, exist_ok=True)
        # Create a mock settings.json
        initial_settings = {
            "agent_settings": {"agent_context": {"load_user_skills": False}},
            "misc_settings": {"app_preferences": {"enabled_skills": ["github"]}},
        }
        fake_settings.write_text(json.dumps(initial_settings), encoding="utf-8")

        with patch.object(cli, "OPENHANDS_DIR", fake_oh), \
             patch.object(cli, "OPENHANDS_SKILLS_DIR", fake_skills), \
             patch.object(cli, "OPENHANDS_INSTALLED_DIR", fake_installed), \
             patch.object(cli, "OPENHANDS_SETTINGS_FILE", fake_settings):

            ok = cli.sync_ali_factory_skill()
            self.assertTrue(ok)

            # Check that files were copied
            skill_target = fake_skills / "ali-factory"
            self.assertTrue((skill_target / "SKILL.md").exists())
            self.assertTrue((skill_target / "scripts").is_dir())

            # Check installed .installed.json
            installed_json = fake_installed / ".installed.json"
            self.assertTrue(installed_json.exists())
            idata = json.loads(installed_json.read_text(encoding="utf-8"))
            self.assertIn("ali-factory", idata.get("extensions", {}))
            self.assertTrue(idata["extensions"]["ali-factory"]["enabled"])

            # Check updated settings.json
            sdata = json.loads(fake_settings.read_text(encoding="utf-8"))
            self.assertTrue(sdata["agent_settings"]["agent_context"]["load_user_skills"])
            self.assertTrue(sdata["agent_settings"]["agent_context"]["load_project_skills"])
            self.assertIn("ali-factory", sdata["misc_settings"]["app_preferences"]["enabled_skills"])

    def test_inject_skill_into_repo(self):
        target_repo = Path(self.test_dir) / "sample-target-repo"
        target_repo.mkdir()

        ok = cli.inject_skill_into_repo(str(target_repo))
        self.assertTrue(ok)
        injected_dir = target_repo / ".openhands" / "skills" / "ali-factory"
        self.assertTrue((injected_dir / "SKILL.md").exists())
        self.assertTrue((injected_dir / "scripts").is_dir())

    def test_check_openhands_health(self):
        # Health check on an unreachable port should return False
        self.assertFalse(cli.check_openhands_health("http://127.0.0.1:59999"))


if __name__ == "__main__":
    unittest.main()
