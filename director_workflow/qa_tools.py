from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .io_utils import read_json, write_json
from .material_tools import ffprobe_json


def run_ffmpeg_filter(video_path: Path, vf: str, timeout: int = 90) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(video_path), "-vf", vf, "-an", "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "error": f"ffmpeg_timeout_after_{timeout}s"}
    output = (result.stderr or result.stdout or "").strip()
    return {"output": output, "error": output if result.returncode != 0 else None}


def detect_black(video_path: Path) -> dict[str, Any]:
    result = run_ffmpeg_filter(video_path, "blackdetect=d=0.2:pix_th=0.10")
    output = result["output"]
    return {
        "events": [line for line in output.splitlines() if "black_start:" in line],
        "error": result["error"],
    }


def detect_audio_volume(video_path: Path, timeout: int = 90) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(video_path),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"mean_volume": None, "max_volume": None, "error": f"ffmpeg_timeout_after_{timeout}s"}
    text = result.stderr or result.stdout or ""
    mean = None
    max_volume = None
    for line in text.splitlines():
        if "mean_volume:" in line:
            mean = line.split("mean_volume:", 1)[1].strip()
        if "max_volume:" in line:
            max_volume = line.split("max_volume:", 1)[1].strip()
    return {
        "mean_volume": mean,
        "max_volume": max_volume,
        "error": text.strip() if result.returncode != 0 else None,
    }


def qa_video(run_dir: Path, video_path: Path) -> dict[str, Any]:
    if not video_path.exists():
        report = {
            "status": "complete",
            "video_path": str(video_path),
            "duration_seconds": 0,
            "target_duration_seconds": float(read_json(run_dir / "brief.json", {}).get("target_duration_seconds") or 0),
            "duration_error_ratio": None,
            "resolution": {"width": 0, "height": 0},
            "audio": {"mean_volume": None, "max_volume": None, "error": "video_missing"},
            "black_events": [],
            "errors": ["video_missing"],
            "checks": {
                "file_exists": False,
                "probe_ok": False,
                "has_video": False,
                "has_audio": False,
                "portrait_1080x1920_or_better": False,
                "duration_close": False,
                "blackdetect_ok": False,
                "no_black_events": False,
                "volume_scan_ok": False,
            },
            "decision": "needs_revision",
            "notes": ["Video file is missing; render QA cannot continue."],
        }
        write_json(run_dir / "qa_report.json", report)
        return report

    probe = ffprobe_json(video_path)
    probe_error = probe.get("error")
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
    brief = read_json(run_dir / "brief.json", {})
    target_duration = float(brief.get("target_duration_seconds") or 0)
    duration = float(fmt.get("duration") or 0)
    duration_error = abs(duration - target_duration) / target_duration if target_duration else 0
    if probe_error:
        black = {"events": [], "error": "skipped_after_probe_error"}
        volume = {"mean_volume": None, "max_volume": None, "error": "skipped_after_probe_error"}
    else:
        black = detect_black(video_path)
        volume = detect_audio_volume(video_path)
    black_events = black["events"]
    errors = [error for error in [probe_error, black.get("error"), volume.get("error")] if error]
    checks = {
        "file_exists": video_path.exists(),
        "probe_ok": not probe_error,
        "has_video": bool(video_stream),
        "has_audio": bool(audio_stream),
        "portrait_1080x1920_or_better": int(video_stream.get("width") or 0) >= 1080
        and int(video_stream.get("height") or 0) >= 1920,
        "duration_close": duration_error <= 0.12 if target_duration else True,
        "blackdetect_ok": not black.get("error"),
        "no_black_events": not black_events,
        "volume_scan_ok": not volume.get("error"),
    }
    report = {
        "status": "complete",
        "video_path": str(video_path),
        "duration_seconds": round(duration, 3),
        "target_duration_seconds": target_duration,
        "duration_error_ratio": round(duration_error, 4) if target_duration else None,
        "resolution": {
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
        },
        "audio": volume,
        "black_events": black_events,
        "errors": errors,
        "checks": checks,
        "decision": "pass" if all(checks.values()) else "needs_revision",
        "notes": [
            "Semantic visual-to-script fit still requires Codex/manual contact-sheet review.",
            "Use this report as the first technical gate, not final creative approval.",
        ],
    }
    write_json(run_dir / "qa_report.json", report)
    return report
