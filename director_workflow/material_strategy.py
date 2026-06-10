from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .config import codex_executable
from .image_tools import build_codex_image_tasks
from .io_utils import ROOT_DIR, read_json, split_cn_sentences, write_json, write_text
from .mpt_tools import validate_shot_plan


MATERIAL_STRATEGY_DECISIONS = {"stock_video", "codex_image", "local_asset"}

ABSTRACT_IMAGE_TERMS = {
    "财报",
    "利润",
    "营收",
    "现金流",
    "毛利",
    "负债",
    "数据",
    "政策",
    "关税",
    "提案",
    "流程",
    "风险",
    "来源",
    "证明",
    "成本",
    "价格",
    "订单",
    "审核",
    "结构",
    "趋势",
    "对比",
}

STOCK_VIDEO_TERMS = {
    "工厂",
    "车间",
    "港口",
    "海关",
    "仓库",
    "物流",
    "集装箱",
    "办公室",
    "街道",
    "汽车",
    "新能源",
    "门店",
    "生产线",
    "货车",
    "船",
}

TOPIC_SEARCH_HINTS = {
    "比亚迪": ["electric vehicle factory", "new energy vehicle production", "car factory assembly line"],
    "财报": ["business finance report", "financial analyst office", "corporate earnings report"],
    "关税": ["customs inspection", "shipping containers port", "international trade logistics"],
    "美国": ["us government building", "newsroom broadcast", "american business office"],
    "外贸": ["shipping containers port", "global logistics warehouse", "customs documents"],
}


def normalize_approved_script(run_dir: Path) -> str:
    script_path = run_dir / "approved_script.md"
    text = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
    return text.replace("# Approved Script", "").strip()


def decide_material_type(sentence: str) -> str:
    if any(term in sentence for term in STOCK_VIDEO_TERMS):
        return "stock_video"
    if any(term in sentence for term in ABSTRACT_IMAGE_TERMS):
        return "codex_image"
    return "stock_video"


def topic_search_hints(topic: str) -> list[str]:
    hints: list[str] = []
    for key, terms in TOPIC_SEARCH_HINTS.items():
        if key in topic:
            hints.extend(terms)
    return list(dict.fromkeys(hints))


def build_visual_intent(sentence: str, topic: str, material_type: str) -> str:
    sentence = sentence.strip("。！？!?；; ")
    if material_type == "codex_image":
        return f"用一张竖屏编辑感画面表达：{sentence}"
    return f"真实竖屏视频素材，表现“{sentence}”对应的具体场景"


def build_search_terms(sentence: str, topic: str) -> list[str]:
    terms = topic_search_hints(topic)
    if any(word in sentence for word in ("工厂", "生产线", "车间", "汽车", "新能源")):
        terms.insert(0, "vertical electric vehicle factory production")
        terms.insert(1, "car assembly line vertical video")
    elif any(word in sentence for word in ("港口", "海关", "外贸", "供应链", "进口", "出口")):
        terms.insert(0, "vertical customs inspection shipping containers")
        terms.insert(1, "port logistics vertical video")
    elif any(word in sentence for word in ("办公室", "分析", "文件", "审核")):
        terms.insert(0, "vertical business analyst documents office")
        terms.insert(1, "finance documents office vertical video")
    elif any(word in sentence for word in ("美国", "政策", "提案", "关税")):
        terms.insert(0, "vertical government policy newsroom")
        terms.insert(1, "international trade news vertical video")
    else:
        terms.insert(0, "vertical business news office")
        terms.insert(1, "professional newsroom vertical video")
    return list(dict.fromkeys(term for term in terms if term))[:5]


def build_codex_image_prompt(sentence: str, topic: str) -> str:
    clean_sentence = sentence.strip("。！？!?；; ")
    return (
        "Vertical 9:16 editorial image for a Chinese short video. "
        f"Topic: {topic}. Shot meaning: {clean_sentence}. "
        "Create a polished realistic scene with finance/news analysis aesthetics, "
        "clear subject, strong composition, no visible text, no logos, no watermark, "
        "no fake charts with readable labels, leave the bottom area subtitle-safe."
    )


