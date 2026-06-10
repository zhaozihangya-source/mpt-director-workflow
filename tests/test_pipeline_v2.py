from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.config import RuntimeConfig
from director_workflow.io_utils import read_json, write_json, write_text
from director_workflow.pipeline_v2 import (
    MAX_SCRIPT_ATTEMPTS,
    PipelineV2,
    StageResult,
)
from director_workflow.scaffold import scaffold_run


def fake_config(tmp: Path) -> RuntimeConfig:
    return RuntimeConfig(
        root_dir=tmp,
        workflow_dir=tmp / "workflow",
        runs_dir=tmp / "runs",
        mpt_dir=tmp / "mpt",
        mpt_api_endpoint="http://127.0.0.1:8080/api/v1/videos",
        default_duration_seconds=60,
        default_platform="douyin",
        default_voice_name="gemini:Zephyr-Female",
        deepseek_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com",
        codex_executable="codex",
        social_upload_dir=None,
        pexels_configured=True,
        pixabay_configured=False,
        deepseek_api_configured=True,
        deepseek_key_source="env",
        deepseek_cli_available=False,
        codex_cli_available=False,
    )


def make_run(tmp: Path) -> Path:
    return scaffold_run("测试主题", duration_seconds=60, run_dir=tmp / "run")


REAL_SCRIPT = "# Approved Script\n\n" + "这是一段足够长的测试旁白，用来通过预审的最小字数检查。" * 4


def write_shot_plan(run_dir: Path, shots: list[dict]) -> None:
    write_json(run_dir / "shot_plan.json", {"status": "ready", "shots": shots})


def stock_shot(sid: str, terms: list[str] | None = None) -> dict:
    return {
        "id": sid,
        "narration": f"{sid} 旁白内容。",
        "duration_hint_seconds": 5,
        "material_type": "stock_video",
        "visual_intent": "真实场景",
        "search_terms": terms or [f"term {sid}"],
        "codex_image_prompt": "",
    }


class PipelineStateTest(unittest.TestCase):
    def test_state_roundtrip_and_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            state = pipe.load_state()
            pipe.save_stage(state, StageResult("script", "complete", "ok"))

            reloaded = pipe.load_state()
            self.assertEqual(reloaded["stages"]["script"]["status"], "complete")

            # run() 应跳过 script，从 materials 开始
            with patch.object(PipelineV2, "stage_materials", return_value=StageResult("materials", "failed", "stop")) as mock_mat, \
                 patch.object(PipelineV2, "stage_script") as mock_script:
                result = pipe.run()
            mock_script.assert_not_called()
            mock_mat.assert_called_once()
            self.assertEqual(result["stage"], "materials")

    def test_corrupt_state_resets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_text(run_dir / "pipeline_state.json", "{not json")
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            state = pipe.load_state()
            self.assertEqual(state["version"], 2)
            self.assertEqual(state["stages"], {})


class ScriptStageTest(unittest.TestCase):
    def test_retry_with_feedback_then_converge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            calls: list[str] = []

            def fake_tool(script_name: str, *args: str, timeout: int = 900):
                calls.append(script_name)
                if script_name == "tts_dry_run.py":
                    attempt = calls.count("tts_dry_run.py")
                    if attempt == 1:
                        write_json(run_dir / "tts_dry_run_report.json", {
                            "decision": "needs_revision",
                            "recommendation": "脚本偏长 10 秒，建议删减 30 字",
                        })
                        return 1, "", ""
                    write_json(run_dir / "tts_dry_run_report.json", {"decision": "pass"})
                    return 0, "", ""
                return 0, "", ""

            with patch.object(PipelineV2, "_run_tool", side_effect=fake_tool):
                result = pipe.stage_script()

            self.assertEqual(result.status, "complete")
            self.assertEqual(calls.count("generate_drafts.py"), 2)
            # 第二轮 generate 前 feedback 应已写入 brief；收敛后清除
            brief = read_json(run_dir / "brief.json", {})
            self.assertNotIn("revision_feedback", brief)

    def test_fail_after_max_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))

            def fake_tool(script_name: str, *args: str, timeout: int = 900):
                if script_name == "tts_dry_run.py":
                    write_json(run_dir / "tts_dry_run_report.json", {
                        "decision": "needs_revision",
                        "recommendation": "仍然太长",
                    })
                    return 1, "", ""
                return 0, "", ""

            with patch.object(PipelineV2, "_run_tool", side_effect=fake_tool):
                result = pipe.stage_script()

            self.assertEqual(result.status, "failed")
            self.assertIn(str(MAX_SCRIPT_ATTEMPTS), result.detail)
            # 失败时 feedback 留在 brief 里，供人工排查
            brief = read_json(run_dir / "brief.json", {})
            self.assertIn("revision_feedback", brief)

    def test_tts_skipped_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))

            def fake_tool(script_name: str, *args: str, timeout: int = 900):
                if script_name == "tts_dry_run.py":
                    write_json(run_dir / "tts_dry_run_report.json", {
                        "decision": "skipped", "skip_reason": "no network",
                    })
                    return 0, "", ""
                return 0, "", ""

            with patch.object(PipelineV2, "_run_tool", side_effect=fake_tool):
                result = pipe.stage_script()
            self.assertEqual(result.status, "complete")
            self.assertIn("skipped", result.detail)


