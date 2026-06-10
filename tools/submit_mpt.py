#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.mpt_tools import post_mpt_task, wait_mpt_task


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit mpt_params.json to the local MPT API.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--wait", action="store_true", help="Poll the task until completion, failure, timeout, or stall.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--stall-seconds", type=int, default=240)
    args = parser.parse_args()

    result = post_mpt_task(args.run_dir)
    print(result)
    if args.wait:
        task_id = str((result.get("data") or {}).get("task_id") or "")
        report = wait_mpt_task(
            args.run_dir,
            task_id=task_id,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            stall_seconds=args.stall_seconds,
        )
        print(f"wait_status={report['status']} task_id={report['task_id']}")


if __name__ == "__main__":
    main()
