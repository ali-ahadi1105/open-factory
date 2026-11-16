import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

from app import (
    extract_text_content,
    parse_openhands_event,
    load_openhands_config,
    save_openhands_config,
    get_openhands_client_config,
    get_openhands_config,
    update_openhands_config,
    OpenHandsConfigUpdate,
)


class TestOpenHandsIntegration(unittest.TestCase):
    def test_extract_text_content(self):
        self.assertEqual(extract_text_content("hello"), "hello")
        self.assertEqual(extract_text_content([{"text": "foo"}, {"text": "bar"}]), "foo\nbar")
        self.assertEqual(extract_text_content({"text": "single"}), "single")
        self.assertEqual(extract_text_content({"content": [{"text": "nested"}]}), "nested")
        self.assertEqual(extract_text_content(""), "")

    def test_parse_openhands_event_action(self):
        ev = {
            "id": "e-1",
            "kind": "ActionEvent",
            "source": "agent",
            "tool_name": "terminal",
            "action": {"command": "npm test", "kind": "TerminalAction"},
            "thought": [{"type": "text", "text": "Let us run tests"}],
        }
        parsed = parse_openhands_event(ev)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["type"], "command")
        self.assertEqual(parsed["content"], "npm test")
        self.assertEqual(parsed["thought"], "Let us run tests")
        self.assertFalse(parsed["is_error"])

    def test_parse_openhands_event_observation(self):
        ev = {
            "id": "e-2",
            "kind": "ObservationEvent",
            "source": "environment",
            "tool_name": "terminal",
            "observation": {"content": "PASS src/App.test.tsx", "is_error": False},
        }
        parsed = parse_openhands_event(ev)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["type"], "output")
        self.assertEqual(parsed["content"], "PASS src/App.test.tsx")
        self.assertFalse(parsed["is_error"])

    def test_parse_openhands_event_observation_error(self):
        ev = {
            "id": "e-3",
            "kind": "ObservationEvent",
            "source": "environment",
            "tool_name": "terminal",
            "observation": {"content": "FAIL src/App.test.tsx: Jest worker encountered exceptions", "is_error": True},
        }
        parsed = parse_openhands_event(ev)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["type"], "error")
        self.assertTrue(parsed["is_error"])

    def test_parse_openhands_event_message(self):
        # Format 1: direct content
        ev1 = {
            "id": "e-4",
            "kind": "MessageEvent",
            "role": "user",
            "content": [{"type": "text", "text": "Skip test suite and run without coverage"}],
        }
        parsed1 = parse_openhands_event(ev1)
        self.assertIsNotNone(parsed1)
        self.assertEqual(parsed1["type"], "user_message")
        self.assertIn("Skip test suite", parsed1["content"])

        # Format 2: llm_message format as sent by OpenHands Agent Server
        ev2 = {
            "id": "e-5",
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "تست‌ها با موفقیت پاس شدند"}],
                "reasoning_content": "User asked for status"
            }
        }
        parsed2 = parse_openhands_event(ev2)
        self.assertIsNotNone(parsed2)
        self.assertEqual(parsed2["type"], "agent_message")
        self.assertEqual(parsed2["content"], "تست‌ها با موفقیت پاس شدند")
        self.assertEqual(parsed2["thought"], "User asked for status")

    def test_get_and_update_openhands_config(self):
        cfg = get_openhands_config()
        self.assertIn("mode", cfg)
        self.assertIn("local", cfg)
        self.assertIn("cloud", cfg)

        # Update to cloud
        req = OpenHandsConfigUpdate(
            mode="cloud",
            cloud_base_url="https://app.all-hands.dev",
            cloud_api_key="test_cloud_key_12345",
        )
        updated = update_openhands_config(req)
        self.assertEqual(updated["mode"], "cloud")
        self.assertTrue(updated["cloud"]["has_key"])

        # Reset back to local
        req2 = OpenHandsConfigUpdate(mode="local")
        reset_cfg = update_openhands_config(req2)
        self.assertEqual(reset_cfg["mode"], "local")


    def test_list_openhands_conversations_graceful_fallback(self):
        from app import list_openhands_conversations
        # Unreachable OpenHands should return graceful fallback dictionary without 502 crash
        res = list_openhands_conversations(limit=5)
        self.assertIn("conversations", res)
        self.assertIsInstance(res["conversations"], list)


if __name__ == "__main__":
    unittest.main()
