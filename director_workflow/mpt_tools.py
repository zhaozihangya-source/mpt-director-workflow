from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .config import env_value, load_local_env
from .endpoint_tools import DEFAULT_MPT_ENDPOINT, MPT_VIDEO_API_PATH, validate_local_mpt_endpoint
from .io_utils import MPT_DIR, ROOT_DIR, read_json, split_cn_sentences, write_json
from .material_tools import ffprobe_json

MAX_MPT_VIDEO_TERMS = 24
MPT_LOCAL_MATERIAL_DIR = MPT_DIR / "storage" / "local_videos"
MPT_LOCAL_MATERIAL_SUFFIXES = {".mp4", ".mov", ".avi", ".flv", ".mkv", ".jpg", ".jpeg", ".png"}


def build_shot_plan_from_script(run_dir: Path) -> dict[str, Any]:
    script = (run_dir / "approved_script.md").read_text(encoding="utf-8")
    script = script.replace("# Approved Script", "").strip()
    sentences = split_cn_sentences(script)
    shots = []
    for index, sentence in enumerate(sentences, start=1):
        shots.append(
            {
                "id": f"s{index:02d}",
                "narration": sentence,
                "duration_hint_seconds": 4,
                "material_type": "stock_video" if index % 3 else "codex_image",
                "visual_intent": "",
                "search_terms": [],
                "codex_image_prompt": "",
            }
        )
    plan = {"status": "needs_director_input", "shots": shots}
    plan["validation"] = validate_shot_plan(plan)
    write_json(run_dir / "shot_plan.json", plan)
    return plan


def validate_shot_plan(plan: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        issues.append(
            {
                "shot_id": None,
                "field": "shots",
                "issue": "shot_plan must contain at least one shot",
            }
        )
        return {"valid": False, "issues": issues}

    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            issues.append(
                {
                    "shot_id": f"s{index:02d}",
                    "field": "shot",
                    "issue": "shot must be an object",
                }
            )
            continue

        shot_id = str(shot.get("id") or f"s{index:02d}")
        if not str(shot.get("narration") or "").strip():
            issues.append({"shot_id": shot_id, "field": "narration", "issue": "narration is required"})
        if not str(shot.get("visual_intent") or "").strip():
            issues.append({"shot_id": shot_id, "field": "visual_intent", "issue": "visual_intent is required"})

        material_type = str(shot.get("material_type") or "").strip()
        if material_type not in {"stock_video", "codex_image", "local_asset"}:
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "material_type",
                    "issue": "material_type must be stock_video, codex_image, or local_asset",
                }
            )
        if material_type == "stock_video":
            terms = shot.get("search_terms")
            if not isinstance(terms, list) or not any(str(term).strip() for term in terms):
                issues.append(
                    {
                        "shot_id": shot_id,
                        "field": "search_terms",
                        "issue": "stock_video shots require at least one search term",
                    }
                )
        if material_type == "codex_image" and not str(shot.get("codex_image_prompt") or "").strip():
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "codex_image_prompt",
                    "issue": "codex_image shots require codex_image_prompt",
                }
            )
        if material_type == "local_asset" and not str(shot.get("asset_path") or "").strip():
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "asset_path",
                    "issue": "local_asset shots require asset_path",
                }
            )

    return {"valid": not issues, "issues": issues}


