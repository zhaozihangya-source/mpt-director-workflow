from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT_DIR / "director-runs"
MPT_DIR = ROOT_DIR / "MoneyPrinterTurbo"


def slugify(value: str, fallback: str = "video") -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "", value)
    value = value.strip("-_")
    return value[:64] or fallback


def dated_run_dir(topic: str, runs_dir: Path = RUNS_DIR) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    return runs_dir / f"{stamp}-{slugify(topic)}"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def count_cjk_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def normalize_script_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def split_cn_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [part.strip() for part in parts if part.strip()]


def target_char_range(duration_seconds: int, chars_per_second: float = 4.38) -> tuple[int, int]:
    target = duration_seconds * chars_per_second
    return round(target * 0.9), round(target * 1.08)
