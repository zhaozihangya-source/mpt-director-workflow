#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.semantic_tools import build_semantic_review_prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Codex semantic-review prompt for a director run.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    build_semantic_review_prompt(args.run_dir)
    print(args.run_dir / "reports" / "semantic_review_prompt.md")


if __name__ == "__main__":
    main()
