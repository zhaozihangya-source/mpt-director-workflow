from __future__ import annotations

import hashlib
import http.client
import ssl
import time
import tomllib
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar

from .asset_tools import validate_asset_bindings
from .config import env_value, load_local_env
from .io_utils import MPT_DIR, read_json, write_json
from .material_tools import media_info
from .mpt_tools import validate_shot_plan


PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"
SUPPORTED_API_PROVIDERS = {"pexels", "pixabay"}
TRANSIENT_NETWORK_ERRORS = (
    TimeoutError,
    ssl.SSLError,
    http.client.HTTPException,
    urllib.error.URLError,
)
DEFAULT_NETWORK_ATTEMPTS = 4
T = TypeVar("T")


def configured_api_keys(provider: str) -> list[str]:
    provider = provider.lower().strip()
    env_names = {
        "pexels": ("PEXELS_API_KEY", "PEXELS_API_KEYS"),
        "pixabay": ("PIXABAY_API_KEY", "PIXABAY_API_KEYS"),
    }.get(provider)
    if not env_names:
        return []

    local_env = load_local_env()
    keys: list[str] = []
    for name in env_names:
        value = env_value(name, local_env)
        if value:
            keys.extend(part.strip() for part in value.split(",") if part.strip())

    config_path = MPT_DIR / "config.toml"
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
            app = data.get("app", {})
            config_keys = app.get(f"{provider}_api_keys", [])
            if isinstance(config_keys, str):
                keys.append(config_keys.strip())
            elif isinstance(config_keys, list):
                keys.extend(str(item).strip() for item in config_keys if str(item).strip())
        except tomllib.TOMLDecodeError:
            return list(dict.fromkeys(key for key in keys if key))

    return list(dict.fromkeys(key for key in keys if key))


def provider_order(primary_provider: str = "pexels", allow_pixabay_fallback: bool = False) -> list[str]:
    primary_provider = primary_provider.lower().strip() or "pexels"
    if primary_provider not in SUPPORTED_API_PROVIDERS:
        raise ValueError(f"unsupported provider: {primary_provider}")
    order = [primary_provider]
    if allow_pixabay_fallback and "pixabay" not in order:
        order.append("pixabay")
    return order


def _with_network_retries(
    operation: Callable[[], T],
    *,
    attempts: int = DEFAULT_NETWORK_ATTEMPTS,
    backoff_seconds: float = 1.5,
) -> T:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except TRANSIENT_NETWORK_ERRORS as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def _http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {})

    def operation() -> bytes:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    return json_loads_bytes(_with_network_retries(operation))


def json_loads_bytes(payload: bytes) -> dict[str, Any]:
    import json

    return json.loads(payload.decode("utf-8"))


def search_pexels_videos(search_term: str, api_key: str, per_page: int = 15, timeout: int = 30) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query": search_term, "per_page": per_page, "orientation": "portrait"})
    payload = _http_json(
        f"https://api.pexels.com/videos/search?{params}",
        headers={
            "Authorization": api_key,
            "User-Agent": "MoneyPrinterTurbo director workflow",
        },
        timeout=timeout,
    )
    results = []
    for item in payload.get("videos", []) or []:
        if not isinstance(item, dict):
            continue
        for file_item in item.get("video_files", []) or []:
            if not isinstance(file_item, dict) or not file_item.get("link"):
                continue
            width = int(file_item.get("width") or 0)
            height = int(file_item.get("height") or 0)
            results.append(
                {
                    "provider": "pexels",
                    "provider_asset_id": str(item.get("id") or ""),
                    "source_url": str(item.get("url") or ""),
                    "license_url": PEXELS_LICENSE_URL,
                    "download_url": str(file_item.get("link") or ""),
                    "width": width,
                    "height": height,
                    "duration_seconds": float(item.get("duration") or 0),
                    "search_term": search_term,
                    "quality": file_item.get("quality"),
                    "orientation": "portrait" if height > width else "landscape" if width > height else "square",
                }
            )
    return sorted(results, key=score_api_candidate, reverse=True)


