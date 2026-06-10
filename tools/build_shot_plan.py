#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.mpt_tools import build_shot_plan_from_script


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft shot_plan.json from approved_script.md.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    plan = build_shot_plan_from_script(args.run_dir)
    print(f"shots={len(plan.get('shots', []))}")


if __name__ == "__main__":
    main()
