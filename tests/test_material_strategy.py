from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import read_json, write_json, write_text
from director_workflow.material_strategy import (
    build_material_strategy_prompt,
    build_rule_based_material_strategy,
    build_stock_only_material_strategy,
    summarize_material_mix,
    validate_material_strategy,
)


class MaterialStrategyTest(unittest.TestCase):
    def test_rule_based_strategy_splits_stock_and_codex_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "brief.json", {"topic": "比亚迪财报分析", "aspect": "9:16"})
            write_text(
                run_dir / "approved_script.md",
                "# Approved Script\n\n比亚迪工厂产线正在加速。财报里的现金流变化更值得看。",
            )

            plan = build_rule_based_material_strategy(run_dir)
            mix = summarize_material_mix(plan)

            self.assertTrue(plan["validation"]["valid"])
            self.assertEqual(mix["stock_video"], 1)
            self.assertEqual(mix["codex_image"], 1)
            self.assertTrue((run_dir / "material_strategy.json").exists())
            self.assertTrue((run_dir / "image_tasks.json").exists())
            image_tasks = read_json(run_dir / "image_tasks.json")
            self.assertEqual(len(image_tasks["tasks"]), 1)

    def test_stock_only_strategy_avoids_codex_image_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "brief.json", {"topic": "财报分析", "aspect": "9:16"})
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n营收变化值得关注。现金流也要看。")

            plan = build_stock_only_material_strategy(run_dir)

            self.assertTrue(plan["validation"]["valid"])
            self.assertEqual({shot["material_type"] for shot in plan["shots"]}, {"stock_video"})

    def test_material_strategy_validation_requires_type_specific_fields(self) -> None:
        report = validate_material_strategy(
            {
                "shots": [
                    {
                        "id": "s01",
                        "narration": "第一句。",
                        "duration_hint_seconds": 5,
                        "material_type": "stock_video",
                        "visual_intent": "真实办公室",
                        "search_terms": [],
                        "codex_image_prompt": "",
                    }
                ]
            }
        )

        self.assertFalse(report["valid"])
        self.assertIn("search_terms", str(report["issues"]))

    def test_codex_prompt_forces_pexels_as_primary_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "brief.json", {"topic": "美国关税新闻", "aspect": "9:16"})
            write_text(run_dir / "approved_script.md", "# Approved Script\n\n关税提案仍在流程阶段。")

            prompt = build_material_strategy_prompt(run_dir)

            self.assertIn("primary_api_provider 固定为 pexels", prompt)
            self.assertIn("不要默认使用 Pixabay", prompt)


if __name__ == "__main__":
    unittest.main()
