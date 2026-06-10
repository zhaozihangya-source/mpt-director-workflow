from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import codex_executable
from .io_utils import ROOT_DIR, read_json, write_json, write_text
from .material_tools import media_info


REVIEW_SCHEMA: dict[str, Any] = {
    "status": "complete",
    "overall_score": 0,
    "decision": "needs_revision",
    "summary": "",
    "script_review": {
        "score": 0,
        "findings": [],
        "revision_notes": [],
    },
    "shot_review": {
        "score": 0,
        "findings": [],
        "revision_notes": [],
    },
    "asset_review": {
        "score": 0,
        "findings": [],
        "revision_notes": [],
    },
    "render_review": {
        "score": 0,
        "findings": [],
        "revision_notes": [],
    },
    "required_changes": [],
    "optional_changes": [],
}

REVIEW_SECTIONS = ("script_review", "shot_review", "asset_review", "render_review")
REVIEW_DECISIONS = {"pass", "needs_revision", "reject"}
MIN_PASS_SCORE = 75


def _compact_assets(asset_manifest: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    assets = asset_manifest.get("assets", [])
    compact = []
    for item in assets[:limit]:
        compact.append(
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "score": item.get("score"),
                "issues": item.get("issues", []),
                "orientation": item.get("orientation"),
                "duration_seconds": item.get("duration_seconds"),
                "width": item.get("width"),
                "height": item.get("height"),
            }
        )
    return compact


def _resolve_context_asset_path(path_value: str, run_dir: Path) -> Path | None:
    if not path_value:
        return None
    candidate = Path(path_value)
    candidates = [candidate] if candidate.is_absolute() else [run_dir / candidate, ROOT_DIR / candidate]
    for path in candidates:
        if path.exists():
            return path
    return None


def _bound_asset_summary(run_dir: Path, asset_bindings: dict[str, Any]) -> list[dict[str, Any]]:
    summary = []
    for binding in asset_bindings.get("bindings", []) or []:
        if not isinstance(binding, dict):
            continue
        asset_path = str(binding.get("asset_path") or "")
        resolved = _resolve_context_asset_path(asset_path, run_dir)
        media = media_info(resolved) if resolved else {}
        summary.append(
            {
                "shot_id": binding.get("shot_id"),
                "material_type": binding.get("material_type"),
                "asset_path": asset_path,
                "resolved_path": str(resolved) if resolved else "",
                "exists": bool(resolved),
                "source_provider": binding.get("source_provider"),
                "license_status": binding.get("license_status"),
                "commercial_use_status": binding.get("commercial_use_status"),
                "fit_score": binding.get("fit_score"),
                "approved": binding.get("approved"),
                "width": media.get("width"),
                "height": media.get("height"),
                "duration_seconds": media.get("duration_seconds"),
                "orientation": media.get("orientation"),
                "probe_error": media.get("probe_error"),
                "notes": binding.get("notes", ""),
            }
        )
    return summary


def collect_review_context(run_dir: Path) -> dict[str, Any]:
    reports_dir = run_dir / "reports"
    contact_sheet = reports_dir / "contact_sheet.jpg"
    approved_script = run_dir / "approved_script.md"
    asset_bindings = read_json(run_dir / "asset_bindings.json", {})
    return {
        "run_dir": str(run_dir),
        "brief": read_json(run_dir / "brief.json", {}),
        "source_notes": read_json(run_dir / "source_notes.json", {}),
        "approved_script": approved_script.read_text(encoding="utf-8") if approved_script.exists() else "",
        "script_audit": read_json(run_dir / "script_audit.json", {}),
        "shot_plan": read_json(run_dir / "shot_plan.json", {}),
        "search_terms": read_json(run_dir / "search_terms.json", {}),
        "image_tasks": read_json(run_dir / "image_tasks.json", {}),
        "asset_bindings": asset_bindings,
        "asset_binding_report": read_json(run_dir / "asset_binding_report.json", {}),
        "bound_asset_summary": _bound_asset_summary(run_dir, asset_bindings),
        "asset_manifest_summary": {
            "status": read_json(run_dir / "asset_manifest.json", {}).get("status"),
            "top_assets": _compact_assets(read_json(run_dir / "asset_manifest.json", {})),
        },
        "mpt_params": read_json(run_dir / "mpt_params.json", {}),
        "qa_report": read_json(run_dir / "qa_report.json", {}),
        "contact_sheet": str(contact_sheet) if contact_sheet.exists() else "",
    }


