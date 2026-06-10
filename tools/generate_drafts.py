#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import read_json
from director_workflow.script_tools import (
    audit_candidates,
    build_deepseek_prompt,
    call_deepseek,
    save_generated_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate script candidates with DeepSeek.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--provider", choices=["auto", "api", "cli"], default="auto")
    parser.add_argument("--dry-run", action="store_true", help="Print the prompt without calling DeepSeek")
    args = parser.parse_args()

    brief = read_json(args.run_dir / "brief.json", {})
    prompt = build_deepseek_prompt(brief, count=args.count)
    if args.dry_run:
        print(prompt)
        return
    payload = call_deepseek(prompt, model=args.model, provider=args.provider)
    save_generated_candidates(args.run_dir, payload, model=args.model)
    report = audit_candidates(args.run_dir)
    print(f"saved script_candidates.json and script_audit.json; recommended={report.get('recommended_id')}")


if __name__ == "__main__":
    main()