def build_mpt_params(
    run_dir: Path,
    local_material_dir: Path = MPT_LOCAL_MATERIAL_DIR,
    allow_draft_pexels: bool = False,
) -> dict[str, Any]:
    local_env = load_local_env()
    brief = read_json(run_dir / "brief.json", {})
    tts_config = brief.get("tts", {}) if isinstance(brief, dict) else {}
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    validation = validate_shot_plan(shot_plan)
    write_json(run_dir / "shot_plan_validation.json", validation)
    if not validation["valid"]:
        raise ValueError("shot_plan.json is not production-ready; see shot_plan_validation.json")
    script = (run_dir / "approved_script.md").read_text(encoding="utf-8")
    script = script.replace("# Approved Script", "").strip()
    terms = collect_representative_video_terms(shot_plan, limit=MAX_MPT_VIDEO_TERMS)
    if not terms:
        candidates = read_json(run_dir / "script_candidates.json", {})
        for item in candidates.get("candidates", []):
            terms.extend(item.get("search_terms", []))
            if terms:
                break
    terms = list(dict.fromkeys(term for term in terms if term))[:MAX_MPT_VIDEO_TERMS]
    asset_report = refresh_asset_binding_report(run_dir)
    if asset_report.get("decision") != "pass" and not allow_draft_pexels:
        write_json(
            run_dir / "mpt_params.json",
            {
                "status": "blocked",
                "reason": "asset_binding_report decision is not pass",
                "asset_binding_report": str(run_dir / "asset_binding_report.json"),
            },
        )
        raise ValueError("asset_binding_report.json is not pass; fix asset bindings or use draft Pexels mode explicitly")

    local_materials = (
        sync_approved_assets_for_mpt(run_dir, local_material_dir=local_material_dir)
        if asset_report.get("decision") == "pass"
        else []
    )
    use_local_materials = bool(local_materials)
    explicit_clip_duration = brief.get("video_clip_duration_seconds")
    if use_local_materials and explicit_clip_duration:
        video_clip_duration = max(2, min(8, int(explicit_clip_duration)))
    else:
        video_clip_duration = (
            derive_video_clip_duration(shot_plan, target_duration_seconds=brief.get("target_duration_seconds"))
            if use_local_materials
            else 3
        )
    params = {
        "video_subject": brief.get("topic", ""),
        "video_script": script,
        "video_terms": terms,
        "video_aspect": brief.get("aspect", "9:16"),
        "video_source": "local" if use_local_materials else "pexels",
        "video_materials": local_materials or None,
        "video_clip_duration": video_clip_duration,
        "video_count": 1,
        "video_concat_mode": "sequential" if use_local_materials else "random",
        "video_transition_mode": None,
        "voice_name": str(tts_config.get("voice_name") or "").strip()
        or env_value("DIRECTOR_DEFAULT_VOICE_NAME", local_env, "gemini:Zephyr-Female"),
        "voice_rate": 1.0,
        "voice_volume": 1.0,
        "bgm_type": "random",
        "bgm_volume": 0.12,
        "subtitle_enabled": True,
        "subtitle_position": "bottom",
        "font_name": "STHeitiMedium.ttc",
        "font_size": 54,
        "text_fore_color": "#FFFFFF",
        "text_background_color": "#000000",
        "rounded_subtitle_background": True,
        "stroke_color": "#000000",
        "stroke_width": 1.5,
        "n_threads": 2,
    }
    payload = {
        "status": "ready",
        "endpoint": validate_local_mpt_endpoint(env_value("MPT_API_ENDPOINT", local_env, DEFAULT_MPT_ENDPOINT)),
        "params": params,
    }
    write_json(run_dir / "mpt_params.json", payload)
    return payload


def sync_approved_assets_for_mpt(run_dir: Path, local_material_dir: Path = MPT_LOCAL_MATERIAL_DIR) -> list[dict[str, Any]]:
    asset_binding_report = read_json(run_dir / "asset_binding_report.json", {})
    if asset_binding_report.get("decision") != "pass":
        write_json(
            run_dir / "local_material_sync_report.json",
            {
                "status": "skipped",
                "reason": "asset_binding_report decision is not pass",
                "materials": [],
            },
        )
        raise ValueError("asset_binding_report.json is not pass; cannot sync local materials")

    asset_bindings = read_json(run_dir / "asset_bindings.json", {})
    bindings = [binding for binding in asset_bindings.get("bindings", []) or [] if isinstance(binding, dict)]
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    duration_hints = {
        str(shot.get("id")): shot.get("duration_hint_seconds")
        for shot in shot_plan.get("shots", []) or []
        if isinstance(shot, dict) and shot.get("id")
    }
    if not bindings:
        write_json(
            run_dir / "local_material_sync_report.json",
            {
                "status": "skipped",
                "reason": "asset_bindings.json has no bindings",
                "materials": [],
            },
        )
        return []

    local_material_dir.mkdir(parents=True, exist_ok=True)
    synced: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for index, binding in enumerate(bindings, start=1):
        shot_id = str(binding.get("shot_id") or f"s{index:02d}")
        if not binding.get("approved"):
            issues.append({"shot_id": shot_id, "field": "approved", "issue": "binding is not approved"})
            continue

        source_path = resolve_bound_asset_path(str(binding.get("asset_path") or ""), run_dir)
        if not source_path:
            issues.append({"shot_id": shot_id, "field": "asset_path", "issue": "asset path does not exist"})
            continue

        suffix = source_path.suffix.lower()
        if suffix not in MPT_LOCAL_MATERIAL_SUFFIXES:
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "asset_path",
                    "issue": f"unsupported local material suffix: {suffix}",
                    "evidence": str(source_path),
                }
            )
            continue

        filename = local_material_filename(run_dir, shot_id, source_path)
        target_path = local_material_dir / filename
        if source_path.resolve() != target_path.resolve():
            shutil.copy2(source_path, target_path)
        digest = file_sha256(target_path)

        material = {
            "provider": str(binding.get("source_provider") or "local"),
            "url": str(target_path),
            "duration": int(duration_hints.get(shot_id) or binding.get("duration_hint_seconds") or 0),
        }
        synced.append(
            {
                "shot_id": shot_id,
                "source_path": str(source_path),
                "local_file": filename,
                "target_path": str(target_path),
                "sha256": digest,
                "material": material,
            }
        )

    report = {
        "status": "complete" if not issues else "failed",
        "local_material_dir": str(local_material_dir),
        "materials": synced,
        "issues": issues,
    }
    write_json(run_dir / "local_material_sync_report.json", report)
    if issues:
        raise ValueError("approved assets could not be synced for MPT; see local_material_sync_report.json")
    return [item["material"] for item in synced]


