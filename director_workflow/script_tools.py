from __future__ import annotations

import http.client
import json
import math
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import codex_executable, deepseek_api_credentials
from .io_utils import ROOT_DIR, count_cjk_chars, normalize_script_text, read_json, split_cn_sentences, write_json


DEFAULT_SCRIPT_SYSTEM = (
    "你是短视频脚本批量生成器。只输出 JSON，不要 markdown。"
    "脚本必须具体、可配画面、适合竖屏短视频。"
)


def build_deepseek_prompt(brief: dict[str, Any], count: int = 5) -> str:
    char_range = brief.get("estimated_script_chars", {})
    min_chars = char_range.get("min", 160)
    max_chars = char_range.get("max", 210)
    sentence_min = max(6, math.ceil(int(min_chars) / 28))
    sentence_max = max(sentence_min, min(14, math.ceil(int(max_chars) / 20)))
    feedback = str(brief.get("revision_feedback") or "").strip()
    feedback_block = (
        f"\n上一轮 TTS 实测反馈（必须据此调整字数）：{feedback}\n" if feedback else ""
    )
    return f"""
请基于下面 brief 生成 {count} 个中文短视频旁白候选。
{feedback_block}
要求：
1. 每个脚本必须严格达到 {min_chars}-{max_chars} 个中文汉字；少于下限视为无效。
2. 每个脚本 {sentence_min}-{sentence_max} 句，每句 18-30 个中文汉字。
3. 第一句必须是强钩子。
4. 不要标题，不要 markdown，不要“欢迎观看”。
5. 每个脚本必须附带 5 个英文 stock video 搜索词。
6. 输出前自检字数，不能输出明显短稿。
7. 只输出 JSON，格式为：
{{
  "candidates": [
    {{
      "id": "v1",
      "angle": "角度说明",
      "script": "旁白正文",
      "search_terms": ["term one", "term two"],
      "reason": "为什么这个版本可用"
    }}
  ]
}}

brief:
{json.dumps(brief, ensure_ascii=False)}
""".strip()