class DegradeTest(unittest.TestCase):
    def test_uncovered_stock_shot_degrades_to_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_shot_plan(run_dir, [stock_shot("s01"), stock_shot("s02")])
            write_json(run_dir / "asset_bindings.json", {
                "bindings": [
                    {"shot_id": "s01", "approved": True, "asset_path": "assets/a.mp4"},
                    # s02 无绑定
                ],
            })
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            degraded = pipe.degrade_uncovered_shots()

            self.assertEqual(degraded, ["s02"])
            plan = read_json(run_dir / "shot_plan.json", {})
            s02 = plan["shots"][1]
            self.assertEqual(s02["material_type"], "codex_image")
            self.assertTrue(s02["codex_image_prompt"])
            # s01 不动
            self.assertEqual(plan["shots"][0]["material_type"], "stock_video")

    def test_unapproved_binding_counts_as_uncovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_shot_plan(run_dir, [stock_shot("s01")])
            write_json(run_dir / "asset_bindings.json", {
                "bindings": [{"shot_id": "s01", "approved": False, "asset_path": "assets/a.mp4"}],
            })
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            self.assertEqual(pipe.degrade_uncovered_shots(), ["s01"])


class SentenceDurationTest(unittest.TestCase):
    def test_durations_written_to_shot_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_shot_plan(run_dir, [stock_shot("s01"), stock_shot("s02")])
            write_json(run_dir / "tts_dry_run_report.json", {
                "decision": "pass",
                "sentence_durations": [
                    {"narration": "第一句。", "estimated_seconds": 3.2},
                    {"narration": "第二句。", "estimated_seconds": 6.8},
                ],
            })
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            applied = pipe.apply_sentence_durations()

            self.assertEqual(applied, 2)
            plan = read_json(run_dir / "shot_plan.json", {})
            self.assertEqual(plan["shots"][0]["duration_hint_seconds"], 4)  # ceil(3.2)
            self.assertEqual(plan["shots"][1]["duration_hint_seconds"], 7)  # ceil(6.8)

    def test_no_report_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_shot_plan(run_dir, [stock_shot("s01")])
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            self.assertEqual(pipe.apply_sentence_durations(), 0)
            plan = read_json(run_dir / "shot_plan.json", {})
            self.assertEqual(plan["shots"][0]["duration_hint_seconds"], 5)  # 原值不动


class PreRenderCheckTest(unittest.TestCase):
    def test_clean_plan_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_text(run_dir / "approved_script.md", REAL_SCRIPT)
            write_shot_plan(run_dir, [stock_shot("s01", ["term a"]), stock_shot("s02", ["term b"])])
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            check = pipe.pre_render_check()
            self.assertTrue(check["valid"], check["issues"])

    def test_duplicate_terms_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_text(run_dir / "approved_script.md", REAL_SCRIPT)
            write_shot_plan(run_dir, [stock_shot("s01", ["same term"]), stock_shot("s02", ["same term"])])
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            check = pipe.pre_render_check()
            self.assertFalse(check["valid"])
            self.assertTrue(any("重复" in issue for issue in check["issues"]))

    def test_empty_script_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            # scaffold 的 approved_script.md 只有占位标题，无正文
            write_shot_plan(run_dir, [stock_shot("s01")])
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            check = pipe.pre_render_check()
            self.assertFalse(check["valid"])


class ImagegenResumeTest(unittest.TestCase):
    def test_waiting_then_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))
            state = pipe.load_state()
            pipe.save_stage(state, StageResult("script", "complete", "ok"))
            pipe.save_stage(state, StageResult("materials", "waiting_for_imagegen", "等待 1 张"))

            # 仍有 pending：保持 waiting
            with patch("director_workflow.pipeline_v2.build_imagegen_handoff",
                       return_value={"pending_count": 1}):
                result = pipe.run()
            self.assertEqual(result["status"], "waiting_for_imagegen")

            # pending 清零：finish_materials 被调用并继续往下
            with patch("director_workflow.pipeline_v2.build_imagegen_handoff",
                       return_value={"pending_count": 0}), \
                 patch.object(PipelineV2, "finish_materials",
                              return_value=StageResult("materials", "complete", "ok")), \
                 patch.object(PipelineV2, "stage_render",
                              return_value=StageResult("render", "failed", "stop here")):
                result = pipe.run()
            self.assertEqual(result["stage"], "render")


class MaterialsStageTest(unittest.TestCase):
    def test_excessive_degrade_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = make_run(tmp_path)
            write_text(run_dir / "approved_script.md", REAL_SCRIPT)
            write_shot_plan(run_dir, [stock_shot("s01"), stock_shot("s02")])
            write_json(run_dir / "asset_bindings.json", {"bindings": []})  # 全部无素材
            pipe = PipelineV2(run_dir, config=fake_config(tmp_path))

            with patch.object(PipelineV2, "_run_tool", return_value=(0, "", "")):
                result = pipe.stage_materials()
            self.assertEqual(result.status, "failed")
            self.assertIn("降级镜头过多", result.detail)


if __name__ == "__main__":
    unittest.main()
