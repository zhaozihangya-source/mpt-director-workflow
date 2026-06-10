from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.image_tools import build_codex_image_tasks, register_codex_image
from director_workflow.io_utils import read_json, write_json


class ImageToolsTest(unittest.TestCase):
    def test_build_codex_image_tasks_exports_prompt_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "material_type": "codex_image",
                            "visual_intent": "独立创作者在电脑前规划视频",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 creator planning a video, no text",
                        }
                    ]
                },
            )

            payload = build_codex_image_tasks(run_dir)

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(len(payload["tasks"]), 1)
            self.assertTrue(Path(payload["tasks"][0]["prompt_file"]).exists())

    def test_register_codex_image_copies_file_and_updates_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source = Path(tmp) / "generated.png"
            source.write_bytes(b"fake-image")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "material_type": "codex_image",
                            "visual_intent": "独立创作者在电脑前规划视频",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 creator planning a video, no text",
                        }
                    ]
                },
            )
            write_json(run_dir / "image_tasks.json", {"tasks": [{"shot_id": "s01", "status": "pending_generation"}]})
            write_json(run_dir / "asset_manifest.json", {"assets": []})

            result = register_codex_image(run_dir, "s01", source)
            tasks = read_json(run_dir / "image_tasks.json")
            bindings = read_json(run_dir / "asset_bindings.json")

            self.assertTrue(Path(result["output_path"]).exists())
            self.assertEqual(tasks["tasks"][0]["status"], "generated")
            self.assertEqual(bindings["bindings"][0]["source_provider"], "codex_imagegen")

    def test_register_codex_image_upserts_missing_image_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source = Path(tmp) / "generated.png"
            source.write_bytes(b"fake-image")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s02",
                            "narration": "第二句。",
                            "material_type": "codex_image",
                            "visual_intent": "项目证据链文件夹",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 project evidence dashboard",
                        }
                    ]
                },
            )
            write_json(run_dir / "image_tasks.json", {"status": "ready", "tasks": []})
            write_json(run_dir / "asset_manifest.json", {"assets": []})

            register_codex_image(run_dir, "s02", source)
            tasks = read_json(run_dir / "image_tasks.json")

            self.assertEqual(tasks["tasks"][0]["shot_id"], "s02")
            self.assertEqual(tasks["tasks"][0]["status"], "generated")
            self.assertTrue(tasks["tasks"][0]["actual_output"])

    def test_register_codex_image_can_mark_approved_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source = Path(tmp) / "generated.png"
            source.write_bytes(b"fake-image")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s03",
                            "narration": "第三句。",
                            "material_type": "codex_image",
                            "visual_intent": "财报数据视觉",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 finance report, no text",
                        }
                    ]
                },
            )
            write_json(run_dir / "image_tasks.json", {"status": "ready", "tasks": []})
            write_json(run_dir / "asset_manifest.json", {"assets": []})

            register_codex_image(run_dir, "s03", source, approved=True)
            bindings = read_json(run_dir / "asset_bindings.json")

            self.assertTrue(bindings["bindings"][0]["approved"])

    def test_register_codex_image_accepts_suggested_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            suggested = run_dir / "assets" / "codex_images" / "s04.png"
            suggested.parent.mkdir(parents=True)
            suggested.write_bytes(b"fake-image")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s04",
                            "narration": "第四句。",
                            "material_type": "codex_image",
                            "visual_intent": "新闻摘要视觉",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 news analysis visual, no text",
                        }
                    ]
                },
            )
            write_json(run_dir / "image_tasks.json", {"tasks": [{"shot_id": "s04", "status": "pending_generation"}]})
            write_json(run_dir / "asset_manifest.json", {"assets": []})

            result = register_codex_image(run_dir, "s04", suggested, approved=True)

            self.assertEqual(Path(result["output_path"]).resolve(), suggested.resolve())
            self.assertTrue(suggested.exists())

    def test_build_codex_image_tasks_preserves_registered_image_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            generated = run_dir / "assets" / "codex_images" / "s05.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"fake-image")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s05",
                            "narration": "第五句。",
                            "material_type": "codex_image",
                            "visual_intent": "AI 数据中心视觉",
                            "search_terms": [],
                            "codex_image_prompt": "Vertical 9:16 AI data center editorial image, no text",
                        }
                    ]
                },
            )
            write_json(
                run_dir / "image_tasks.json",
                {
                    "tasks": [
                        {
                            "shot_id": "s05",
                            "status": "generated",
                            "actual_output": str(generated),
                        }
                    ]
                },
            )

            payload = build_codex_image_tasks(run_dir)

            self.assertEqual(payload["tasks"][0]["status"], "generated")
            self.assertEqual(payload["tasks"][0]["actual_output"], str(generated))


if __name__ == "__main__":
    unittest.main()
