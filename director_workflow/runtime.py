from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable

from .config import RuntimeConfig, load_local_env, load_runtime_config
from .io_utils import dated_run_dir, read_json, write_json
from .material_strategy import summarize_material_mix
from .mpt_tools import resolve_mpt_output_video
from .scaffold import scaffold_run


@dataclass(frozen=True)
class StepDefinition:
    id: str
    label: str
    description: str
    command_builder: Callable[[Path, RuntimeConfig, dict[str, Any]], list[str]]

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
        }


@dataclass
class JobRecord:
    id: str
    run_id: str
    step_id: str
    status: str = "queued"
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    returncode: int | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "status": self.status,
            "command": redact_command(self.command),
            "stdout": self.stdout[-12000:],
            "stderr": self.stderr[-12000:],
            "error": self.error,
            "returncode": self.returncode,
            "created_at": iso_time(self.created_at),
            "started_at": iso_time(self.started_at),
            "finished_at": iso_time(self.finished_at),
        }


def iso_time(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")


_SENSITIVE_FLAG = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)


def redact_command(command: list[str]) -> list[str]:
    redacted = []
    prev_was_sensitive = False
    for item in command:
        if prev_was_sensitive:
            redacted.append("<redacted>")
            prev_was_sensitive = False
        elif _SENSITIVE_FLAG.search(item):
            redacted.append("<redacted>")
            # 若是 --api-key=value 形式，值已在同一 item 里，不需要标记下一个
            prev_was_sensitive = "=" not in item
        else:
            redacted.append(item)
    return redacted


def _tool(config: RuntimeConfig, name: str) -> str:
    return str(config.workflow_dir / "tools" / name)


def _bool_option(options: dict[str, Any], name: str, default: bool = False) -> bool:
    value = options.get(name, default)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_option(options: dict[str, Any], name: str, default: int, minimum: int = 1, maximum: int = 9999) -> int:
    try:
        value = int(options.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _str_option(options: dict[str, Any], name: str, default: str = "") -> str:
    return str(options.get(name) or default).strip()


def _deepseek_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    model = _str_option(options, "model", config.deepseek_model)
    provider = _str_option(options, "provider", "auto")
    count = str(_int_option(options, "count", 5, 1, 20))
    return [
        sys.executable,
        _tool(config, "generate_drafts.py"),
        str(run_dir),
        "--count",
        count,
        "--model",
        model,
        "--provider",
        provider if provider in {"auto", "api", "cli"} else "auto",
    ]


def _audit_script_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "audit_script.py"), str(run_dir)]


def _approve_script_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [sys.executable, _tool(config, "approve_script.py"), str(run_dir)]
    candidate_id = _str_option(options, "candidate_id")
    if candidate_id:
        command.extend(["--candidate-id", candidate_id])
    return command


def _codex_approve_script_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [
        sys.executable,
        _tool(config, "codex_approve_script.py"),
        str(run_dir),
        "--timeout",
        str(_int_option(options, "timeout", 600, 30, 3600)),
    ]
    model = _str_option(options, "model")
    if model:
        command.extend(["--model", model])
    if _bool_option(options, "dry_run"):
        command.append("--dry-run")
    return command


def _material_rules_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "plan_material_strategy.py"), str(run_dir), "--mode", "rules"]


def _material_stock_only_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "plan_material_strategy.py"), str(run_dir), "--mode", "stock-only"]


def _material_codex_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [
        sys.executable,
        _tool(config, "plan_material_strategy.py"),
        str(run_dir),
        "--mode",
        "codex",
        "--timeout",
        str(_int_option(options, "timeout", 600, 30, 3600)),
    ]
    model = _str_option(options, "model")
    if model:
        command.extend(["--model", model])
    if _bool_option(options, "dry_run"):
        command.append("--dry-run")
    return command


def _collect_api_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    provider = _str_option(options, "provider", "pexels")
    if provider not in {"pexels", "pixabay"}:
        provider = "pexels"
    command = [
        sys.executable,
        _tool(config, "collect_api_materials.py"),
        str(run_dir),
        "--provider",
        provider,
        "--per-page",
        str(_int_option(options, "per_page", 15, 1, 80)),
        "--max-candidates-per-shot",
        str(_int_option(options, "max_candidates_per_shot", 2, 1, 10)),
        "--bind",
    ]
    if _bool_option(options, "allow_pixabay_fallback"):
        command.append("--allow-pixabay-fallback")
    if _bool_option(options, "approve_passing", True):
        command.append("--approve-passing")
    return command


