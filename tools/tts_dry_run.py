#!/usr/bin/env python3
"""TTS dry-run: synthesize approved_script.md with edge-tts to measure actual duration.

不生成音频文件，只通过 word boundary 事件统计合成时长，用于渲染前校验脚本时长。
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import count_cjk_chars, read_json, split_cn_sentences, write_json

# 不指定 edge: 前缀时的兜底声音，接近新闻播报风格
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DURATION_TOLERANCE = 0.15  # ±15%，与 qa_render 的 0.12 宽松一档，留给视频片头片尾


def strip_markdown(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(stripped)
    return "\n".join(line for line in lines if line).strip()


async def _synthesize_bytes(text: str, voice: str) -> bytes:
    """将文本合成为 MP3 字节，不写磁盘。"""
    try:
        import edge_tts
    except ImportError:
        raise ImportError("缺少 edge-tts：pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def _ffprobe_duration(audio_bytes: bytes) -> float:
    """用 ffprobe 测量 MP3 时长（秒），写临时文件后读取。"""
    import json as _json
    import os as _os
    import subprocess as _sp
    import tempfile as _tf

    with _tf.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        result = _sp.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-i", tmp_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe 失败：{result.stderr.decode(errors='replace')[:200]}")
        data = _json.loads(result.stdout)
        return float(data["format"]["duration"])
    finally:
        _os.unlink(tmp_path)


def measure_duration(text: str, voice: str) -> float:
    audio = asyncio.run(_synthesize_bytes(text, voice))
    if not audio:
        raise RuntimeError("edge-tts 返回空音频")
    return _ffprobe_duration(audio)


def run_dry_run(run_dir: Path) -> dict:
    brief = read_json(run_dir / "brief.json", {})
    script_path = run_dir / "approved_script.md"

    if not script_path.exists():
        return {
            "status": "error",
            "decision": "error",
            "error": "approved_script.md 不存在，请先定稿",
        }

    raw_text = script_path.read_text(encoding="utf-8")
    text = strip_markdown(raw_text)
    if not text:
        return {
            "status": "error",
            "decision": "error",
            "error": "approved_script.md 去除 Markdown 后内容为空",
        }

    # 确定 edge-tts 声音：brief 里有 edge: 前缀就直接用，否则用兜底声音
    brief_voice = str((brief.get("tts") or {}).get("voice_name") or "")
    if brief_voice.lower().startswith("edge:"):
        voice = brief_voice[5:].strip()
        is_estimate = False
    else:
        voice = EDGE_DEFAULT_VOICE
        is_estimate = True

    target_seconds = float(brief.get("target_duration_seconds") or 0)
    char_count = count_cjk_chars(text)
    total_chars = len(text)

    try:
        actual_seconds = measure_duration(text, voice)
    except ImportError as exc:
        # edge-tts 未安装：跳过，不阻断流水线
        return {"status": "skipped", "decision": "skipped", "skip_reason": str(exc)}
    except Exception as exc:
        # 网络不通等基础设施问题：跳过，不阻断流水线
        return {"status": "skipped", "decision": "skipped", "skip_reason": f"TTS 合成失败：{exc}"}

    if actual_seconds <= 0:
        return {"status": "skipped", "decision": "skipped", "skip_reason": "TTS 返回零时长"}

    chars_per_sec = round(cjk_cps := char_count / actual_seconds, 2)
    ratio = round(actual_seconds / target_seconds, 3) if target_seconds else None
    ok = ratio is not None and abs(ratio - 1.0) <= DURATION_TOLERANCE

    if ratio is None:
        recommendation = "brief.json 里没有 target_duration_seconds，无法评估"
    elif ratio > 1 + DURATION_TOLERANCE:
        excess = actual_seconds - target_seconds
        cut = int(excess * cjk_cps)
        recommendation = f"脚本偏长 {excess:.1f}秒（{ratio:.0%}），建议删减约 {cut} 个汉字"
    elif ratio < 1 - DURATION_TOLERANCE:
        shortage = target_seconds - actual_seconds
        add = int(shortage * cjk_cps)
        recommendation = f"脚本偏短 {shortage:.1f}秒（{ratio:.0%}），建议补充约 {add} 个汉字"
    else:
        recommendation = f"时长合格（{ratio:.0%}，容差 ±{DURATION_TOLERANCE:.0%}），可进入下一步"

    # 句级时长：整篇实测总时长按每句 CJK 字符占比分配，零额外网络调用。
    # 单句独立合成的语调停顿与整篇不同，按比例分配反而更接近整篇真实节奏。
    sentences = split_cn_sentences(text)
    sentence_durations = []
    if sentences and char_count > 0:
        for sent in sentences:
            weight = count_cjk_chars(sent) / char_count
            sentence_durations.append(
                {"narration": sent, "estimated_seconds": round(actual_seconds * weight, 2)}
            )

    report = {
        "status": "complete",
        "decision": "pass" if ok else "needs_revision",
        "voice_used": voice,
        "voice_in_brief": brief_voice or "(未配置)",
        "is_estimate": is_estimate,
        "estimate_note": f"brief 声音非 edge: 前缀，用 {EDGE_DEFAULT_VOICE} 估算" if is_estimate else "",
        "char_count_cjk": char_count,
        "char_count_total": total_chars,
        "actual_duration_seconds": round(actual_seconds, 2),
        "target_duration_seconds": target_seconds,
        "chars_per_second_cjk": chars_per_sec,
        "duration_ratio": ratio,
        "duration_ok": ok,
        "tolerance": DURATION_TOLERANCE,
        "recommendation": recommendation,
        "sentence_durations": sentence_durations,
    }
    write_json(run_dir / "tts_dry_run_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TTS dry-run：合成 approved_script.md，测量实际时长，渲染前校验",
    )
    parser.add_argument("run_dir", type=Path, help="run 目录路径")
    args = parser.parse_args()

    report = run_dry_run(args.run_dir.resolve())
    decision = report.get("decision", "error")

    print(f"decision={decision}")
    if report.get("actual_duration_seconds"):
        print(
            f"duration={report['actual_duration_seconds']}s  "
            f"target={report['target_duration_seconds']}s  "
            f"ratio={report.get('duration_ratio')}  "
            f"cjk_cps={report.get('chars_per_second_cjk')}"
        )
    if report.get("is_estimate"):
        print(f"[estimate] {report.get('estimate_note')}")
    print(f"recommendation={report.get('recommendation')}")

    if report.get("skip_reason"):
        print(f"[skipped] {report['skip_reason']}")

    # exit 0: pass 或 skipped（基础设施不可用，不阻断流水线）
    # exit 1: 测量成功但时长不合格，阻断流水线
    sys.exit(1 if decision == "needs_revision" else 0)


if __name__ == "__main__":
    main()