def build_rule_based_material_strategy(run_dir: Path) -> dict[str, Any]:
    brief = read_json(run_dir / "brief.json", {})
    topic = str(brief.get("topic") or "")
    script = normalize_approved_script(run_dir)
    sentences = split_cn_sentences(script)
    shots = []
    for index, sentence in enumerate(sentences, start=1):
        material_type = decide_material_type(sentence)
        shot = {
            "id": f"s{index:02d}",
            "narration": sentence,
            "duration_hint_seconds": 5,
            "material_type": material_type,
            "visual_intent": build_visual_intent(sentence, topic, material_type),
            "decision_reason": (
                "抽象数据/政策/流程类镜头，用生成图保证画面可控。"
                if material_type == "codex_image"
                else "具体现实场景镜头，优先使用 Pexels API 竖屏视频素材。"
            ),
            "search_terms": [],
            "codex_image_prompt": "",
        }
        if material_type == "stock_video":
            shot["search_terms"] = build_search_terms(sentence, topic)
        elif material_type == "codex_image":
            shot["codex_image_prompt"] = build_codex_image_prompt(sentence, topic)
        shots.append(shot)

    payload = {
        "status": "rule_based_ready",
        "strategy_owner": "director_workflow_rules",
        "primary_api_provider": "pexels",
        "shots": shots,
        "notes": [
            "Pexels is the primary API material provider.",
            "Codex should review this strategy before commercial publishing.",
        ],
    }
    return apply_material_strategy(run_dir, payload)


def build_stock_only_material_strategy(run_dir: Path) -> dict[str, Any]:
    brief = read_json(run_dir / "brief.json", {})
    topic = str(brief.get("topic") or "")
    script = normalize_approved_script(run_dir)
    sentences = split_cn_sentences(script)
    shots = []
    for index, sentence in enumerate(sentences, start=1):
        shots.append(
            {
                "id": f"s{index:02d}",
                "narration": sentence,
                "duration_hint_seconds": 5,
                "material_type": "stock_video",
                "visual_intent": build_visual_intent(sentence, topic, "stock_video"),
                "decision_reason": "手动 stock-only 兜底模式强制使用 API 视频，仅用于临时验证。",
                "search_terms": build_search_terms(sentence, topic),
                "codex_image_prompt": "",
            }
        )

    payload = {
        "status": "stock_only_ready",
        "strategy_owner": "director_workflow_auto",
        "primary_api_provider": "pexels",
        "shots": shots,
        "notes": [
            "Stock-only mode is intended for one-click automatic draft rendering.",
            "Use Codex reviewed mixed-material mode for higher quality commercial publishing.",
        ],
    }
    return apply_material_strategy(run_dir, payload)


def material_strategy_schema() -> dict[str, Any]:
    return {
        "status": "codex_reviewed",
        "strategy_owner": "codex",
        "primary_api_provider": "pexels",
        "shots": [
            {
                "id": "s01",
                "narration": "旁白句子",
                "duration_hint_seconds": 5,
                "material_type": "stock_video",
                "visual_intent": "这一句应该看到什么",
                "decision_reason": "为什么用 API 视频或生成图",
                "search_terms": ["vertical business newsroom"],
                "codex_image_prompt": "",
            }
        ],
    }


def build_material_strategy_prompt(run_dir: Path) -> str:
    brief = read_json(run_dir / "brief.json", {})
    source_notes = (run_dir / "source_notes.md").read_text(encoding="utf-8") if (run_dir / "source_notes.md").exists() else ""
    script = normalize_approved_script(run_dir)
    prompt = f"""
你是短视频前置导演。请基于 brief 和 approved_script，为每一句旁白决定素材类型。

规则：
1. 只输出 JSON，不要 markdown。
2. 每句旁白必须对应一个 shot。
3. material_type 只能是 stock_video 或 codex_image。已有本地/官方素材时才用 local_asset。
4. 具体真实场景优先 stock_video，例如工厂、港口、海关、办公室、物流、生产线。
5. 抽象概念、财报数据、政策流程、供应链证明、风险传导、对比结构优先 codex_image。
6. stock_video 必须给英文 search_terms，优先适合 Pexels 的竖屏视频搜索词。
7. codex_image 必须给英文 codex_image_prompt，要求 vertical 9:16、no text、no logo、subtitle-safe bottom area。
8. primary_api_provider 固定为 pexels，不要默认使用 Pixabay。
9. 不要把 stock 或生成图当事实证据，只作为视觉表达。

输出 JSON 结构必须符合：
{json.dumps(material_strategy_schema(), ensure_ascii=False, indent=2)}

brief:
{json.dumps(brief, ensure_ascii=False, indent=2)}

approved_script:
{script}

source_notes:
{source_notes}
""".strip()
    prompt_path = run_dir / "reports" / "material_strategy_prompt.md"
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
    raise ValueError("No JSON object found in Codex material strategy output")