def parse_json_response_text(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as inner_exc:
                snippet = text[:600].replace("\n", "\\n")
                raise ValueError(f"model response was not valid JSON: {inner_exc}; snippet={snippet}") from inner_exc
        snippet = text[:600].replace("\n", "\\n")
        raise ValueError(f"model response was not valid JSON: {exc}; snippet={snippet}") from exc


def call_deepseek_api(
    prompt: str,
    model: str | None = None,
    timeout: int = 120,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    resolved_key, resolved_base_url, resolved_model = deepseek_api_credentials()
    api_key = (api_key or resolved_key).strip()
    base_url = (base_url or resolved_base_url).rstrip("/")
    model = model or resolved_model
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured; set env/.env.local or macOS Keychain service codex.deepseek.api_key")

    payload = {
        "model": model,
        "temperature": 0.8,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": DEFAULT_SCRIPT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "mpt-director-workflow",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API failed: HTTP {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek API failed: {exc.reason}") from exc
    except (http.client.HTTPException, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek API failed while reading response: {exc}") from exc

    choices = raw_payload.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek API response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = str((message or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("DeepSeek API response has empty message content")
    return parse_json_response_text(content)


def call_deepseek_cli(prompt: str, model: str = "deepseek-v4-flash", timeout: int = 120) -> dict[str, Any]:
    cmd = [
        "deepseek",
        "ask",
        "--model",
        model,
        "--temperature",
        "0.8",
        "--max-tokens",
        "4096",
        "--timeout",
        str(timeout),
        "--system",
        DEFAULT_SCRIPT_SYSTEM,
        prompt,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout + 30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"deepseek command timed out after {timeout + 30}s") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "").strip())
    return parse_json_response_text(result.stdout)


def call_deepseek(
    prompt: str,
    model: str = "deepseek-v4-flash",
    timeout: int = 120,
    provider: str = "auto",
) -> dict[str, Any]:
    provider = provider.lower().strip()
    if provider not in {"auto", "api", "cli"}:
        raise ValueError("provider must be auto, api, or cli")
    if provider in {"auto", "api"}:
        try:
            return call_deepseek_api(prompt, model=model, timeout=timeout)
        except (RuntimeError, ValueError):
            if provider == "api":
                raise
    return call_deepseek_cli(prompt, model=model, timeout=timeout)


def validate_generated_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek response must be a JSON object")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("DeepSeek response must contain candidates list")

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates, start=1):
        if not isinstance(item, dict):
            rejected.append({"index": index, "issue": "candidate must be an object"})
            continue

        script = str(item.get("script") or "").strip()
        terms = item.get("search_terms")
        clean_terms = [str(term).strip() for term in terms if str(term).strip()] if isinstance(terms, list) else []
        if not script:
            rejected.append({"index": index, "id": item.get("id"), "issue": "script is required"})
            continue
        if not clean_terms:
            rejected.append({"index": index, "id": item.get("id"), "issue": "search_terms must contain at least one term"})
            continue

        candidates.append(
            {
                **item,
                "id": str(item.get("id") or f"v{index}"),
                "angle": str(item.get("angle") or "").strip(),
                "script": script,
                "search_terms": clean_terms[:10],
                "reason": str(item.get("reason") or "").strip(),
            }
        )

    if not candidates:
        raise ValueError(f"No valid DeepSeek script candidates: {rejected}")
    return {"candidates": candidates, "rejected_candidates": rejected}


def audit_script(script: str, min_chars: int, max_chars: int) -> dict[str, Any]:
    normalized = normalize_script_text(script)
    sentences = split_cn_sentences(script)
    chars = count_cjk_chars(normalized)
    long_sentences = [s for s in sentences if count_cjk_chars(s) > 30]
    min_sentences = max(5, math.ceil(min_chars / 30))
    max_sentences = max(9, min(16, math.ceil(max_chars / 18)))
    weak_terms = ["前所未有", "改变", "赋能", "生态", "未来", "智能化", "高质量"]
    weak_hits = [term for term in weak_terms if term in script]
    checks = {
        "char_count_ok": min_chars <= chars <= max_chars,
        "sentence_count_ok": min_sentences <= len(sentences) <= max_sentences,
        "sentence_length_ok": not long_sentences,
        "has_hook": bool(sentences and count_cjk_chars(sentences[0]) <= 30),
        "generic_terms_limited": len(weak_hits) <= 2,
    }
    return {
        "chars": chars,
        "sentences": sentences,
        "expected_sentence_count": {"min": min_sentences, "max": max_sentences},
        "long_sentences": long_sentences,
        "generic_terms": weak_hits,
        "checks": checks,
        "pass": all(checks.values()),
    }


def audit_candidates(run_dir: Path) -> dict[str, Any]:
    brief = read_json(run_dir / "brief.json", {})
    candidates_data = read_json(run_dir / "script_candidates.json", {})
    char_range = brief.get("estimated_script_chars", {})
    min_chars = int(char_range.get("min", 160))
    max_chars = int(char_range.get("max", 210))
    approved_script_path = run_dir / "approved_script.md"
    approved_script_audit = None
    if approved_script_path.exists():
        approved_script = approved_script_path.read_text(encoding="utf-8").replace("# Approved Script", "").strip()
        if approved_script:
            approved_script_audit = {
                "path": str(approved_script_path),
                "audit": audit_script(approved_script, min_chars, max_chars),
            }
    audited = []
    for item in candidates_data.get("candidates", []):
        if not isinstance(item, dict):
            continue
        script = item.get("script", "")
        audited.append({**item, "audit": audit_script(script, min_chars, max_chars)})
    report = {
        "status": "complete",
        "min_chars": min_chars,
        "max_chars": max_chars,
        "candidates": audited,
        "recommended_id": next((i.get("id") for i in audited if i["audit"]["pass"]), None),
    }
    if approved_script_audit:
        report["approved_script"] = approved_script_audit
    write_json(run_dir / "script_audit.json", report)
    return report


def save_generated_candidates(run_dir: Path, payload: dict[str, Any], model: str) -> None:
    validated = validate_generated_candidates(payload)
    write_json(
        run_dir / "script_candidates.json",
        {
            "status": "complete",
            "generator": "deepseek",
            "model": model,
            "candidates": validated["candidates"],
            "rejected_candidates": validated["rejected_candidates"],
        },
    )


def build_codex_script_director_prompt(run_dir: Path) -> str:
    brief = read_json(run_dir / "brief.json", {})
    candidates_payload = read_json(run_dir / "script_candidates.json", {})
    source_notes = (run_dir / "source_notes.md").read_text(encoding="utf-8") if (run_dir / "source_notes.md").exists() else ""
    char_range = brief.get("estimated_script_chars", {}) if isinstance(brief, dict) else {}
    min_chars = int(char_range.get("min", 160) or 160)
    max_chars = int(char_range.get("max", 210) or 210)
    sentence_min = max(5, math.ceil(min_chars / 30))
    sentence_max = max(9, min(16, math.ceil(max_chars / 18)))
    prompt = f"""
你是短视频总导演和事实审核编辑。DeepSeek 只负责候选草稿，你必须重新定稿。

要求：
1. 只输出 JSON，不要 markdown。
2. approved_script 必须是最终中文旁白正文，严格控制在 {min_chars}-{max_chars} 个中文汉字。
3. 保留强钩子、明确叙事线和可配画面信息；删除泛词、虚假确定性、无法验证的夸张细节。
4. 如果候选稿事实不可靠，改写成更稳健的表达，不要编造具体机构、数字、地点。
5. 每句不超过 30 个中文汉字；总句数必须是 {sentence_min}-{sentence_max} 句，不能多也不能少。
6. 输出结构：
{{
  "status": "complete",
  "selected_source_id": "v1",
  "angle": "定稿角度",
  "approved_script": "最终旁白正文",
  "review_notes": ["修改理由"]
}}

brief:
{json.dumps(brief, ensure_ascii=False, indent=2)}

DeepSeek candidates:
{json.dumps(candidates_payload, ensure_ascii=False, indent=2)}

source_notes:
{source_notes}
""".strip()
    prompt_path = run_dir / "reports" / "codex_script_director_prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    return prompt


def run_codex_script_director(
    run_dir: Path,
    model: str | None = None,
    timeout: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    brief = read_json(run_dir / "brief.json", {})
    char_range = brief.get("estimated_script_chars", {}) if isinstance(brief, dict) else {}
    min_chars = int(char_range.get("min", 160) or 160)
    max_chars = int(char_range.get("max", 210) or 210)
    prompt = build_codex_script_director_prompt(run_dir)
    prompt_path = run_dir / "reports" / "codex_script_director_prompt.md"
    output_file = run_dir / "reports" / "codex_script_director_raw.txt"
    if dry_run:
        report = {
            "status": "dry_run",
            "source": "codex",
            "prompt_path": str(prompt_path),
            "decision": "not_submitted",
        }
        write_json(run_dir / "approved_script_report.json", report)
        return report

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
        report = {
            "status": "failed",
            "source": "codex",
            "error": error,
            "prompt_path": str(prompt_path),
        }
        write_json(run_dir / "approved_script_report.json", report)
        raise RuntimeError(error)

    raw = output_file.read_text(encoding="utf-8") if output_file.exists() else result.stdout
    payload = parse_json_response_text(raw)
    script = normalize_script_text(str(payload.get("approved_script") or "").strip())
    if not script:
        report = {
            "status": "failed",
            "source": "codex",
            "error": "Codex output did not include approved_script",
            "prompt_path": str(prompt_path),
        }
        write_json(run_dir / "approved_script_report.json", report)
        raise ValueError("Codex output did not include approved_script")

    audit = audit_script(script, min_chars, max_chars)
    report = {
        "status": "complete" if audit["pass"] else "failed",
        "selected_id": str(payload.get("selected_source_id") or ""),
        "selected_angle": str(payload.get("angle") or ""),
        "source": "codex_script_director",
        "prompt_path": str(prompt_path),
        "raw_output_path": str(output_file),
        "chars": audit["chars"],
        "audit": audit,
        "review_notes": payload.get("review_notes", []),
    }
    write_json(run_dir / "approved_script_report.json", report)
    if not audit["pass"]:
        raise ValueError(
            "Codex approved script failed audit: "
            f"chars={audit['chars']}, expected={min_chars}-{max_chars}, checks={audit['checks']}"
        )

    (run_dir / "approved_script.md").write_text(f"# Approved Script\n\n{script}\n", encoding="utf-8")
    return report


def promote_recommended_script(
    run_dir: Path,
    candidate_id: str | None = None,
    allow_out_of_range: bool = False,
) -> dict[str, Any]:
    candidates_payload = read_json(run_dir / "script_candidates.json", {})
    audit_payload = read_json(run_dir / "script_audit.json", {})
    brief = read_json(run_dir / "brief.json", {})
    char_range = brief.get("estimated_script_chars", {}) if isinstance(brief, dict) else {}
    min_chars = int(char_range.get("min", 0) or 0)
    max_chars = int(char_range.get("max", 0) or 0)
    enforce_range = bool(char_range and min_chars and max_chars and not allow_out_of_range)
    candidates = [item for item in candidates_payload.get("candidates", []) or [] if isinstance(item, dict)]
    if not candidates:
        raise ValueError("script_candidates.json has no candidates")

    selected_id = candidate_id or str(audit_payload.get("recommended_id") or "")
    selected = next((item for item in candidates if str(item.get("id") or "") == selected_id), None)
    if selected is None:
        passing_id = next(
            (
                str(item.get("id") or "")
                for item in audit_payload.get("candidates", []) or []
                if isinstance(item, dict) and (item.get("audit") or {}).get("pass")
            ),
            "",
        )
        if passing_id:
            selected = next((item for item in candidates if str(item.get("id") or "") == passing_id), None)
    if selected is None and not enforce_range:
        selected = next(
            (
                item
                for item in audit_payload.get("candidates", []) or []
                if isinstance(item, dict) and (item.get("audit") or {}).get("pass")
            ),
            None,
        )
    if selected is None:
        if enforce_range:
            raise ValueError(
                f"No script candidate passed audit; expected {min_chars}-{max_chars} Chinese chars. "
                "Regenerate drafts or manually write approved_script.md."
            )
        selected = candidates[0]

    script = str(selected.get("script") or "").strip()
    if not script:
        raise ValueError("selected candidate has empty script")
    selected_audit = audit_script(script, min_chars, max_chars) if enforce_range else None
    if selected_audit and not selected_audit["pass"]:
        raise ValueError(
            "selected script failed audit: "
            f"chars={selected_audit['chars']}, expected={min_chars}-{max_chars}, "
            f"checks={selected_audit['checks']}"
        )

    (run_dir / "approved_script.md").write_text(f"# Approved Script\n\n{script}\n", encoding="utf-8")
    report = {
        "status": "complete",
        "selected_id": str(selected.get("id") or ""),
        "selected_angle": selected.get("angle", ""),
        "source": "script_candidates.json",
        "chars": count_cjk_chars(script),
    }
    write_json(run_dir / "approved_script_report.json", report)
    return report
