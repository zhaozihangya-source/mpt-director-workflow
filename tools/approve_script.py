#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.script_tools import promote_recommended_script


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote a generated script candidate into approved_script.md.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-id")
    parser.add_argument("--allow-out-of-range", action="store_true", help="Allow promoting a candidate that fails the brief char range.")
    args = parser.parse_args()

    report = promote_recommended_script(
        args.run_dir,
        candidate_id=args.candidate_id,
        allow_out_of_range=args.allow_out_of_range,
    )
    print(f"approved_script selected_id={report['selected_id']} chars={report['chars']}")


if __name__ == "__main__":
    main()
