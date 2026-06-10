from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import write_json, write_text
from director_workflow.scaffold import scaffold_run
from director_workflow.semantic_tools import (
    build_revision_plan,
    build_semantic_review_prompt,
    run_codex_semantic_review,
    validate_semantic_review,
)


def valid_semantic_review() -> dict:
    return {
        "status": "complete",
        "overall_score": 86,
        "decision": "pass",
        "summary": "可以发布",
        "script_review": {"score": 86, "findings": [], "revision_notes": []},
        "shot_review": {"score": 84, "findings": [], "revision_notes": []},
        "asset_review": {"score": 82, "findings": [], "revision_notes": []},
        "render_review": {"score": 88, "findings": [], "revision_notes": []},
        "required_changes": [],
        "optional_changes": [],
    }


class SemanticToolsTest(unittest.TestCase):
    def test_scaffold_creates_semantic_review_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            self.assertTrue((run_dir / "semantic_review.json").exists())
            self.assertTrue((run_dir / "revision_plan.json").exists())

    def test_build_semantic_prompt_writes_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_text(run_dir / "approved_script.md", "第一句有钩子。第二句有画面。")
            write_json(run_dir / "source_notes.json", {"sources": [{"publisher": "官方来源"}]})
            write_json(run_dir / "shot_plan.json", {"shots": [{"id": "s01", "narration": "第一句有钩子。"}]})
            asset = run_dir / "assets" / "s01.png"
            asset.parent.mkdir(exist_ok=True)
            asset.write_bytes(b"not a real image")
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "bindings": [
                        {
                            "shot_id": "s01",
                            "material_type": "codex_image",
                            "asset_path": "assets/s01.png",
                            "source_provider": "codex_imagegen",
                            "approved": True,
                        }
                    ]
                },
            )
            prompt = build_semantic_review_prompt(run_dir)
            self.assertIn("MoneyPrinterTurbo 短视频导演审核智能体", prompt)
            self.assertIn("overall_score", prompt)
            self.assertIn("bound_asset_summary", prompt)
            self.assertIn("source_notes", prompt)
            self.assertIn("官方来源", prompt)
            self.assertIn("codex_imagegen", prompt)
            self.assertTrue((run_dir / "reports" / "semantic_review_prompt.md").exists())

    def test_revision_plan_collects_script_and_render_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_json(
                run_dir / "script_audit.json",
                {
                    "min_chars": 180,
                    "max_chars": 210,
                    "candidates": [
                        {
                            "id": "v1",
                            "audit": {
                                "chars": 90,
                                "long_sentences": ["这是一句太长的测试句子"],
                                "checks": {"char_count_ok": False},
                            },
                        }
                    ],
                },
            )
            write_json(
                run_dir / "qa_report.json",
                {
                    "checks": {"duration_close": False},
                    "duration_seconds": 51,
                    "target_duration_seconds": 45,
                    "duration_error_ratio": 0.1333,
                    "black_events": [],
                },
            )
            plan = build_revision_plan(run_dir)
            self.assertEqual(plan["decision"], "revise")
            self.assertGreaterEqual(len(plan["actions"]), 2)

    def test_semantic_review_validation_blocks_dry_run(self) -> None:
        validation = validate_semantic_review({"status": "dry_run", "decision": "not_reviewed"})

        self.assertFalse(validation["valid"])
        self.assertIn("status must be complete", validation["issues"])

    def test_revision_plan_fails_closed_without_valid_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_json(run_dir / "script_audit.json", {"candidates": []})
            write_json(
                run_dir / "qa_report.json",
                {
                    "checks": {
                        "file_exists": True,
                        "probe_ok": True,
                        "has_video": True,
                        "has_audio": True,
                        "portrait_1080x1920_or_better": True,
                        "duration_close": True,
                        "blackdetect_ok": True,
                        "no_black_events": True,
                        "volume_scan_ok": True,
                    },
                    "black_events": [],
                },
            )
            write_json(run_dir / "semantic_review.json", {"status": "dry_run", "decision": "not_reviewed"})

            plan = build_revision_plan(run_dir)

            self.assertEqual(plan["decision"], "revise")
            self.assertTrue(any(action["area"] == "semantic" for action in plan["actions"]))

    def test_revision_plan_can_be_ready_after_valid_pass_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_json(run_dir / "script_audit.json", {"candidates": []})
            write_json(
                run_dir / "qa_report.json",
                {
                    "checks": {
                        "file_exists": True,
                        "probe_ok": True,
                        "has_video": True,
                        "has_audio": True,
                        "portrait_1080x1920_or_better": True,
                        "duration_close": True,
                        "blackdetect_ok": True,
                        "no_black_events": True,
                        "volume_scan_ok": True,
                    },
                    "black_events": [],
                },
            )
            write_json(run_dir / "semantic_review.json", valid_semantic_review())
            write_json(run_dir / "asset_binding_report.json", {"status": "complete", "decision": "pass", "issues": []})

            plan = build_revision_plan(run_dir)

            self.assertEqual(plan["decision"], "ready")

    def test_revision_plan_prefers_approved_script_audit_over_old_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_json(
                run_dir / "script_audit.json",
                {
                    "min_chars": 10,
                    "max_chars": 80,
                    "approved_script": {"audit": {"checks": {"char_count_ok": True}, "long_sentences": []}},
                    "candidates": [
                        {
                            "id": "old",
                            "audit": {
                                "chars": 2,
                                "checks": {"char_count_ok": False},
                                "long_sentences": ["旧候选长句不应污染当前定稿"],
                            },
                        }
                    ],
                },
            )
            write_json(
                run_dir / "qa_report.json",
                {
                    "checks": {
                        "file_exists": True,
                        "probe_ok": True,
                        "has_video": True,
                        "has_audio": True,
                        "portrait_1080x1920_or_better": True,
                        "duration_close": True,
                        "blackdetect_ok": True,
                        "no_black_events": True,
                        "volume_scan_ok": True,
                    },
                    "black_events": [],
                },
            )
            write_json(run_dir / "semantic_review.json", valid_semantic_review())
            write_json(run_dir / "asset_binding_report.json", {"status": "complete", "decision": "pass", "issues": []})

            plan = build_revision_plan(run_dir)

            self.assertEqual(plan["decision"], "ready")

    def test_codex_semantic_review_passes_prompt_via_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = scaffold_run("测试主题", run_dir=Path(tmp) / "run")
            write_text(run_dir / "approved_script.md", "第一句有钩子。")
            raw = valid_semantic_review()

            def fake_run(cmd, **kwargs):
                self.assertEqual(cmd[-1], "-")
                self.assertIn("MoneyPrinterTurbo 短视频导演审核智能体", kwargs.get("input", ""))
                output_path = Path(cmd[cmd.index("--output-last-message") + 1])
                output_path.write_text(__import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            # 只 mock semantic_tools 里实际发起的 Codex CLI 调用，
            # 同时隔离 config._read_deepseek_key_from_keychain 避免串扰
            with patch("director_workflow.semantic_tools.subprocess.run", fake_run), \
                 patch("director_workflow.config._read_deepseek_key_from_keychain", return_value=""):
                review = run_codex_semantic_review(run_dir)

            self.assertEqual(review["decision"], "pass")
            self.assertTrue(review["validation"]["valid"])


if __name__ == "__main__":
    unittest.main()
