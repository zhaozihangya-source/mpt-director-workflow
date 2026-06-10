#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.semantic_tools import build_revision_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Build revision_plan.json from technical and semantic reviews.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    plan = build_revision_plan(args.run_dir)
    print(f"decision={plan['decision']} actions={len(plan['actions'])}")


if __name__ == "__main__":
    main()