def validate_material_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return {"valid": False, "issues": [{"field": "payload", "issue": "strategy must be a JSON object"}]}
    shots = payload.get("shots")
    if not isinstance(shots, list) or not shots:
        issues.append({"field": "shots", "issue": "strategy must contain shots"})
        return {"valid": False, "issues": issues}
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            issues.append({"shot_id": f"s{index:02d}", "field": "shot", "issue": "shot must be an object"})
            continue
        shot_id = str(shot.get("id") or f"s{index:02d}")
        material_type = str(shot.get("material_type") or "")
        if material_type not in MATERIAL_STRATEGY_DECISIONS:
            issues.append({"shot_id": shot_id, "field": "material_type", "issue": "unsupported material_type"})
        if material_type == "stock_video" and not any(str(term).strip() for term in shot.get("search_terms", []) or []):
            issues.append({"shot_id": shot_id, "field": "search_terms", "issue": "stock_video requires search_terms"})
        if material_type == "codex_image" and not str(shot.get("codex_image_prompt") or "").strip():
            issues.append({"shot_id": shot_id, "field": "codex_image_prompt", "issue": "codex_image requires prompt"})

    shot_validation = validate_shot_plan({"shots": shots})
    if not shot_validation["valid"]:
        issues.append({"field": "shot_plan", "issue": "shot_plan validation failed", "evidence": shot_validation["issues"]})
    return {"valid": not issues, "issues": issues}


def apply_material_strategy(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_material_strategy(payload)
    payload["validation"] = validation
    write_json(run_dir / "material_strategy.json", payload)
    if not validation["valid"]:
        write_json(run_dir / "shot_plan_validation.json", {"valid": False, "issues": validation["issues"]})
        raise ValueError("material_strategy.json is not valid; see material_strategy.json")

    shot_plan = {
        "status": payload.get("status") or "material_strategy_ready",
        "strategy_owner": payload.get("strategy_owner") or "unknown",
        "primary_api_provider": payload.get("primary_api_provider") or "pexels",
        "shots": payload["shots"],
    }
    shot_plan["validation"] = validate_shot_plan(shot_plan)
    write_json(run_dir / "shot_plan.json", shot_plan)
    write_json(
        run_dir / "search_terms.json",
        {
            "status": "complete",
            "primary_api_provider": "pexels",
            "items": [
                {"shot_id": shot.get("id"), "search_terms": shot.get("search_terms", [])}
                for shot in payload["shots"]
                if shot.get("material_type") == "stock_video"
            ],
        },
    )
    build_codex_image_tasks(run_dir)
    return shot_plan


def run_codex_material_strategy(
    run_dir: Path,
    model: str | None = None,
    timeout: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    prompt = build_material_strategy_prompt(run_dir)
    if dry_run:
        result = {
            "status": "dry_run",
            "prompt_path": str(run_dir / "reports" / "material_strategy_prompt.md"),
            "decision": "not_submitted",
        }
        write_json(run_dir / "material_strategy.json", result)
        return result

    output_file = run_dir / "reports" / "material_strategy_raw.txt"
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
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        write_json(
            run_dir / "material_strategy.json",
            {
                "status": "failed",
                "error": error,
                "prompt_path": str(run_dir / "reports" / "material_strategy_prompt.md"),
            },
        )
        raise RuntimeError(error)
    raw = output_file.read_text(encoding="utf-8") if output_file.exists() else result.stdout
    payload = _extract_json_object(raw)
    payload["status"] = payload.get("status") or "codex_reviewed"
    payload["strategy_owner"] = payload.get("strategy_owner") or "codex"
    payload["primary_api_provider"] = "pexels"
    return apply_material_strategy(run_dir, payload)


def summarize_material_mix(shot_plan: dict[str, Any]) -> dict[str, int]:
    counts = {"stock_video": 0, "codex_image": 0, "local_asset": 0}
    for shot in shot_plan.get("shots", []) or []:
        material_type = str(shot.get("material_type") or "")
        if material_type in counts:
            counts[material_type] += 1
    return counts