def build_semantic_review_prompt(run_dir: Path) -> str:
    context = collect_review_context(run_dir)
    prompt = f"""
你是 MoneyPrinterTurbo 短视频导演审核智能体。你要审核一个 director-run，不要只看技术成功，要判断它是否值得发布。

审核重点：
1. 稿件：前三秒钩子、句子长度、是否空泛、是否每句都能配画面。
2. 分镜：每句旁白是否有明确 visual_intent，素材类型是否合理。
3. 素材：stock video 搜索词是否具体，Codex 生图 prompt 是否足够清楚，素材清单是否支持竖屏短视频。
4. 成片：结合 qa_report 和 contact_sheet 路径判断技术风险；如果你没有实际看到 contact_sheet，只能标为 needs_visual_review。
5. 输出必须是 JSON，不能输出 markdown。

评分规则：
- 每个维度 0-100。
- overall_score 是综合分。
- decision 只能是 pass、needs_revision、reject 三者之一。
- 低于 75 分必须 needs_revision 或 reject。

必须输出这个 JSON 结构，字段不能缺失：
{json.dumps(REVIEW_SCHEMA, ensure_ascii=False, indent=2)}

director-run context:
{json.dumps(context, ensure_ascii=False, indent=2)}
""".strip()
    prompt_path = run_dir / "reports" / "semantic_review_prompt.md"
    write_text(prompt_path, prompt + "\n")
    return prompt


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in Codex review output")


def validate_semantic_review(review: dict[str, Any], min_pass_score: int = MIN_PASS_SCORE) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(review, dict):
        return {"valid": False, "issues": ["semantic review must be a JSON object"]}

    if review.get("status") != "complete":
        issues.append("status must be complete")

    decision = review.get("decision")
    if decision not in REVIEW_DECISIONS:
        issues.append("decision must be pass, needs_revision, or reject")

    overall_score = review.get("overall_score")
    if not isinstance(overall_score, int | float) or not 0 <= overall_score <= 100:
        issues.append("overall_score must be a number from 0 to 100")
    elif decision == "pass" and overall_score < min_pass_score:
        issues.append(f"pass requires overall_score >= {min_pass_score}")

    for section_name in REVIEW_SECTIONS:
        section = review.get(section_name)
        if not isinstance(section, dict):
            issues.append(f"{section_name} must be an object")
            continue
        section_score = section.get("score")
        if not isinstance(section_score, int | float) or not 0 <= section_score <= 100:
            issues.append(f"{section_name}.score must be a number from 0 to 100")
        for list_name in ("findings", "revision_notes"):
            if not isinstance(section.get(list_name), list):
                issues.append(f"{section_name}.{list_name} must be a list")

    if not isinstance(review.get("required_changes"), list):
        issues.append("required_changes must be a list")
    if not isinstance(review.get("optional_changes"), list):
        issues.append("optional_changes must be a list")
    if decision in {"needs_revision", "reject"} and not review.get("required_changes"):
        issues.append("needs_revision/reject requires at least one required_change")

    return {"valid": not issues, "issues": issues}


