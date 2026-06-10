#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.material_tools import inventory_assets


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or refresh asset_manifest.json.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("paths", nargs="+", type=Path, help="Media files or directories")
    parser.add_argument("--aspect", default="9:16")
    args = parser.parse_args()

    manifest = inventory_assets(args.run_dir, args.paths, target_aspect=args.aspect)
    print(f"assets={len(manifest['assets'])}")
    for item in manifest["assets"][:10]:
        print(f"{item['score']:3} {item['orientation']:9} {item['name']}")


if __name__ == "__main__":
    main()