def search_pixabay_videos(search_term: str, api_key: str, per_page: int = 15, timeout: int = 30) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": search_term, "per_page": per_page, "video_type": "all", "key": api_key})
    payload = _http_json(f"https://pixabay.com/api/videos/?{params}", timeout=timeout)
    results = []
    for item in payload.get("hits", []) or []:
        if not isinstance(item, dict):
            continue
        for quality, file_item in (item.get("videos", {}) or {}).items():
            if not isinstance(file_item, dict) or not file_item.get("url"):
                continue
            width = int(file_item.get("width") or 0)
            height = int(file_item.get("height") or 0)
            results.append(
                {
                    "provider": "pixabay",
                    "provider_asset_id": str(item.get("id") or ""),
                    "source_url": str(item.get("pageURL") or ""),
                    "license_url": PIXABAY_LICENSE_URL,
                    "download_url": str(file_item.get("url") or ""),
                    "width": width,
                    "height": height,
                    "duration_seconds": float(item.get("duration") or 0),
                    "search_term": search_term,
                    "quality": quality,
                    "orientation": "portrait" if height > width else "landscape" if width > height else "square",
                }
            )
    return sorted(results, key=score_api_candidate, reverse=True)


def score_api_candidate(candidate: dict[str, Any]) -> int:
    score = 60
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    duration = float(candidate.get("duration_seconds") or 0)
    provider = str(candidate.get("provider") or "")
    if provider == "pexels":
        score += 8
    if height > width:
        score += 18
    else:
        score -= 25
    if width >= 720 and height >= 1280:
        score += 8
    if width >= 1080 and height >= 1920:
        score += 8
    if duration >= 3:
        score += 6
    if duration >= 8:
        score += 4
    if not candidate.get("source_url"):
        score -= 8
    if not candidate.get("license_url"):
        score -= 8
    return max(0, min(100, score))


def candidate_issues(candidate: dict[str, Any], min_fit_score: int = 75) -> list[str]:
    issues = []
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if height <= width:
        issues.append("not_portrait")
    if width < 720 or height < 1280:
        issues.append("low_resolution")
    if float(candidate.get("duration_seconds") or 0) < 3:
        issues.append("too_short")
    if score_api_candidate(candidate) < min_fit_score:
        issues.append("low_fit_score")
    return issues


def _download_filename(shot_id: str, candidate: dict[str, Any]) -> str:
    provider = str(candidate.get("provider") or "api")
    asset_id = str(candidate.get("provider_asset_id") or "asset")
    digest = hashlib.sha256(str(candidate.get("download_url") or "").encode("utf-8")).hexdigest()[:10]
    return f"{shot_id}-{provider}-{asset_id}-{digest}.mp4"


