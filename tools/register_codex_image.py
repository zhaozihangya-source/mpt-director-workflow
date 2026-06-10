#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.image_tools import register_codex_image


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a generated Codex image into a run and register it as a shot asset.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("shot_id")
    parser.add_argument("image_path", type=Path)
    parser.add_argument("--fit-score", type=int, default=80)
    parser.add_argument("--commercial-use-status", default="generated_owned")
    parser.add_argument("--approve", action="store_true", help="Mark the generated image approved after visual review.")
    args = parser.parse_args()

    result = register_codex_image(
        args.run_dir,
        args.shot_id,
        args.image_path,
        fit_score=args.fit_score,
        commercial_use_status=args.commercial_use_status,
        approved=args.approve,
    )
    report = result["asset_binding_report"]
    print(f"registered={result['output_path']}")
    print(f"asset_binding_decision={report['decision']} issues={len(report['issues'])}")


if __name__ == "__main__":
    main()
