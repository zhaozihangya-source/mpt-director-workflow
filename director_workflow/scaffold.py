from __future__ import annotations

from pathlib import Path

from .config import mpt_task_endpoint
from .io_utils import dated_run_dir, target_char_range, write_json, write_text


def default_brief(topic: str, duration_seconds: int, platform: str, style: str, voice_name: str = "gemini:Zephyr-Female") -> dict:
    min_chars, max_chars = target_char_range(duration_seconds)
    return {
        "topic": topic,
        "platform": platform,
        "aspect": "9:16",
        "language": "zh-CN",
        "target_duration_seconds": duration_seconds,
        "estimated_script_chars": {
            "min": min_chars,
            "max": max_chars,
            "basis": "Gemini TTS baseline from local MPT run: about 4.38 Chinese chars/sec. Validate with real TTS.",
        },
        "audience": "普通短视频用户",
        "style": style,
        "tts": {
            "provider": voice_name.split(":", 1)[0] if ":" in voice_name else "",
            "voice_name": voice_name,
        },
        "quality_goals": [
            "前三秒有明确钩子",
            "每句旁白都能对应具体画面",
            "避免泛泛宣传和空话",
            "字幕不超过两行",
            "素材不黑屏、不重复、不明显跑题",
        ],
        "model_roles": {
            "codex": "导演、创意选择、分镜、素材策略、成片审核",
            "deepseek": "批量脚本、搜索词、标题、标签等重复性生成",
            "mpt": "TTS、字幕、素材下载、剪辑渲染引擎",
        },
    }


def scaffold_run(
    topic: str,
    duration_seconds: int = 45,
    platform: str = "douyin",
    style: str = "商业科普，具体、有钩子、适合竖屏短视频",
    voice_name: str = "gemini:Zephyr-Female",
    run_dir: Path | None = None,
) -> Path:
    run_dir = run_dir or dated_run_dir(topic)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "assets" / "codex_images").mkdir(parents=True, exist_ok=True)
    (run_dir / "assets" / "api_videos").mkdir(parents=True, exist_ok=True)
    (run_dir / "assets" / "stock").mkdir(parents=True, exist_ok=True)
    (run_dir / "media").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)

    brief = default_brief(topic, duration_seconds, platform, style, voice_name=voice_name)
    write_json(run_dir / "brief.json", brief)
    write_json(
        run_dir / "script_candidates.json",
        {
            "status": "pending",
            "generator": "deepseek",
            "candidates": [],
        },
    )
    write_text(
        run_dir / "approved_script.md",
        "# Approved Script\n\n把最终旁白粘贴到这里，或运行稿件生成/审核工具后由 Codex 定稿。\n",
    )
    write_json(
        run_dir / "shot_plan.json",
        {
            "status": "pending",
            "shots": [],
            "notes": "每句旁白对应一个 visual_intent，并标记 stock_video 或 codex_image。",
        },
    )
    write_json(run_dir / "search_terms.json", {"status": "pending", "items": []})
    write_json(
        run_dir / "image_tasks.json",
        {
            "status": "pending",
            "tasks": [],
        },
    )
    write_json(
        run_dir / "asset_manifest.json",
        {
            "status": "pending",
            "assets": [],
            "rules": [
                "Codex image should be vertical 9:16, no text, no logo, subtitle-safe bottom area.",
                "Stock video should match one narration line, not just the broad topic.",
            ],
        },
    )
    write_json(
        run_dir / "asset_bindings.json",
        {
            "status": "pending",
            "bindings": [],
        },
    )
    write_json(
        run_dir / "asset_binding_report.json",
        {
            "status": "pending",
            "decision": "not_reviewed",
            "issues": [],
        },
    )
    write_json(
        run_dir / "mpt_params.json",
        {
            "status": "pending",
            "endpoint": mpt_task_endpoint(),
            "params": {},
        },
    )
    write_json(
        run_dir / "qa_report.json",
        {
            "status": "pending",
            "checks": {},
            "decision": "not_reviewed",
        },
    )
    write_json(
        run_dir / "semantic_review.json",
        {
            "status": "pending",
            "overall_score": None,
            "decision": "not_reviewed",
            "summary": "",
            "required_changes": [],
            "optional_changes": [],
        },
    )
    write_json(
        run_dir / "revision_plan.json",
        {
            "status": "pending",
            "decision": "not_built",
            "actions": [],
        },
    )
    write_text(
        run_dir / "reports" / "review_notes.md",
        "# Review Notes\n\n## 稿件\n\n## 素材\n\n## 成片审核\n",
    )
    return run_dir
