from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.asset_tools import build_asset_binding_template, validate_asset_bindings
from director_workflow.io_utils import read_json, write_json


class AssetToolsTest(unittest.TestCase):
    def test_asset_binding_template_fails_until_binding_is_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            asset = run_dir / "assets" / "stock" / "shot.mp4"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"placeholder")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "material_type": "stock_video",
                            "visual_intent": "创作者在电脑前剪视频",
                            "search_terms": ["creator editing video"],
                        }
                    ]
                },
            )
            write_json(
                run_dir / "asset_manifest.json",
                {
                    "assets": [
                        {
                            "path": "assets/stock/shot.mp4",
                            "issues": [],
                        }
                    ]
                },
            )

            result = build_asset_binding_template(run_dir)

            self.assertEqual(result["asset_binding_report"]["decision"], "needs_revision")
            self.assertTrue((run_dir / "asset_bindings.json").exists())

    def test_approved_asset_binding_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            asset = run_dir / "assets" / "stock" / "shot.mp4"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"placeholder")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "material_type": "stock_video",
                            "visual_intent": "创作者在电脑前剪视频",
                            "search_terms": ["creator editing video"],
                        }
                    ]
                },
            )
            write_json(run_dir / "asset_manifest.json", {"assets": [{"path": "assets/stock/shot.mp4", "issues": []}]})
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "bindings": [
                        {
                            "shot_id": "s01",
                            "material_type": "stock_video",
                            "asset_path": "assets/stock/shot.mp4",
                            "source_provider": "pixabay",
                            "license_status": "verified",
                            "commercial_use_status": "platform_license_verified",
                            "fit_score": 82,
                            "approved": True,
                        }
                    ]
                },
            )

            report = validate_asset_bindings(run_dir)
            saved = read_json(run_dir / "asset_binding_report.json")

            self.assertEqual(report["decision"], "pass")
            self.assertEqual(saved["decision"], "pass")

    def test_extra_asset_binding_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            asset = run_dir / "assets" / "stock" / "shot.mp4"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"placeholder")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "material_type": "stock_video",
                            "visual_intent": "创作者在电脑前剪视频",
                            "search_terms": ["creator editing video"],
                        }
                    ]
                },
            )
            write_json(run_dir / "asset_manifest.json", {"assets": [{"path": "assets/stock/shot.mp4", "issues": []}]})
            base_binding = {
                "material_type": "stock_video",
                "asset_path": "assets/stock/shot.mp4",
                "source_provider": "pexels",
                "license_status": "verified",
                "commercial_use_status": "platform_license_verified",
                "fit_score": 90,
                "approved": True,
            }
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "bindings": [
                        {"shot_id": "s01", **base_binding},
                        {"shot_id": "s99", **base_binding},
                    ]
                },
            )

            report = validate_asset_bindings(run_dir)

            self.assertEqual(report["decision"], "needs_revision")
            self.assertEqual(report["issues"][0]["shot_id"], "s99")
            self.assertEqual(report["issues"][0]["issue"], "binding does not exist in shot_plan")


if __name__ == "__main__":
    unittest.main()
