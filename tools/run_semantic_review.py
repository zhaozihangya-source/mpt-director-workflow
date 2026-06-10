#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.semantic_tools import run_codex_semantic_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Codex semantic review for a director run.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--dry-run", action="store_true", help="Only build the prompt; do not call Codex CLI")
    args = parser.parse_args()

    result = run_codex_semantic_review(args.run_dir, model=args.model, dry_run=args.dry_run)
    validation = result.get("validation", {}) if isinstance(result, dict) else {}
    print(f"status={result.get('status')} decision={result.get('decision')}")
    if validation:
        print(f"validation_valid={validation.get('valid')} issues={len(validation.get('issues', []))}")
    print(args.run_dir / "semantic_review.json")


if __name__ == "__main__":
    main()
