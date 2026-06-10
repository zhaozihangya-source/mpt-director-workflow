#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.scaffold import scaffold_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new director workflow run.")
    parser.add_argument("topic", help="Video topic")
    parser.add_argument("--duration", type=int, default=60, help="Target duration in seconds")
    parser.add_argument("--platform", default="douyin", help="Target platform")
    parser.add_argument("--style", default="商业科普，具体、有钩子、适合竖屏短视频")
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()

    run_dir = scaffold_run(
        topic=args.topic,
        duration_seconds=args.duration,
        platform=args.platform,
        style=args.style,
        run_dir=args.run_dir,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
