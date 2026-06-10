#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.asset_tools import build_asset_binding_template


def main() -> None:
    parser = argparse.ArgumentParser(description="Build asset_bindings.json and asset_binding_report.json.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    result = build_asset_binding_template(args.run_dir)
    report = result["asset_binding_report"]
    print(f"decision={report['decision']} issues={len(report['issues'])}")


if __name__ == "__main__":
    main()
