#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.script_tools import run_codex_script_director


def main() -> None:
    parser = argparse.ArgumentParser(description="Use Codex CLI to produce the final approved script from DeepSeek candidates.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = run_codex_script_director(
        args.run_dir,
        model=args.model,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    print(f"codex_script status={report['status']} chars={report.get('chars', '')}")
    print(args.run_dir / "approved_script.md")


if __name__ == "__main__":
    main()
