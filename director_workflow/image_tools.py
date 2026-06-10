from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .asset_tools import build_asset_binding_template
from .io_utils import read_json, write_json, write_text
from .mpt_tools import validate_shot_plan


def build_codex_image_tasks(run_dir: Path) -> dict[str, Any]:
    # 写进 JSON 的路径必须是绝对路径，否则换 CWD 后 exists() 判断失效
    run_dir = run_dir.resolve()
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    validation = validate_shot_plan(shot_plan)
    existing_payload = read_json(run_dir / "image_tasks.json", {"tasks": []})
    existing_by_shot = {
        str(task.get("shot_id")): task
        for task in existing_payload.get("tasks", []) or []
        if isinstance(task, dict) and task.get("shot_id")
    }
    tasks = []
    prompt_dir = run_dir / "reports" / "codex_image_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)

    for shot in shot_plan.get("shots", []) or []:
        if not isinstance(shot, dict) or shot.get("material_type") != "codex_image":
            continue
        shot_id = str(shot.get("id") or "")
        prompt = str(shot.get("codex_image_prompt") or "").strip()
        suggested_output = run_dir / "assets" / "codex_images" / f"{shot_id}.png"
        prompt_file = prompt_dir / f"{shot_id}.md"
        prior = existing_by_shot.get(shot_id, {})
        actual_output = str(prior.get("actual_output") or "")
        generated = str(prior.get("status") or "") == "generated" and actual_output and Path(actual_output).exists()
        write_text(
            prompt_file,
            "\n".join(
                [
                    "# Codex Image Task",
                    "",
                    f"shot_id: {shot_id}",
                    f"narration: {shot.get('narration', '')}",
                    f"visual_intent: {shot.get('visual_intent', '')}",
                    f"suggested_output: {suggested_output}",
                    "",
                    "Prompt:",
                    "",
                    prompt,
                    "",
                ]
            ),
        )
        tasks.append(
            {
                "shot_id": shot_id,
                "status": "generated" if generated else "pending_generation",
                "narration": shot.get("narration", ""),
                "visual_intent": shot.get("visual_intent", ""),
                "prompt": prompt,
                "prompt_file": str(prompt_file),
                "suggested_output": str(suggested_output),
                "actual_output": actual_output if generated else "",
            }
        )

    payload = {
        "status": "ready" if validation["valid"] else "blocked",
        "shot_plan_validation": validation,
        "tasks": tasks,
        "notes": [
            "Generate each prompt with Codex image generation.",
            "After saving an image file, register it with tools/register_codex_image.py.",
        ],
    }
    write_json(run_dir / "image_tasks.json", payload)
    return payload


def register_codex_image(
    run_dir: Path,
    shot_id: str,
    image_path: Path,
    fit_score: int = 80,
    commercial_use_status: str = "generated_owned",
    approved: bool = False,
) -> dict[str, Any]:
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))

    run_dir = run_dir.resolve()
    output_dir = run_dir / "assets" / "codex_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = image_path.suffix.lower() or ".png"
    output_path = output_dir / f"{shot_id}{suffix}"
    source_path = image_path.resolve()
    target_path = output_path.resolve()
    if source_path != target_path:
        shutil.copy2(image_path, output_path)

    tasks_payload = read_json(run_dir / "image_tasks.json", {"tasks": []})
    tasks = tasks_payload.setdefault("tasks", [])
    task_found = False
    for task in tasks_payload.get("tasks", []) or []:
        if isinstance(task, dict) and task.get("shot_id") == shot_id:
            task["status"] = "generated"
            task["actual_output"] = str(output_path)
            task_found = True
    if not task_found:
        shot_plan = read_json(run_dir / "shot_plan.json", {})
        shot = next(
            (
                item
                for item in shot_plan.get("shots", []) or []
                if isinstance(item, dict) and str(item.get("id") or "") == shot_id
            ),
            {},
        )
        tasks.append(
            {
                "shot_id": shot_id,
                "status": "generated",
                "narration": shot.get("narration", ""),
                "visual_intent": shot.get("visual_intent", ""),
                "prompt": shot.get("codex_image_prompt", ""),
                "prompt_file": "",
                "suggested_output": str(output_path),
                "actual_output": str(output_path),
            }
        )
    write_json(run_dir / "image_tasks.json", tasks_payload)

    asset_bindings = read_json(run_dir / "asset_bindings.json", {"status": "draft", "bindings": []})
    bindings = asset_bindings.setdefault("bindings", [])
    binding = next((item for item in bindings if isinstance(item, dict) and item.get("shot_id") == shot_id), None)
    if binding is None:
        binding = {"shot_id": shot_id}
        bindings.append(binding)

    binding.update(
        {
            "material_type": "codex_image",
            "asset_path": str(output_path),
            "source_provider": "codex_imagegen",
            "license_status": "generated",
            "commercial_use_status": commercial_use_status,
            "fit_score": fit_score,
            "approved": bool(approved),
            "notes": (
                "Generated image registered and approved after visual review."
                if approved
                else "Generated image registered; set approved=true after visual review."
            ),
        }
    )
    write_json(run_dir / "asset_bindings.json", asset_bindings)
    report_bundle = build_asset_binding_template(run_dir)
    return {
        "shot_id": shot_id,
        "output_path": str(output_path),
        "asset_binding_report": report_bundle["asset_binding_report"],
    }
