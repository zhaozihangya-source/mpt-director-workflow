from __future__ import annotations

from pathlib import Path
from typing import Any

from .io_utils import ROOT_DIR, read_json, write_json
from .mpt_tools import validate_shot_plan

APPROVED_COMMERCIAL_STATUSES = {"approved", "generated_owned", "platform_license_verified"}
MIN_ASSET_FIT_SCORE = 75


def _path_exists(path_value: str, run_dir: Path) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if path.is_absolute():
        return path.exists()
    return (run_dir / path).exists() or (ROOT_DIR / path).exists()


def _manifest_asset_map(asset_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for asset in asset_manifest.get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("path"):
            result[str(asset["path"])] = asset
    return result


def build_asset_binding_template(run_dir: Path) -> dict[str, Any]:
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    existing = read_json(run_dir / "asset_bindings.json", {})
    existing_by_shot = {
        str(item.get("shot_id")): item
        for item in existing.get("bindings", []) or []
        if isinstance(item, dict) and item.get("shot_id")
    }
    manifest = read_json(run_dir / "asset_manifest.json", {})
    manifest_assets = [item for item in manifest.get("assets", []) or [] if isinstance(item, dict)]
    reusable_assets = iter(manifest_assets)

    bindings = []
    for shot in shot_plan.get("shots", []) or []:
        if not isinstance(shot, dict):
            continue
        shot_id = str(shot.get("id") or "")
        prior = existing_by_shot.get(shot_id, {})
        material_type = str(shot.get("material_type") or prior.get("material_type") or "")
        suggested_asset = ""
        if material_type in {"stock_video", "local_asset"}:
            suggested_asset = str(prior.get("asset_path") or next(reusable_assets, {}).get("path") or "")
        elif material_type == "codex_image":
            suggested_asset = str(prior.get("asset_path") or f"assets/codex_images/{shot_id}.png")

        bindings.append(
            {
                "shot_id": shot_id,
                "narration": shot.get("narration", ""),
                "material_type": material_type,
                "visual_intent": shot.get("visual_intent", ""),
                "asset_path": suggested_asset,
                "source_provider": prior.get("source_provider") or "unknown",
                "provider_asset_id": prior.get("provider_asset_id") or "",
                "source_url": prior.get("source_url") or "",
                "license_url": prior.get("license_url") or "",
                "search_term": prior.get("search_term") or "",
                "license_status": prior.get("license_status") or "unknown",
                "commercial_use_status": prior.get("commercial_use_status") or "unknown",
                "fit_score": prior.get("fit_score"),
                "approved": bool(prior.get("approved", False)),
                "notes": prior.get("notes", ""),
            }
        )

    payload = {
        "status": "draft",
        "bindings": bindings,
        "notes": [
            "Each shot must bind to one selected asset before commercial delivery.",
            "source_provider, license_status, commercial_use_status, fit_score, and approved are required for pass.",
        ],
    }
    write_json(run_dir / "asset_bindings.json", payload)
    report = validate_asset_bindings(run_dir)
    return {"asset_bindings": payload, "asset_binding_report": report}


def validate_asset_bindings(run_dir: Path, min_fit_score: int = MIN_ASSET_FIT_SCORE) -> dict[str, Any]:
    shot_plan = read_json(run_dir / "shot_plan.json", {})
    shot_validation = validate_shot_plan(shot_plan)
    asset_bindings = read_json(run_dir / "asset_bindings.json", {})
    asset_manifest = read_json(run_dir / "asset_manifest.json", {})
    manifest_by_path = _manifest_asset_map(asset_manifest)
    issues: list[dict[str, Any]] = []

    if not shot_validation["valid"]:
        issues.append(
            {
                "shot_id": None,
                "field": "shot_plan",
                "issue": "shot_plan is not valid",
                "evidence": shot_validation["issues"],
            }
        )

    bindings = asset_bindings.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
        issues.append({"shot_id": None, "field": "bindings", "issue": "asset_bindings.json must contain bindings list"})

    shots = [shot for shot in shot_plan.get("shots", []) or [] if isinstance(shot, dict)]
    required_shot_ids = {str(shot.get("id") or "") for shot in shots if shot.get("id")}
    binding_by_shot = {
        str(binding.get("shot_id")): binding for binding in bindings if isinstance(binding, dict) and binding.get("shot_id")
    }

    missing = sorted(required_shot_ids - set(binding_by_shot))
    for shot_id in missing:
        issues.append({"shot_id": shot_id, "field": "asset_path", "issue": "shot has no asset binding"})

    extra = sorted(set(binding_by_shot) - required_shot_ids)
    for shot_id in extra:
        issues.append({"shot_id": shot_id, "field": "bindings", "issue": "binding does not exist in shot_plan"})

    for shot_id in sorted(required_shot_ids):
        binding = binding_by_shot.get(shot_id)
        if not binding:
            continue

        asset_path = str(binding.get("asset_path") or "").strip()
        if not asset_path:
            issues.append({"shot_id": shot_id, "field": "asset_path", "issue": "asset_path is required"})
        elif not _path_exists(asset_path, run_dir):
            issues.append({"shot_id": shot_id, "field": "asset_path", "issue": "asset_path does not exist", "evidence": asset_path})

        if not str(binding.get("source_provider") or "").strip() or binding.get("source_provider") == "unknown":
            issues.append({"shot_id": shot_id, "field": "source_provider", "issue": "source_provider must be known"})
        if binding.get("license_status") in {None, "", "unknown"}:
            issues.append({"shot_id": shot_id, "field": "license_status", "issue": "license_status must be verified"})
        if binding.get("commercial_use_status") not in APPROVED_COMMERCIAL_STATUSES:
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "commercial_use_status",
                    "issue": f"commercial_use_status must be one of {sorted(APPROVED_COMMERCIAL_STATUSES)}",
                }
            )

        fit_score = binding.get("fit_score")
        if not isinstance(fit_score, int | float):
            issues.append({"shot_id": shot_id, "field": "fit_score", "issue": "fit_score is required"})
        elif fit_score < min_fit_score:
            issues.append({"shot_id": shot_id, "field": "fit_score", "issue": f"fit_score must be >= {min_fit_score}"})

        if not binding.get("approved"):
            issues.append({"shot_id": shot_id, "field": "approved", "issue": "binding must be approved"})

        manifest_asset = manifest_by_path.get(asset_path)
        if manifest_asset and manifest_asset.get("issues"):
            issues.append(
                {
                    "shot_id": shot_id,
                    "field": "asset_manifest",
                    "issue": "selected asset has manifest issues",
                    "evidence": manifest_asset.get("issues"),
                }
            )

    report = {
        "status": "complete",
        "decision": "pass" if not issues else "needs_revision",
        "min_fit_score": min_fit_score,
        "required_shots": sorted(required_shot_ids),
        "bound_shots": sorted(set(binding_by_shot)),
        "issues": issues,
    }
    write_json(run_dir / "asset_binding_report.json", report)
    return report