def _image_tasks_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "build_image_tasks.py"), str(run_dir)]


def _asset_bindings_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "build_asset_bindings.py"), str(run_dir)]


def _build_mpt_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [sys.executable, _tool(config, "build_mpt_params.py"), str(run_dir)]
    if _bool_option(options, "allow_draft_pexels"):
        command.append("--allow-draft-pexels")
    return command


def _submit_mpt_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [sys.executable, _tool(config, "submit_mpt.py"), str(run_dir)]
    if _bool_option(options, "wait", True):
        command.extend(
            [
                "--wait",
                "--timeout-seconds",
                str(_int_option(options, "timeout_seconds", 900, 30, 7200)),
                "--poll-seconds",
                str(_int_option(options, "poll_seconds", 10, 1, 120)),
                "--stall-seconds",
                str(_int_option(options, "stall_seconds", 240, 30, 1800)),
            ]
        )
    return command


def _qa_render_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    video_path = _str_option(options, "video_path")
    if not video_path:
        raise ValueError("qa_render requires options.video_path")
    command = [sys.executable, _tool(config, "qa_render.py"), str(run_dir), video_path]
    if _bool_option(options, "contact_sheet", True):
        command.append("--contact-sheet")
    return command


def _semantic_step(run_dir: Path, config: RuntimeConfig, options: dict[str, Any]) -> list[str]:
    command = [sys.executable, _tool(config, "run_semantic_review.py"), str(run_dir)]
    model = _str_option(options, "model")
    if model:
        command.extend(["--model", model])
    if _bool_option(options, "dry_run"):
        command.append("--dry-run")
    return command


def _revision_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "build_revision_plan.py"), str(run_dir)]


def _tts_dry_run_step(run_dir: Path, config: RuntimeConfig, _options: dict[str, Any]) -> list[str]:
    return [sys.executable, _tool(config, "tts_dry_run.py"), str(run_dir)]


STEP_DEFINITIONS: dict[str, StepDefinition] = {
    "generate_drafts": StepDefinition("generate_drafts", "DeepSeek 批量写稿", "生成 script_candidates.json 并自动跑稿件硬审。", _deepseek_step),
    "audit_script": StepDefinition("audit_script", "稿件硬审", "检查字数、句数、长句和泛词。", _audit_script_step),
    "approve_script": StepDefinition("approve_script", "自动定稿", "把推荐候选稿写入 approved_script.md。", _approve_script_step),
    "codex_approve_script": StepDefinition("codex_approve_script", "Codex 定稿", "调用 Codex CLI 从 DeepSeek 候选稿中审核并写出最终旁白。", _codex_approve_script_step),
    "tts_dry_run": StepDefinition("tts_dry_run", "TTS 时长预估", "用 edge-tts 合成 approved_script.md，渲染前校验脚本时长。", _tts_dry_run_step),
    "material_rules": StepDefinition("material_rules", "规则素材策略", "不用 Codex，按本地规则生成 stock_video/codex_image 分镜。", _material_rules_step),
    "material_codex": StepDefinition("material_codex", "Codex 导演策略", "调用 Codex CLI 判断每句旁白用 API 视频还是生成图。", _material_codex_step),
    "collect_api_materials": StepDefinition("collect_api_materials", "Pexels 素材采集", "按镜头搜索、下载、绑定并审核 API 视频素材。", _collect_api_step),
    "build_image_tasks": StepDefinition("build_image_tasks", "导出生图任务", "为 codex_image 镜头生成逐镜头 prompt 文件。", _image_tasks_step),
    "build_asset_bindings": StepDefinition("build_asset_bindings", "刷新素材绑定", "合并 API 视频和 Codex 生图登记结果，重新计算素材审核。", _asset_bindings_step),
    "build_mpt_params": StepDefinition("build_mpt_params", "构建 MPT 参数", "asset_binding_report pass 后生成 mpt_params.json。", _build_mpt_step),
    "submit_mpt": StepDefinition("submit_mpt", "提交 MPT 渲染", "提交本地 MPT API，可等待完成并记录 watchdog 报告。", _submit_mpt_step),
    "qa_render": StepDefinition("qa_render", "成片技术 QA", "检查时长、分辨率、音频、黑屏，并生成 contact sheet。", _qa_render_step),
    "semantic_review": StepDefinition("semantic_review", "Codex 成片审核", "调用 Codex CLI 做商业发布前语义审核。", _semantic_step),
    "revision_plan": StepDefinition("revision_plan", "返修计划", "合并硬审、素材、QA 和语义审核结果。", _revision_step),
}


