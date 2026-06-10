from __future__ import annotations

import json
import http.client
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import read_json
from director_workflow.script_tools import (
    audit_candidates,
    build_codex_script_director_prompt,
    call_deepseek,
    call_deepseek_api,
    promote_recommended_script,
    save_generated_candidates,
    validate_generated_candidates,
)


class ScriptToolsTest(unittest.TestCase):
    def test_validate_generated_candidates_requires_list(self) -> None:
        with self.assertRaises(ValueError):
            validate_generated_candidates({"candidates": "not-a-list"})

    def test_save_generated_candidates_keeps_rejected_items_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            save_generated_candidates(
                run_dir,
                {
                    "candidates": [
                        "bad",
                        {"id": "v1", "script": "第一句有钩子。", "search_terms": ["city commute"], "reason": "可配画面"},
                        {"id": "v2", "script": "", "search_terms": ["office"]},
                    ]
                },
                model="deepseek-v4-flash",
            )

            saved = read_json(run_dir / "script_candidates.json")

            self.assertEqual(saved["status"], "complete")
            self.assertEqual(len(saved["candidates"]), 1)
            self.assertEqual(saved["candidates"][0]["id"], "v1")
            self.assertEqual(len(saved["rejected_candidates"]), 2)

    def test_call_deepseek_api_extracts_json_from_message_content(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return (
                    '{"choices":[{"message":{"content":"{\\"candidates\\":[{\\"id\\":\\"v1\\",'
                    '\\"script\\":\\"第一句。\\",\\"search_terms\\":[\\"office\\"]}]}"}}]}'
                ).encode("utf-8")

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return FakeResponse()

        with (
            patch("director_workflow.script_tools.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("director_workflow.script_tools.deepseek_api_credentials", return_value=("secret", "https://api.deepseek.test", "deepseek-test")),
        ):
            payload = call_deepseek_api("prompt")

        self.assertEqual(payload["candidates"][0]["id"], "v1")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})  # type: ignore[index]

    def test_call_deepseek_auto_falls_back_to_cli(self) -> None:
        with (
            patch("director_workflow.script_tools.call_deepseek_api", side_effect=RuntimeError("api down")),
            patch("director_workflow.script_tools.call_deepseek_cli", return_value={"candidates": []}) as cli,
        ):
            payload = call_deepseek("prompt", provider="auto")

        self.assertEqual(payload, {"candidates": []})
        cli.assert_called_once()

    def test_call_deepseek_auto_falls_back_to_cli_on_invalid_json(self) -> None:
        with (
            patch("director_workflow.script_tools.call_deepseek_api", side_effect=ValueError("bad json")),
            patch("director_workflow.script_tools.call_deepseek_cli", return_value={"candidates": []}) as cli,
        ):
            payload = call_deepseek("prompt", provider="auto")

        self.assertEqual(payload, {"candidates": []})
        cli.assert_called_once()

    def test_call_deepseek_api_converts_incomplete_read_to_runtime_error(self) -> None:
        class BrokenResponse:
            def __enter__(self) -> "BrokenResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                raise http.client.IncompleteRead(b"")

        with (
            patch("director_workflow.script_tools.urllib.request.urlopen", return_value=BrokenResponse()),
            patch("director_workflow.script_tools.deepseek_api_credentials", return_value=("secret", "https://api.deepseek.test", "deepseek-test")),
        ):
            with self.assertRaisesRegex(RuntimeError, "while reading response"):
                call_deepseek_api("prompt")

    def test_codex_script_prompt_includes_sentence_count_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "brief.json").write_text(
                '{"estimated_script_chars":{"min":237,"max":284}}',
                encoding="utf-8",
            )
            (run_dir / "script_candidates.json").write_text('{"candidates":[]}', encoding="utf-8")

            prompt = build_codex_script_director_prompt(run_dir)

        self.assertIn("总句数必须是 8-16 句", prompt)

    def test_promote_recommended_script_writes_approved_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            save_generated_candidates(
                run_dir,
                {
                    "candidates": [
                        {"id": "v1", "script": "第一句。", "search_terms": ["office"]},
                        {"id": "v2", "script": "第二句更好。", "search_terms": ["newsroom"]},
                    ]
                },
                model="deepseek-test",
            )
            (run_dir / "script_audit.json").write_text('{"recommended_id":"v2"}', encoding="utf-8")

            report = promote_recommended_script(run_dir)

            self.assertEqual(report["selected_id"], "v2")
            self.assertIn("第二句更好。", (run_dir / "approved_script.md").read_text(encoding="utf-8"))

    def test_audit_candidates_includes_current_approved_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "brief.json").write_text(
                '{"estimated_script_chars":{"min":10,"max":80}}',
                encoding="utf-8",
            )
            save_generated_candidates(
                run_dir,
                {"candidates": [{"id": "v1", "script": "太短。", "search_terms": ["newsroom"]}]},
                model="deepseek-test",
            )
            (run_dir / "approved_script.md").write_text(
                "第一句有钩子。第二句有画面。第三句给事实。第四句接原因。第五句能收住。",
                encoding="utf-8",
            )

            report = audit_candidates(run_dir)

            self.assertIn("approved_script", report)
            self.assertTrue(report["approved_script"]["audit"]["pass"])

    def test_promote_recommended_script_rejects_failed_audit_when_brief_has_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "brief.json").write_text(
                '{"estimated_script_chars":{"min":30,"max":50}}',
                encoding="utf-8",
            )
            save_generated_candidates(
                run_dir,
                {"candidates": [{"id": "v1", "script": "太短。", "search_terms": ["newsroom"]}]},
                model="deepseek-test",
            )
            audit_candidates(run_dir)

            with self.assertRaisesRegex(ValueError, "No script candidate passed audit"):
                promote_recommended_script(run_dir)

            report = promote_recommended_script(run_dir, allow_out_of_range=True)
            self.assertEqual(report["selected_id"], "v1")


if __name__ == "__main__":
    unittest.main()