def refresh_asset_binding_report(run_dir: Path) -> dict[str, Any]:
    from .asset_tools import validate_asset_bindings

    return validate_asset_bindings(run_dir)


def derive_video_clip_duration(
    shot_plan: dict[str, Any],
    target_duration_seconds: int | float | None = None,
    default: int = 5,
) -> int:
    durations = []
    shots = [shot for shot in shot_plan.get("shots", []) or [] if isinstance(shot, dict)]
    for shot in shots:
        try:
            value = float(shot.get("duration_hint_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            durations.append(value)
    median = float(default)
    if durations:
        durations.sort()
        midpoint = len(durations) // 2
        if len(durations) % 2:
            median = durations[midpoint]
        else:
            median = (durations[midpoint - 1] + durations[midpoint]) / 2

    required = median
    if target_duration_seconds and shots:
        required = max(required, math.ceil(float(target_duration_seconds) * 1.12 / len(shots)))
    return max(2, min(8, round(required)))


def resolve_bound_asset_path(path_value: str, run_dir: Path) -> Path | None:
    if not path_value:
        return None
    candidate = Path(path_value)
    candidates = [candidate] if candidate.is_absolute() else [run_dir / candidate, ROOT_DIR / candidate]
    for path in candidates:
        if path.exists():
            return path
    return None


def local_material_filename(run_dir: Path, shot_id: str, source_path: Path) -> str:
    run_slug = re.sub(r"[^a-z0-9_-]+", "-", run_dir.name.lower()).strip("-_") or "run"
    safe_shot_id = re.sub(r"[^a-z0-9_-]+", "-", shot_id.lower()).strip("-_") or "shot"
    digest = file_sha256(source_path)[:10]
    return f"director-{run_slug}-{safe_shot_id}-{digest}{source_path.suffix.lower()}"


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_representative_video_terms(shot_plan: dict[str, Any], limit: int = MAX_MPT_VIDEO_TERMS) -> list[str]:
    primary_terms: list[str] = []
    extra_terms: list[str] = []
    for shot in shot_plan.get("shots", []) or []:
        if not isinstance(shot, dict) or shot.get("material_type") != "stock_video":
            continue
        terms = [str(term).strip() for term in shot.get("search_terms", []) if str(term).strip()]
        if not terms:
            continue
        primary_terms.append(terms[0])
        extra_terms.extend(terms[1:])

    ordered = []
    for term in [*primary_terms, *extra_terms]:
        if term and term not in ordered:
            ordered.append(term)
        if len(ordered) >= limit:
            break
    return ordered


def post_mpt_task(run_dir: Path, timeout: int = 30) -> dict[str, Any]:
    payload = read_json(run_dir / "mpt_params.json", {})
    endpoint = validate_local_mpt_endpoint(str(payload.get("endpoint") or ""))
    params = payload.get("params", {})
    if not endpoint or not params:
        raise ValueError("mpt_params.json is not ready")
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(
            f"MPT API is not reachable at {endpoint}. "
            f"Start MoneyPrinterTurbo API first: cd {MPT_DIR} && .venv/bin/python main.py. "
            f"Original error: {reason}"
        ) from exc
    write_json(run_dir / "mpt_submit_result.json", result)
    return result


def query_mpt_task(endpoint: str, task_id: str, timeout: int = 10) -> dict[str, Any]:
    endpoint = validate_local_mpt_endpoint(endpoint)
    base_url = endpoint[: -len(MPT_VIDEO_API_PATH)]
    task_url = urljoin(f"{base_url}/", f"api/v1/tasks/{task_id}")
    with urllib.request.urlopen(task_url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_mpt_task(
    run_dir: Path,
    task_id: str | None = None,
    timeout_seconds: int = 900,
    poll_seconds: int = 10,
    stall_seconds: int = 240,
) -> dict[str, Any]:
    payload = read_json(run_dir / "mpt_params.json", {})
    endpoint = validate_local_mpt_endpoint(str(payload.get("endpoint") or ""))
    submit_result = read_json(run_dir / "mpt_submit_result.json", {})
    resolved_task_id = task_id or str((submit_result.get("data") or {}).get("task_id") or "")
    if not resolved_task_id:
        raise ValueError("task_id is required or mpt_submit_result.json must contain data.task_id")

    started = time.monotonic()
    last_progress = None
    last_change = started
    snapshots: list[dict[str, Any]] = []
    consecutive_query_failures = 0
    while True:
        try:
            raw = query_mpt_task(endpoint, resolved_task_id)
            consecutive_query_failures = 0
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            # 长轮询必须容忍瞬时网络抖动 / MPT 重启，连续多次失败才放弃
            consecutive_query_failures += 1
            if consecutive_query_failures >= 5:
                report = {
                    "status": "query_failed",
                    "task_id": resolved_task_id,
                    "error": str(exc),
                    "snapshots": snapshots,
                }
                write_json(run_dir / "mpt_wait_report.json", report)
                raise RuntimeError(
                    f"MPT task query failed {consecutive_query_failures} times in a row: {exc}"
                ) from exc
            time.sleep(poll_seconds)
            continue
        task = raw.get("data") or {}
        progress = task.get("progress")
        state = task.get("state")
        videos = task.get("videos") or []
        snapshot = {"elapsed_seconds": round(time.monotonic() - started, 1), "state": state, "progress": progress, "videos": videos}
        snapshots.append(snapshot)
        write_json(
            run_dir / "mpt_wait_report.json",
            {
                "status": "running",
                "task_id": resolved_task_id,
                "last_snapshot": snapshot,
                "snapshots": snapshots[-30:],
            },
        )

        if progress != last_progress:
            last_progress = progress
            last_change = time.monotonic()
        if state == 1 and videos:
            report = {
                "status": "complete",
                "task_id": resolved_task_id,
                "task": task,
                "snapshots": snapshots,
            }
            write_json(run_dir / "mpt_wait_report.json", report)
            return report
        if state == -1:
            report = {
                "status": "failed",
                "task_id": resolved_task_id,
                "task": task,
                "snapshots": snapshots,
            }
            write_json(run_dir / "mpt_wait_report.json", report)
            raise RuntimeError(f"MPT task failed: {resolved_task_id}")

        now = time.monotonic()
        if now - started > timeout_seconds:
            report = {
                "status": "timeout",
                "task_id": resolved_task_id,
                "last_snapshot": snapshot,
                "snapshots": snapshots,
            }
            write_json(run_dir / "mpt_wait_report.json", report)
            raise TimeoutError(f"MPT task timed out after {timeout_seconds}s: {resolved_task_id}")
        if now - last_change > stall_seconds:
            report = {
                "status": "stalled",
                "task_id": resolved_task_id,
                "last_snapshot": snapshot,
                "snapshots": snapshots,
            }
            write_json(run_dir / "mpt_wait_report.json", report)
            raise TimeoutError(f"MPT task stalled for {stall_seconds}s: {resolved_task_id}")

        time.sleep(poll_seconds)


def resolve_mpt_output_video(run_dir: Path, mpt_dir: Path = MPT_DIR) -> Path | None:
    wait_report = read_json(run_dir / "mpt_wait_report.json", {})
    task = wait_report.get("task") if isinstance(wait_report, dict) else {}
    videos = task.get("videos") if isinstance(task, dict) else []
    if not isinstance(videos, list):
        return None
    for item in videos:
        value = str(item or "").strip()
        if not value:
            continue
        path = Path(value)
        candidates = [path] if path.is_absolute() else []
        if value.startswith("/tasks/"):
            candidates.append(mpt_dir / "storage" / value.lstrip("/"))
        else:
            candidates.append(mpt_dir / "storage" / value.lstrip("/"))
            candidates.append(run_dir / value)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
    return None


def contact_sheet_tile(video_path: Path, interval_seconds: int = 5) -> str:
    probe = ffprobe_json(video_path)
    duration = float(probe.get("format", {}).get("duration") or 0)
    frames = max(1, min(16, math.ceil(duration / max(interval_seconds, 1))))
    candidates = []
    for cols_candidate in range(1, 6):
        rows_candidate = math.ceil(frames / cols_candidate)
        if rows_candidate > 4:
            continue
        blanks = cols_candidate * rows_candidate - frames
        aspect_penalty = abs(cols_candidate - rows_candidate)
        candidates.append((blanks, aspect_penalty, rows_candidate, cols_candidate))
    _, _, rows, cols = min(candidates)
    return f"{cols}x{rows}"


def create_contact_sheet(video_path: Path, output_path: Path, interval_seconds: int = 5, timeout: int = 90) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps=1/{interval_seconds},scale=270:-1,tile={contact_sheet_tile(video_path, interval_seconds)}"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf, "-frames:v", "1", str(output_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg contact sheet timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg contact sheet failed").strip())
    return output_path