def run_codex_semantic_review(
    run_dir: Path,
    model: str | None = None,
    timeout: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    prompt = build_semantic_review_prompt(run_dir)
    if dry_run:
        result = {
            "status": "dry_run",
            "prompt_path": str(run_dir / "reports" / "semantic_review_prompt.md"),
            "decision": "not_reviewed",
        }
        write_json(run_dir / "semantic_review.json", result)
        return result

    output_file = run_dir / "reports" / "semantic_review_raw.txt"
    cmd = [
        codex_executable(),
        "exec",
        "-C",
        str(ROOT_DIR),
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(output_file),
    ]
    contact_sheet = run_dir / "reports" / "contact_sheet.jpg"
    if contact_sheet.exists():
        cmd.extend(["--image", str(contact_sheet)])
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        write_json(
            run_dir / "semantic_review.json",
            {
                "status": "failed",
                "error": (result.stderr or result.stdout or "").strip(),
                "prompt_path": str(run_dir / "reports" / "semantic_review_prompt.md"),
            },
        )
        raise RuntimeError((result.stderr or result.stdout or "").strip())

    raw = output_file.read_text(encoding="utf-8") if output_file.exists() else result.stdout
    review = _extract_json_object(raw)
    validation = validate_semantic_review(review)
    review["validation"] = validation
    write_json(run_dir / "semantic_review.json", review)
    if not validation["valid"]:
        raise ValueError(f"semantic_review.json failed validation: {validation['issues']}")
    return review


def build_revision_plan(run_dir: Path) -> dict[str, Any]:
    script_audit = read_json(run_dir / "script_audit.json", {})
    qa_report = read_json(run_dir / "qa_report.json", {})
    semantic = read_json(run_dir / "semantic_review.json", {})
    asset_binding_report = read_json(run_dir / "asset_binding_report.json", {})
    actions = []

    approved_script_audit = script_audit.get("approved_script")
    script_items = (
        [{"id": "approved_script", "audit": approved_script_audit.get("audit", {})}]
        if isinstance(approved_script_audit, dict) and approved_script_audit.get("audit")
        else script_audit.get("candidates", [])
    )
    for item in script_items:
        if not isinstance(item, dict):
            continue
        audit = item.get("audit", {})
        checks = audit.get("checks", {})
        if checks and not checks.get("char_count_ok", True):
            actions.append(
                {
                    "area": "script",
                    "priority": "high",
                    "action": "调整旁白字数到 brief 的目标区间",
                    "evidence": f"chars={audit.get('chars')}, expected={script_audit.get('min_chars')}-{script_audit.get('max_chars')}",
                }
            )
        if audit.get("long_sentences"):
            actions.append(
                {
                    "area": "script",
                    "priority": "medium",
                    "action": "拆短长句，改善字幕和 TTS 节奏",
                    "evidence": audit.get("long_sentences"),
                }
            )

    checks = qa_report.get("checks", {})
    if checks and not checks.get("duration_close", True):
        actions.append(
            {
                "area": "render",
                "priority": "high",
                "action": "按目标时长重写脚本或重新测 TTS 时长",
                "evidence": {
                    "duration_seconds": qa_report.get("duration_seconds"),
                    "target_duration_seconds": qa_report.get("target_duration_seconds"),
                    "duration_error_ratio": qa_report.get("duration_error_ratio"),
                },
            }
        )
    if qa_report.get("black_events"):
        actions.append(
            {
                "area": "material",
                "priority": "high",
                "action": "替换或重剪黑屏素材",
                "evidence": qa_report.get("black_events"),
            }
        )

    if not asset_binding_report or asset_binding_report.get("decision") != "pass":
        evidence = (
            asset_binding_report.get("issues", ["asset_binding_report.json is missing"])
            if isinstance(asset_binding_report, dict)
            else ["asset_binding_report.json is missing"]
        )
        actions.append(
            {
                "area": "material",
                "priority": "high",
                "action": "完成镜头级素材绑定和商业授权审核，asset_binding_report 必须 pass",
                "evidence": evidence,
            }
        )

    semantic_validation = validate_semantic_review(semantic) if semantic else {"valid": False, "issues": ["semantic_review.json is missing"]}
    if not semantic_validation["valid"]:
        actions.append(
            {
                "area": "semantic",
                "priority": "high",
                "action": "完成有效的 Codex/人工语义审核，审核缺失或格式不合格时不能放行",
                "evidence": semantic_validation["issues"],
            }
        )

    semantic_decision = semantic.get("decision") if isinstance(semantic, dict) else None
    if semantic_decision in {"needs_revision", "reject"}:
        for change in semantic.get("required_changes", []) or []:
            actions.append(
                {
                    "area": "semantic",
                    "priority": "high",
                    "action": change,
                    "evidence": "semantic_review.required_changes",
                }
            )
        if not semantic.get("required_changes"):
            actions.append(
                {
                    "area": "semantic",
                    "priority": "high",
                    "action": f"补充 semantic_review.required_changes，当前 decision={semantic_decision}",
                    "evidence": "semantic_review decision requires concrete changes",
                }
            )
    elif semantic_decision == "pass":
        for section_name in REVIEW_SECTIONS:
            section = semantic.get(section_name, {})
            if isinstance(section, dict) and section.get("score", 100) < MIN_PASS_SCORE:
                actions.append(
                    {
                        "area": "semantic",
                        "priority": "high",
                        "action": f"修复 {section_name} 低分项，pass 不能覆盖低于 {MIN_PASS_SCORE} 分的维度",
                        "evidence": {"section": section_name, "score": section.get("score")},
                    }
                )

    plan = {
        "status": "complete",
        "source_reports": {
            "script_audit": str(run_dir / "script_audit.json"),
            "qa_report": str(run_dir / "qa_report.json"),
            "semantic_review": str(run_dir / "semantic_review.json") if semantic else "",
        },
        "decision": "revise" if actions else "ready",
        "actions": actions,
    }
    write_json(run_dir / "revision_plan.json", plan)
    return plan
