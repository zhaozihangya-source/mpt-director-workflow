#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.material_strategy import (  # noqa: E402
    build_rule_based_material_strategy,
    build_stock_only_material_strategy,
    run_codex_material_strategy,
    summarize_material_mix,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build shot_plan.json with stock_video / codex_image material strategy.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--mode", choices=["codex", "rules", "stock-only"], default="codex")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "codex":
        plan = run_codex_material_strategy(args.run_dir, model=args.model, timeout=args.timeout, dry_run=args.dry_run)
    elif args.mode == "rules":
        if args.dry_run:
            raise ValueError("--dry-run is only supported with --mode codex")
        plan = build_rule_based_material_strategy(args.run_dir)
    else:
        if args.dry_run:
            raise ValueError("--dry-run is only supported with --mode codex")
        plan = build_stock_only_material_strategy(args.run_dir)

    if args.dry_run:
        print(f"status={plan['status']} prompt={plan['prompt_path']}")
        return

    mix = summarize_material_mix(plan)
    print(f"status={plan.get('status')} shots={len(plan.get('shots', []))} mix={mix}")


if __name__ == "__main__":
    main()
