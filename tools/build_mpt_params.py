#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.mpt_tools import build_mpt_params


def main() -> None:
    parser = argparse.ArgumentParser(description="Build mpt_params.json from the approved director plan.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--allow-draft-pexels",
        action="store_true",
        help="Allow fallback to Pexels search when asset bindings are not approved. Use only for drafts.",
    )
    args = parser.parse_args()

    payload = build_mpt_params(args.run_dir, allow_draft_pexels=args.allow_draft_pexels)
    params = payload["params"]
    print(f"ready: {payload['endpoint']}")
    print(f"subject={params['video_subject']}")
    print(f"terms={params['video_terms']}")


if __name__ == "__main__":
    main()
