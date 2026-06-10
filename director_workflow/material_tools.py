from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .io_utils import write_json


def ffprobe_json(path: Path, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"ffprobe_timeout_after_{timeout}s"}
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout or "").strip()}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "ffprobe_json_decode_error"}


def media_info(path: Path) -> dict[str, Any]:
    probe = ffprobe_json(path)
    streams = probe.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    fmt = probe.get("format", {})
    duration = float(fmt.get("duration") or video_stream.get("duration") or 0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    size = int(fmt.get("size") or path.stat().st_size if path.exists() else 0)
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower().lstrip("."),
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "size_bytes": size,
        "has_video": bool(video_stream),
        "orientation": "portrait" if height > width else "landscape" if width > height else "square",
        "probe_error": probe.get("error"),
        "source_provider": None,
        "license_status": "unknown",
        "commercial_use_status": "unknown",
    }


def score_asset(info: dict[str, Any], target_aspect: str = "9:16") -> dict[str, Any]:
    score = 100
    issues = []
    if not info.get("has_video") and info.get("extension") not in {"jpg", "jpeg", "png", "bmp"}:
        score -= 50
        issues.append("not_video_or_supported_image")
    if info.get("width", 0) < 720 or info.get("height", 0) < 720:
        score -= 20
        issues.append("low_resolution")
    if target_aspect == "9:16" and info.get("orientation") != "portrait":
        score -= 15
        issues.append("not_portrait")
    if info.get("duration_seconds", 0) and info["duration_seconds"] < 3:
        score -= 10
        issues.append("too_short")
    if info.get("probe_error"):
        score -= 40
        issues.append("probe_error")
    if info.get("license_status") == "unknown":
        score -= 15
        issues.append("unknown_license")
    if not info.get("source_provider"):
        score -= 10
        issues.append("missing_source_provider")
    return {"score": max(score, 0), "issues": issues}


def inventory_assets(run_dir: Path, paths: list[Path], target_aspect: str = "9:16") -> dict[str, Any]:
    assets = []
    for path in paths:
        if path.is_dir():
            candidates = [
                *path.glob("*.mp4"),
                *path.glob("*.mov"),
                *path.glob("*.webm"),
                *path.glob("*.jpg"),
                *path.glob("*.jpeg"),
                *path.glob("*.png"),
            ]
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.exists():
                continue
            info = media_info(candidate)
            assets.append({**info, **score_asset(info, target_aspect)})
    manifest = {
        "status": "complete",
        "target_aspect": target_aspect,
        "assets": sorted(assets, key=lambda item: item["score"], reverse=True),
        "commercial_notes": [
            "license_status and source_provider default to unknown for local files.",
            "Do not approve commercial delivery until selected assets have verified provenance.",
        ],
    }
    write_json(run_dir / "asset_manifest.json", manifest)
    return manifest
