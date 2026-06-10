from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.config import load_runtime_config
from director_workflow.endpoint_tools import DEFAULT_MPT_ENDPOINT
from director_workflow.io_utils import read_json, write_json
from director_workflow.runtime import JobRecord, create_run, list_runs, resolve_run_dir, run_auto_pipeline, run_workflow_step


class RuntimeTest(unittest.TestCase):
    def _config(self, runs_dir: str) -> object:
        patcher = patch.dict(
            os.environ,
            {
                "MPT_API_ENDPOINT": DEFAULT_MPT_ENDPOINT,
                "DIRECTOR_RUNS_DIR": runs_dir,
            },
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return load_runtime_config()

    def test_create_and_list_runs_uses_configured_runs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)

            run = create_run("测试主题", 60, "douyin", "新闻解读", config=config)
            runs = list_runs(config=config)

            self.assertEqual(run["topic"], "测试主题")
            self.assertTrue(Path(tmp).resolve() in Path(run["path"]).resolve().parents)
            self.assertEqual(runs[0]["run_id"], run["run_id"])

    def test_resolve_run_dir_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)

            with self.assertRaises(ValueError):
                resolve_run_dir("../outside", config=config)
            with self.assertRaises(ValueError):
                resolve_run_dir("", config=config)

    def test_run_whitelisted_step_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)

            record = run_workflow_step(run["run_id"], "audit_script", config=config)

            self.assertEqual(record.status, "succeeded")
            self.assertEqual(record.returncode, 0)
            self.assertTrue((Path(run["path"]) / "script_audit.json").exists())

    def test_build_image_tasks_step_refreshes_handoff_for_webui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)
            run_dir = Path(run["path"])
            write_json(
                run_dir / "shot_plan.json",
                {
                    "status": "complete",
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "start": 0,
                            "end": 5,
                            "duration": 5,
                            "material_type": "codex_image",
                            "visual_intent": "新闻分析画面",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 news analysis image, no text",
                        }
                    ],
                },
            )

            record = run_workflow_step(run["run_id"], "build_image_tasks", config=config)
            handoff = read_json(run_dir / "image_generation_handoff.json")

            self.assertEqual(record.status, "succeeded")
            self.assertEqual(handoff["status"], "waiting_for_codex_imagegen")
            self.assertEqual(handoff["pending_count"], 1)

    def test_auto_pipeline_stops_on_required_stage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)

            with patch("director_workflow.runtime.run_workflow_step") as mocked:
                failed = type("Stage", (), {})()
                failed.command = ["cmd"]
                failed.stdout = ""
                failed.stderr = ""
                failed.error = "boom"
                failed.status = "failed"
                mocked.return_value = failed

                record = run_auto_pipeline(run["run_id"], config=config)

            self.assertEqual(record.status, "failed")
            self.assertIn("generate_drafts", record.error)

    def test_auto_pipeline_fails_when_quality_gate_needs_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)
            run_dir = Path(run["path"])

            def fake_step(run_id: str, step_id: str, **_kwargs: object) -> JobRecord:
                if step_id == "build_asset_bindings":
                    write_json(run_dir / "asset_binding_report.json", {"decision": "pass"})
                if step_id == "qa_render":
                    write_json(run_dir / "qa_report.json", {"decision": "needs_revision"})
                if step_id == "semantic_review":
                    write_json(run_dir / "semantic_review.json", {"decision": "pass"})
                if step_id == "revision_plan":
                    write_json(run_dir / "revision_plan.json", {"decision": "revise"})
                return JobRecord(
                    id=f"stage-{step_id}",
                    run_id=run_id,
                    step_id=step_id,
                    status="succeeded",
                    returncode=0,
                )

            with (
                patch("director_workflow.runtime.run_workflow_step", side_effect=fake_step),
                patch("director_workflow.runtime.resolve_mpt_output_video", return_value=run_dir / "final.mp4"),
            ):
                record = run_auto_pipeline(run["run_id"], config=config)

            self.assertEqual(record.status, "failed")
            self.assertIn("auto_pipeline needs revision", record.error)

    def test_auto_pipeline_waits_for_codex_image_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)
            run_dir = Path(run["path"])

            def fake_step(run_id: str, step_id: str, **_kwargs: object) -> JobRecord:
                if step_id == "build_image_tasks":
                    write_json(
                        run_dir / "image_tasks.json",
                        {
                            "status": "ready",
                            "tasks": [
                                {
                                    "shot_id": "s01",
                                    "status": "pending_generation",
                                    "prompt": "Vertical 9:16 editorial image",
                                    "suggested_output": str(run_dir / "assets" / "codex_images" / "s01.png"),
                                }
                            ],
                        },
                    )
                return JobRecord(
                    id=f"stage-{step_id}",
                    run_id=run_id,
                    step_id=step_id,
                    status="succeeded",
                    returncode=0,
                )

            with patch("director_workflow.runtime.run_workflow_step", side_effect=fake_step):
                record = run_auto_pipeline(run["run_id"], config=config)

            self.assertEqual(record.status, "waiting_for_imagegen")
            self.assertTrue((run_dir / "image_generation_handoff.json").exists())
            self.assertIn("waiting_for_codex_imagegen", record.stdout)

    def test_auto_pipeline_resumes_after_codex_image_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            run = create_run("测试主题", 45, "douyin", "新闻解读", config=config)
            run_dir = Path(run["path"])
            write_json(run_dir / "image_generation_handoff.json", {"status": "complete", "pending_count": 0})
            called_steps: list[str] = []

            def fake_step(run_id: str, step_id: str, **_kwargs: object) -> JobRecord:
                called_steps.append(step_id)
                if step_id == "build_asset_bindings":
                    write_json(run_dir / "asset_binding_report.json", {"decision": "pass"})
                if step_id == "qa_render":
                    write_json(run_dir / "qa_report.json", {"decision": "pass"})
                if step_id == "semantic_review":
                    write_json(run_dir / "semantic_review.json", {"decision": "pass"})
                if step_id == "revision_plan":
                    write_json(run_dir / "revision_plan.json", {"decision": "ready"})
                return JobRecord(
                    id=f"stage-{step_id}",
                    run_id=run_id,
                    step_id=step_id,
                    status="succeeded",
                    returncode=0,
                )

            with (
                patch("director_workflow.runtime.run_workflow_step", side_effect=fake_step),
                patch("director_workflow.runtime.resolve_mpt_output_video", return_value=run_dir / "final.mp4"),
            ):
                record = run_auto_pipeline(run["run_id"], config=config)

            self.assertEqual(record.status, "succeeded")
            self.assertNotIn("generate_drafts", called_steps)
            self.assertIn("build_asset_bindings", called_steps)
            self.assertIn("resume_after_imagegen=true", record.stdout)


if __name__ == "__main__":
    unittest.main()
