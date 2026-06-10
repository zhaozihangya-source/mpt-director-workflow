#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.script_tools import audit_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit generated script candidates.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    report = audit_candidates(args.run_dir)
    print(f"recommended={report.get('recommended_id')}")
    for item in report.get("candidates", []):
        audit = item.get("audit", {})
        print(f"{item.get('id')}: pass={audit.get('pass')} chars={audit.get('chars')}")


if __name__ == "__main__":
    main()
