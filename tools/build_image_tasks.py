#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.image_tools import build_codex_image_tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Codex image generation tasks from shot_plan.json.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    payload = build_codex_image_tasks(args.run_dir)
    print(f"status={payload['status']} tasks={len(payload['tasks'])}")
    for task in payload["tasks"]:
        print(f"{task['shot_id']}: {task['prompt_file']}")


if __name__ == "__main__":
    main()