def download_candidate(run_dir: Path, shot_id: str, candidate: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    output_dir = run_dir / "assets" / "api_videos" / shot_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _download_filename(shot_id, candidate)
    if not output_path.exists() or output_path.stat().st_size == 0:
        temp_path = output_path.with_suffix(output_path.suffix + ".part")

        def operation() -> None:
            temp_path.unlink(missing_ok=True)
            request = urllib.request.Request(
                str(candidate["download_url"]),
                headers={"User-Agent": "MoneyPrinterTurbo director workflow"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response, temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            temp_path.replace(output_path)

        try:
            _with_network_retries(operation, backoff_seconds=2.0)
        finally:
            temp_path.unlink(missing_ok=True)
    relative_path = output_path.relative_to(run_dir)
    info = media_info(output_path)
    enriched = {
        **candidate,
        "local_path": str(relative_path),
        "fit_score": score_api_candidate(candidate),
        "issues": sorted(set([*candidate_issues(candidate), *(info.get("issues") or [])])),
        "media_info": info,
        "license_status": "verified",
        "commercial_use_status": "platform_license_verified",
    }
    return enriched


def search_api_candidates_for_shot(
    shot: dict[str, Any],
    primary_provider: str = "pexels",
    allow_pixabay_fallback: bool = False,
    per_page: int = 15,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    terms = [str(term).strip() for term in shot.get("search_terms", []) or [] if str(term).strip()]
    candidates: list[dict[str, Any]] = []
    for provider in provider_order(primary_provider, allow_pixabay_fallback=allow_pixabay_fallback):
        keys = configured_api_keys(provider)
        if not keys:
            continue
        api_key = keys[0]
        for term in terms:
            if provider == "pexels":
                candidates.extend(search_pexels_videos(term, api_key=api_key, per_page=per_page, timeout=timeout))
            elif provider == "pixabay":
                candidates.extend(search_pixabay_videos(term, api_key=api_key, per_page=per_page, timeout=timeout))
        passing = [candidate for candidate in candidates if not candidate_issues(candidate)]
        if passing:
            break
    # 去重键必须是视频本身（provider + asset id），不能用 download_url：
    # 同一视频的不同分辨率变体 download_url 不同，会让候选池虚胖。
    deduped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        asset_id = str(candidate.get("provider_asset_id") or "")
        key = (
            f"{candidate.get('provider')}:{asset_id}"
            if asset_id
            else str(candidate.get("download_url") or candidate.get("source_url"))
        )
        prior = deduped.get(key)
        if prior is None or score_api_candidate(candidate) > score_api_candidate(prior):
            deduped[key] = candidate
    return sorted(deduped.values(), key=score_api_candidate, reverse=True)


def collect_api_assets(
    run_dir: Path,
    primary_provider: str = "pexels",
    allow_pixabay_fallback: bool = False,
    per_page: int = 15,
    max_candidates_per_shot: int = 2,
    download: bool = True,
) -> dict[str, Any]:
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    validation = validate_shot_plan(shot_plan)
    if not validation["valid"]:
        raise ValueError("shot_plan.json is not valid; build material strategy before collecting API assets")

    shot_results = []
    for shot in shot_plan.get("shots", []) or []:
        if not isinstance(shot, dict) or shot.get("material_type") != "stock_video":
            continue
        shot_id = str(shot.get("id") or "")
        raw_candidates = search_api_candidates_for_shot(
            shot,
            primary_provider=primary_provider,
            allow_pixabay_fallback=allow_pixabay_fallback,
            per_page=per_page,
        )
        selected_raw = raw_candidates[:max_candidates_per_shot]
        candidates = [
            download_candidate(run_dir, shot_id, candidate) if download else {**candidate, "fit_score": score_api_candidate(candidate), "issues": candidate_issues(candidate)}
            for candidate in selected_raw
        ]
        shot_results.append(
            {
                "shot_id": shot_id,
                "narration": shot.get("narration", ""),
                "visual_intent": shot.get("visual_intent", ""),
                "search_terms": shot.get("search_terms", []),
                "provider_priority": provider_order(primary_provider, allow_pixabay_fallback),
                "candidates": candidates,
            }
        )

    payload = {
        "status": "complete",
        "primary_provider": primary_provider,
        "allow_pixabay_fallback": allow_pixabay_fallback,
        "downloaded": download,
        "shots": shot_results,
    }
    write_json(run_dir / "api_asset_candidates.json", payload)
    merge_api_candidates_into_manifest(run_dir, payload)
    return payload


def merge_api_candidates_into_manifest(run_dir: Path, candidates_payload: dict[str, Any]) -> dict[str, Any]:
    manifest = read_json(run_dir / "asset_manifest.json", {"status": "pending", "assets": []})
    existing_assets = [asset for asset in manifest.get("assets", []) or [] if isinstance(asset, dict)]
    by_path = {str(asset.get("path")): asset for asset in existing_assets if asset.get("path")}
    for shot in candidates_payload.get("shots", []) or []:
        for candidate in shot.get("candidates", []) or []:
            local_path = candidate.get("local_path")
            if not local_path:
                continue
            by_path[str(local_path)] = {
                "path": local_path,
                "name": Path(local_path).name,
                "score": candidate.get("fit_score"),
                "issues": candidate.get("issues", []),
                "source_provider": candidate.get("provider"),
                "provider_asset_id": candidate.get("provider_asset_id"),
                "source_url": candidate.get("source_url"),
                "license_url": candidate.get("license_url"),
                "license_status": candidate.get("license_status", "verified"),
                "commercial_use_status": candidate.get("commercial_use_status", "platform_license_verified"),
                "orientation": candidate.get("orientation"),
                "width": candidate.get("width"),
                "height": candidate.get("height"),
                "duration_seconds": candidate.get("duration_seconds"),
            }
    manifest = {
        **manifest,
        "status": "complete",
        "assets": sorted(by_path.values(), key=lambda item: str(item.get("path") or "")),
    }
    write_json(run_dir / "asset_manifest.json", manifest)
    return manifest


def select_api_assets(
    run_dir: Path,
    min_fit_score: int = 75,
    approve_passing: bool = False,
) -> dict[str, Any]:
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    ordered_shot_ids = [
        str(shot.get("id"))
        for shot in shot_plan.get("shots", []) or []
        if isinstance(shot, dict) and shot.get("id")
    ]
    candidates_payload = read_json(run_dir / "api_asset_candidates.json", {})
    existing = read_json(run_dir / "asset_bindings.json", {"status": "draft", "bindings": []})
    bindings = [binding for binding in existing.get("bindings", []) or [] if isinstance(binding, dict)]
    by_shot = {str(binding.get("shot_id")): binding for binding in bindings if binding.get("shot_id")}
    issues: list[dict[str, Any]] = []

    used_asset_keys: set[str] = set()
    for shot in candidates_payload.get("shots", []) or []:
        shot_id = str(shot.get("shot_id") or "")
        passing = [
            candidate
            for candidate in shot.get("candidates", []) or []
            if candidate.get("local_path")
            and candidate.get("fit_score", 0) >= min_fit_score
            and not candidate.get("issues")
        ]
        if not passing:
            issues.append({"shot_id": shot_id, "field": "api_asset_candidates", "issue": "no passing API video candidate"})
            continue
        # 跨镜头去重：优先选未被其他镜头使用的素材，避免成片画面重复
        def asset_key(candidate: dict[str, Any]) -> str:
            return str(candidate.get("provider_asset_id") or candidate.get("local_path") or "")

        fresh = [candidate for candidate in passing if asset_key(candidate) not in used_asset_keys]
        if fresh:
            selected = fresh[0]
        else:
            selected = passing[0]
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "asset_reuse",
                    "issue": "all passing candidates already used by other shots; reusing footage",
                }
            )
        used_asset_keys.add(asset_key(selected))
        binding = by_shot.get(shot_id, {"shot_id": shot_id})
        binding.update(
            {
                "shot_id": shot_id,
                "material_type": "stock_video",
                "asset_path": selected["local_path"],
                "source_provider": selected["provider"],
                "provider_asset_id": selected.get("provider_asset_id"),
                "source_url": selected.get("source_url"),
                "license_url": selected.get("license_url"),
                "license_status": "verified",
                "commercial_use_status": "platform_license_verified",
                "fit_score": int(selected["fit_score"]),
                "approved": bool(approve_passing),
                "notes": "Selected by director API material collector. Pexels is preferred before Pixabay fallback.",
            }
        )
        by_shot[shot_id] = binding

    output = {
        "status": "approved" if approve_passing and not issues else "draft",
        "bindings": [by_shot[shot_id] for shot_id in ordered_shot_ids if shot_id in by_shot],
    }
    write_json(run_dir / "asset_bindings.json", output)
    report = validate_asset_bindings(run_dir, min_fit_score=min_fit_score)
    selection_report = {"status": "complete", "selection_issues": issues, "asset_binding_report": report}
    write_json(run_dir / "api_asset_selection_report.json", selection_report)
    return {"asset_bindings": output, "asset_binding_report": report, "selection_issues": issues}
