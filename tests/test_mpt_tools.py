from __future__ import annotations

import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import write_json, write_text
from director_workflow.mpt_tools import (
    build_mpt_params,
    build_shot_plan_from_script,
    collect_representative_video_terms,
    derive_video_clip_duration,
    local_material_filename,
    post_mpt_task,
    resolve_mpt_output_video,
    sync_approved_assets_for_mpt,
    validate_local_mpt_endpoint,
    validate_shot_plan,
    wait_mpt_task,
)


class MptToolsTest(unittest.TestCase):
    def test_endpoint_validation_blocks_external_hosts(self) -> None:
        self.assertEqual(
            validate_local_mpt_endpoint("http://127.0.0.1:8080/api/v1/videos"),
            "http://127.0.0.1:8080/api/v1/videos",
        )
        with self.assertRaises(ValueError):
            validate_local_mpt_endpoint("https://127.0.0.1:8080/api/v1/videos")
        with self.assertRaises(ValueError):
            validate_local_mpt_endpoint("http://example.com/api/v1/videos")
        with self.assertRaises(ValueError):
            validate_local_mpt_endpoint("http://127.0.0.1:8080/admin")

    def test_post_mpt_task_reports_unreachable_api_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(
                run_dir / "mpt_params.json",
                {
                    "endpoint": "http://127.0.0.1:8080/api/v1/videos",
                    "params": {"video_subject": "测试"},
                },
            )

            with patch(
                "director_workflow.mpt_tools.urllib.request.urlopen",
                side_effect=urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
            ):
                with self.assertRaisesRegex(RuntimeError, "MPT API is not reachable"):
                    post_mpt_task(run_dir)

    def test_generated_shot_plan_requires_director_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n第一句有钩子。第二句有画面。")
            write_json(run_dir / "brief.json", {"topic": "测试", "aspect": "9:16"})

            plan = build_shot_plan_from_script(run_dir)

            self.assertEqual(plan["status"], "needs_director_input")
            self.assertFalse(plan["validation"]["valid"])
            with self.assertRaises(ValueError):
                build_mpt_params(run_dir)
            self.assertTrue((run_dir / "shot_plan_validation.json").exists())

    def test_valid_shot_plan_allows_mpt_payload_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n第一句有钩子。")
            write_json(run_dir / "brief.json", {"topic": "测试", "aspect": "9:16"})
            write_json(
                run_dir / "shot_plan.json",
                {
                    "status": "approved",
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句有钩子。",
                            "duration_hint_seconds": 4,
                            "material_type": "stock_video",
                            "visual_intent": "城市通勤人群刷到短视频",
                            "search_terms": ["city commute vertical video"],
                        }
                    ],
                },
            )

            validation = validate_shot_plan(
                {"shots": [{"narration": "x", "visual_intent": "y", "material_type": "stock_video", "search_terms": ["z"]}]}
            )
            self.assertTrue(validation["valid"])
            payload = build_mpt_params(run_dir, allow_draft_pexels=True)

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["params"]["video_terms"], ["city commute vertical video"])
            self.assertEqual(payload["params"]["video_source"], "pexels")
            self.assertIsNone(payload["params"]["video_materials"])
            self.assertEqual(payload["params"]["video_clip_duration"], 3)

    def test_passed_asset_bindings_switch_mpt_payload_to_local_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "sample-run"
            run_dir.mkdir()
            asset_path = run_dir / "assets" / "s01.png"
            asset_path.parent.mkdir()
            asset_path.write_bytes(b"fake image content")
            local_material_dir = run_dir / "mpt-local-videos"
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n第一句有钩子。")
            write_json(run_dir / "brief.json", {"topic": "测试", "aspect": "9:16"})
            write_json(
                run_dir / "shot_plan.json",
                {
                    "status": "approved",
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句有钩子。",
                            "duration_hint_seconds": 4,
                            "material_type": "codex_image",
                            "visual_intent": "清晰的问题卡片",
                            "codex_image_prompt": "a clean question card",
                        }
                    ],
                },
            )
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "status": "approved",
                    "bindings": [
                        {
                            "shot_id": "s01",
                            "asset_path": "assets/s01.png",
                            "source_provider": "codex_imagegen",
                            "license_status": "generated",
                            "commercial_use_status": "generated_owned",
                            "fit_score": 88,
                            "approved": True,
                        }
                    ],
                },
            )
            write_json(run_dir / "asset_binding_report.json", {"decision": "pass"})

            materials = sync_approved_assets_for_mpt(run_dir, local_material_dir=local_material_dir)
            payload = build_mpt_params(run_dir, local_material_dir=local_material_dir)

            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0]["provider"], "codex_imagegen")
            self.assertTrue(Path(materials[0]["url"]).exists())
            self.assertEqual(materials[0]["duration"], 4)
            self.assertEqual(payload["params"]["video_source"], "local")
            self.assertEqual(payload["params"]["video_concat_mode"], "sequential")
            self.assertEqual(payload["params"]["video_clip_duration"], 4)
            self.assertEqual(payload["params"]["video_materials"][0]["url"], materials[0]["url"])

    def test_build_mpt_params_allows_explicit_clip_duration_for_local_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "sample-run"
            run_dir.mkdir()
            asset_path = run_dir / "assets" / "s01.png"
            asset_path.parent.mkdir()
            asset_path.write_bytes(b"fake image content")
            local_material_dir = run_dir / "mpt-local-videos"
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n第一句有钩子。")
            write_json(run_dir / "brief.json", {"topic": "测试", "aspect": "9:16", "video_clip_duration_seconds": 3})
            write_json(
                run_dir / "shot_plan.json",
                {
                    "status": "approved",
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句有钩子。",
                            "duration_hint_seconds": 8,
                            "material_type": "codex_image",
                            "visual_intent": "清晰的问题卡片",
                            "codex_image_prompt": "a clean question card",
                        }
                    ],
                },
            )
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "status": "approved",
                    "bindings": [
                        {
                            "shot_id": "s01",
                            "asset_path": "assets/s01.png",
                            "source_provider": "codex_imagegen",
                            "license_status": "generated",
                            "commercial_use_status": "generated_owned",
                            "fit_score": 88,
                            "approved": True,
                        }
                    ],
                },
            )
            write_json(run_dir / "asset_binding_report.json", {"decision": "pass"})

            payload = build_mpt_params(run_dir, local_material_dir=local_material_dir)

            self.assertEqual(payload["params"]["video_clip_duration"], 3)

    def test_build_mpt_params_fails_closed_when_asset_bindings_are_not_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n第一句有钩子。")
            write_json(run_dir / "brief.json", {"topic": "测试", "aspect": "9:16"})
            write_json(
                run_dir / "shot_plan.json",
                {
                    "status": "approved",
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句有钩子。",
                            "duration_hint_seconds": 4,
                            "material_type": "stock_video",
                            "visual_intent": "城市通勤人群刷到短视频",
                            "search_terms": ["city commute vertical video"],
                        }
                    ],
                },
            )
            write_json(run_dir / "asset_binding_report.json", {"decision": "pass"})

            with self.assertRaises(ValueError):
                build_mpt_params(run_dir)

            self.assertEqual((run_dir / "mpt_params.json").exists(), True)

    def test_derive_video_clip_duration_uses_shot_hint_median(self) -> None:
        self.assertEqual(
            derive_video_clip_duration({"shots": [{"duration_hint_seconds": 4}, {"duration_hint_seconds": 6}]}),
            5,
        )
        self.assertEqual(
            derive_video_clip_duration(
                {"shots": [{"duration_hint_seconds": 5} for _ in range(9)]},
                target_duration_seconds=45,
            ),
            6,
        )
        self.assertEqual(derive_video_clip_duration({"shots": [{"duration_hint_seconds": 20}]}), 8)
        self.assertEqual(derive_video_clip_duration({"shots": []}), 5)

    def test_local_material_filename_uses_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            asset = run_dir / "asset.png"
            asset.write_bytes(b"one")
            first = local_material_filename(run_dir, "s01", asset)
            asset.write_bytes(b"two")
            second = local_material_filename(run_dir, "s01", asset)

            self.assertNotEqual(first, second)

    def test_wait_mpt_task_records_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "mpt_params.json", {"endpoint": "http://127.0.0.1:8080/api/v1/videos"})
            write_json(run_dir / "mpt_submit_result.json", {"data": {"task_id": "task-1"}})

            with patch(
                "director_workflow.mpt_tools.query_mpt_task",
                return_value={"data": {"state": 1, "progress": 100, "videos": ["/tasks/task-1/final-1.mp4"]}},
            ):
                report = wait_mpt_task(run_dir, poll_seconds=0, timeout_seconds=1, stall_seconds=1)

            self.assertEqual(report["status"], "complete")
            self.assertTrue((run_dir / "mpt_wait_report.json").exists())

    def test_collect_video_terms_preserves_each_stock_shot_first(self) -> None:
        plan = {
            "shots": [
                {"material_type": "stock_video", "search_terms": ["s1 primary", "s1 extra"]},
                {"material_type": "codex_image", "search_terms": []},
                {"material_type": "stock_video", "search_terms": ["s2 primary", "s2 extra"]},
            ]
        }

        terms = collect_representative_video_terms(plan, limit=3)

        self.assertEqual(terms, ["s1 primary", "s2 primary", "s1 extra"])

    def test_resolve_mpt_output_video_maps_task_url_to_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            mpt_dir = root / "MoneyPrinterTurbo"
            video_path = mpt_dir / "storage" / "tasks" / "task-1" / "final-1.mp4"
            video_path.parent.mkdir(parents=True)
            video_path.write_bytes(b"video")
            run_dir.mkdir()
            write_json(run_dir / "mpt_wait_report.json", {"task": {"videos": ["/tasks/task-1/final-1.mp4"]}})

            self.assertEqual(resolve_mpt_output_video(run_dir, mpt_dir=mpt_dir), video_path.resolve())


if __name__ == "__main__":
    unittest.main()