def list_steps() -> list[dict[str, str]]:
    return [definition.public_dict() for definition in STEP_DEFINITIONS.values()]


def child_env(config: RuntimeConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.update(load_local_env(config.workflow_dir))
    prior_pythonpath = env.get("PYTHONPATH", "")
    paths = [str(config.workflow_dir)]
    if prior_pythonpath:
        paths.append(prior_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def resolve_run_dir(run_id: str, config: RuntimeConfig | None = None) -> Path:
    config = config or load_runtime_config()
    run_id = run_id.strip()
    if not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("run_id must be a single run directory name")
    runs_dir = config.runs_dir.resolve()
    candidate = (runs_dir / run_id).resolve()
    if candidate != runs_dir and runs_dir not in candidate.parents:
        raise ValueError("run_id must stay inside DIRECTOR_RUNS_DIR")
    if not candidate.exists() or not candidate.is_dir():
        raise FileNotFoundError(str(candidate))
    return candidate


def unique_run_dir(topic: str, config: RuntimeConfig | None = None) -> Path:
    config = config or load_runtime_config()
    base = dated_run_dir(topic, config.runs_dir)
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate a unique run directory")


def create_run(
    topic: str,
    duration_seconds: int,
    platform: str,
    style: str,
    voice_name: str | None = None,
    config: RuntimeConfig | None = None,
) -> dict[str, Any]:
    config = config or load_runtime_config()
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")
    run_dir = scaffold_run(
        topic=topic,
        duration_seconds=duration_seconds,
        platform=platform,
        style=style,
        voice_name=voice_name or config.default_voice_name,
        run_dir=unique_run_dir(topic, config),
    )
    return summarize_run(run_dir)


def file_status(run_dir: Path, name: str, default: str = "missing") -> dict[str, Any]:
    path = run_dir / name
    if not path.exists():
        return {"status": default, "path": str(path)}
    if path.suffix == ".json":
        payload = read_json(path, {})
        if isinstance(payload, dict):
            return {
                "status": payload.get("status", "exists"),
                "decision": payload.get("decision", ""),
                "path": str(path),
            }
    return {"status": "exists", "path": str(path)}


def summarize_run(run_dir: Path) -> dict[str, Any]:
    brief = read_json(run_dir / "brief.json", {})
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    image_tasks = read_json(run_dir / "image_tasks.json", {})
    image_handoff = read_json(run_dir / "image_generation_handoff.json", {})
    asset_bindings = read_json(run_dir / "asset_bindings.json", {})
    mpt_wait = read_json(run_dir / "mpt_wait_report.json", {})
    qa_report = read_json(run_dir / "qa_report.json", {})
    revision_plan = read_json(run_dir / "revision_plan.json", {})
    videos = []
    task = mpt_wait.get("task") if isinstance(mpt_wait, dict) else {}
    if isinstance(task, dict):
        videos = task.get("videos") or []
    updated_at = run_dir.stat().st_mtime
    return {
        "run_id": run_dir.name,
        "path": str(run_dir),
        "topic": brief.get("topic", run_dir.name),
        "platform": brief.get("platform", ""),
        "target_duration_seconds": brief.get("target_duration_seconds", ""),
        "updated_at": iso_time(updated_at),
        "material_mix": summarize_material_mix(shot_plan),
        "counts": {
            "shots": len(shot_plan.get("shots", []) or []) if isinstance(shot_plan, dict) else 0,
            "image_tasks": len(image_tasks.get("tasks", []) or []) if isinstance(image_tasks, dict) else 0,
            "pending_image_tasks": int(image_handoff.get("pending_count") or 0) if isinstance(image_handoff, dict) else 0,
            "asset_bindings": len(asset_bindings.get("bindings", []) or []) if isinstance(asset_bindings, dict) else 0,
        },
        "imagegen": {
            "status": image_handoff.get("status", "") if isinstance(image_handoff, dict) else "",
            "pending_tasks": image_handoff.get("pending_tasks", []) if isinstance(image_handoff, dict) else [],
            "complete_tasks": image_handoff.get("complete_tasks", []) if isinstance(image_handoff, dict) else [],
        },
        "files": {
            "script_candidates": file_status(run_dir, "script_candidates.json"),
            "script_audit": file_status(run_dir, "script_audit.json"),
            "approved_script": file_status(run_dir, "approved_script.md"),
            "approved_script_report": file_status(run_dir, "approved_script_report.json"),
            "shot_plan": file_status(run_dir, "shot_plan.json"),
            "material_strategy": file_status(run_dir, "material_strategy.json"),
            "api_assets": file_status(run_dir, "api_asset_candidates.json"),
            "image_tasks": file_status(run_dir, "image_tasks.json"),
            "image_generation_handoff": file_status(run_dir, "image_generation_handoff.json"),
            "asset_binding_report": file_status(run_dir, "asset_binding_report.json"),
            "mpt_params": file_status(run_dir, "mpt_params.json"),
            "mpt_wait": file_status(run_dir, "mpt_wait_report.json"),
            "qa_report": file_status(run_dir, "qa_report.json"),
            "semantic_review": file_status(run_dir, "semantic_review.json"),
            "revision_plan": file_status(run_dir, "revision_plan.json"),
        },
        "qa_decision": qa_report.get("decision", "") if isinstance(qa_report, dict) else "",
        "revision_decision": revision_plan.get("decision", "") if isinstance(revision_plan, dict) else "",
        "mpt_videos": videos,
    }


def list_runs(config: RuntimeConfig | None = None, limit: int = 80) -> list[dict[str, Any]]:
    config = config or load_runtime_config()
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    candidates = [path for path in config.runs_dir.iterdir() if path.is_dir() and not path.name.startswith(".")]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [summarize_run(path) for path in candidates[:limit]]


def run_workflow_step(
    run_id: str,
    step_id: str,
    options: dict[str, Any] | None = None,
    config: RuntimeConfig | None = None,
) -> JobRecord:
    config = config or load_runtime_config()
    options = options or {}
    if step_id not in STEP_DEFINITIONS:
        raise ValueError(f"unknown step_id: {step_id}")
    run_dir = resolve_run_dir(run_id, config)
    command = STEP_DEFINITIONS[step_id].command_builder(run_dir, config, options)
    started = time.time()
    record = JobRecord(
        id=str(uuid.uuid4()),
        run_id=run_id,
        step_id=step_id,
        status="running",
        command=command,
        created_at=started,
        started_at=started,
    )
    try:
        result = subprocess.run(
            command,
            cwd=str(config.root_dir),
            env=child_env(config),
            capture_output=True,
            text=True,
            check=False,
            timeout=_int_option(options, "process_timeout", 7200, 30, 14400),
        )
        record.returncode = result.returncode
        record.stdout = result.stdout
        record.stderr = result.stderr
        record.status = "succeeded" if result.returncode == 0 else "failed"
        if result.returncode == 0 and step_id == "build_image_tasks":
            build_imagegen_handoff(run_dir)
        if result.returncode != 0:
            record.error = (result.stderr or result.stdout or "").strip()
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
    finally:
        record.finished_at = time.time()
    return record


def build_imagegen_handoff(run_dir: Path) -> dict[str, Any]:
    image_tasks = read_json(run_dir / "image_tasks.json", {})
    asset_bindings = read_json(run_dir / "asset_bindings.json", {})
    bindings = {
        str(binding.get("shot_id")): binding
        for binding in asset_bindings.get("bindings", []) or []
        if isinstance(binding, dict) and binding.get("shot_id")
    }
    pending: list[dict[str, Any]] = []
    complete: list[dict[str, Any]] = []
    for task in image_tasks.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        shot_id = str(task.get("shot_id") or "")
        actual_output = Path(str(task.get("actual_output") or ""))
        binding = bindings.get(shot_id, {})
        generated = (
            str(task.get("status") or "") == "generated"
            and str(task.get("actual_output") or "")
            and actual_output.exists()
            and bool(binding.get("approved"))
        )
        item = {
            "shot_id": shot_id,
            "narration": task.get("narration", ""),
            "visual_intent": task.get("visual_intent", ""),
            "prompt": task.get("prompt", ""),
            "prompt_file": task.get("prompt_file", ""),
            "suggested_output": task.get("suggested_output", ""),
            "actual_output": task.get("actual_output", ""),
            "register_command": (
                f"tools/register_codex_image.py {run_dir} {shot_id} "
                f"{task.get('suggested_output', '')} --fit-score 86 --approve"
            ),
        }
        if generated:
            complete.append(item)
        else:
            pending.append(item)

    payload = {
        "status": "waiting_for_codex_imagegen" if pending else "complete",
        "pending_count": len(pending),
        "complete_count": len(complete),
        "pending_tasks": pending,
        "complete_tasks": complete,
        "instructions": [
            "Use the Codex built-in image_gen tool for each pending prompt.",
            "Save each generated image to the task suggested_output path.",
            "Register each saved image with tools/register_codex_image.py <run_dir> <shot_id> <image_path> --fit-score 86 --approve.",
            "Run the strict pipeline again after all pending_tasks are registered.",
        ],
    }
    write_json(run_dir / "image_generation_handoff.json", payload)
    return payload


def run_auto_pipeline(
    run_id: str,
    options: dict[str, Any] | None = None,
    config: RuntimeConfig | None = None,
) -> JobRecord:
    config = config or load_runtime_config()
    options = options or {}
    run_dir = resolve_run_dir(run_id, config)
    started = time.time()
    record = JobRecord(
        id=str(uuid.uuid4()),
        run_id=run_id,
        step_id="auto_pipeline",
        status="running",
        created_at=started,
        started_at=started,
    )
    logs: list[str] = []

    def run_stage(step_id: str, stage_options: dict[str, Any] | None = None, required: bool = True) -> JobRecord:
        logs.append(f"\n=== {step_id} ===")
        stage = run_workflow_step(run_id, step_id, options=stage_options or {}, config=config)
        if stage.command:
            logs.append("$ " + " ".join(redact_command(stage.command)))
        if stage.stdout:
            logs.append(stage.stdout.strip())
        if stage.stderr:
            logs.append(stage.stderr.strip())
        if stage.error:
            logs.append(stage.error.strip())
        logs.append(f"stage_status={stage.status}")
        if required and stage.status != "succeeded":
            raise RuntimeError(f"{step_id} failed")
        return stage

    try:
        provider = _str_option(options, "provider", "auto")
        count = _int_option(options, "count", 5, 1, 20)
        allow_pixabay_fallback = _bool_option(options, "allow_pixabay_fallback", False)
        codex_model = _str_option(options, "codex_model")

        handoff_path = run_dir / "image_generation_handoff.json"
        existing_handoff = read_json(handoff_path, {}) if handoff_path.exists() else {}
        resume_after_imagegen = (
            existing_handoff.get("status") == "complete"
            and (run_dir / "approved_script.md").exists()
            and (run_dir / "shot_plan.json").exists()
            and (run_dir / "image_tasks.json").exists()
        )
        if resume_after_imagegen:
            logs.append("resume_after_imagegen=true; keeping approved_script, shot_plan, API assets, and registered Codex images.")
        else:
            run_stage("generate_drafts", {"provider": provider, "count": count, "process_timeout": 600})
            run_stage("codex_approve_script", {"model": codex_model, "process_timeout": 900, "timeout": 750})
            # 渲染前校验：稿件硬审 + TTS 时长预估
            # tts_dry_run: exit 1 = 时长不合格（阻断），exit 0 = 合格或跳过（edge-tts 不可用）
            run_stage("audit_script")
            run_stage("tts_dry_run", {"process_timeout": 120})
            run_stage("material_codex", {"model": codex_model, "process_timeout": 900, "timeout": 750})
            run_stage("build_image_tasks")
            collect_stage = run_stage(
                "collect_api_materials",
                {
                    "provider": "pexels",
                    "allow_pixabay_fallback": allow_pixabay_fallback,
                    "approve_passing": True,
                    "process_timeout": 1800,
                },
                required=False,
            )
            if collect_stage.status != "succeeded":
                logs.append("warning: Pexels material collection failed or has missing stock shots; strict mode will not use draft fallback.")
            image_handoff = build_imagegen_handoff(run_dir)
            if image_handoff["pending_count"]:
                logs.append("\n=== codex_imagegen ===")
                logs.append(f"stage_status=waiting_for_codex_imagegen pending={image_handoff['pending_count']}")
                logs.append(f"handoff={run_dir / 'image_generation_handoff.json'}")
                for task in image_handoff["pending_tasks"]:
                    logs.append(f"{task['shot_id']}: {task['prompt_file']} -> {task['suggested_output']}")
                record.status = "waiting_for_imagegen"
                record.returncode = 0
                return record
        run_stage("build_asset_bindings")
        asset_report = read_json(run_dir / "asset_binding_report.json", {})
        if asset_report.get("decision") != "pass":
            raise RuntimeError("asset_binding_report is not pass; strict skill mode blocks MPT render")
        run_stage("build_mpt_params")
        run_stage(
            "submit_mpt",
            {
                "wait": True,
                "timeout_seconds": _int_option(options, "timeout_seconds", 1200, 60, 7200),
                "poll_seconds": _int_option(options, "poll_seconds", 10, 1, 120),
                "stall_seconds": _int_option(options, "stall_seconds", 300, 30, 1800),
                "process_timeout": _int_option(options, "process_timeout", 1800, 120, 9000),
            },
        )
        video_path = resolve_mpt_output_video(run_dir, mpt_dir=config.mpt_dir)
        if video_path:
            run_stage("qa_render", {"video_path": str(video_path), "contact_sheet": True, "process_timeout": 240})
            logs.append(f"final_video={video_path}")
        else:
            raise RuntimeError("final_video_not_found: mpt_wait_report has no resolvable output path")
        run_stage("semantic_review", {"model": codex_model, "process_timeout": 900, "timeout": 750})
        run_stage("revision_plan", required=False)
        qa_report = read_json(run_dir / "qa_report.json", {})
        semantic_review = read_json(run_dir / "semantic_review.json", {})
        revision_plan = read_json(run_dir / "revision_plan.json", {})
        qa_decision = str(qa_report.get("decision") or "")
        semantic_decision = str(semantic_review.get("decision") or "")
        revision_decision = str(revision_plan.get("decision") or "")
        if qa_decision != "pass" or semantic_decision != "pass" or revision_decision != "ready":
            raise RuntimeError(
                "auto_pipeline needs revision: "
                f"qa_decision={qa_decision or 'missing'}, "
                f"semantic_decision={semantic_decision or 'missing'}, "
                f"revision_decision={revision_decision or 'missing'}"
            )
        record.status = "succeeded"
        record.returncode = 0
    except Exception as exc:
        record.status = "failed"
        record.returncode = 1
        record.error = str(exc)
    finally:
        record.stdout = "\n".join(logs).strip()
        record.finished_at = time.time()
    return record


class JobManager:
    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or load_runtime_config()
        self._jobs: dict[str, JobRecord] = {}
        self._lock = Lock()

    def start(self, run_id: str, step_id: str, options: dict[str, Any] | None = None) -> JobRecord:
        job = JobRecord(id=str(uuid.uuid4()), run_id=run_id, step_id=step_id, status="queued")
        with self._lock:
            self._jobs[job.id] = job

        def worker() -> None:
            with self._lock:
                queued = self._jobs[job.id]
                queued.status = "running"
                queued.started_at = time.time()
            result = run_workflow_step(run_id, step_id, options=options, config=self.config)
            result.id = job.id
            result.created_at = job.created_at
            with self._lock:
                self._jobs[job.id] = result

        Thread(target=worker, daemon=True).start()
        return job

    def start_auto(self, run_id: str, options: dict[str, Any] | None = None) -> JobRecord:
        with self._lock:
            for existing in self._jobs.values():
                if existing.run_id == run_id and existing.step_id == "auto_pipeline" and existing.status in {"queued", "running"}:
                    return existing
            job = JobRecord(id=str(uuid.uuid4()), run_id=run_id, step_id="auto_pipeline", status="queued")
            self._jobs[job.id] = job

        def worker() -> None:
            with self._lock:
                queued = self._jobs[job.id]
                queued.status = "running"
                queued.started_at = time.time()
            result = run_auto_pipeline(run_id, options=options, config=self.config)
            result.id = job.id
            result.created_at = job.created_at
            with self._lock:
                self._jobs[job.id] = result

        Thread(target=worker, daemon=True).start()
        return job

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return [job.to_public_dict() for job in jobs]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.to_public_dict() if job else None
