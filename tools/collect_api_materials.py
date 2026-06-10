#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.api_material_tools import collect_api_assets, select_api_assets  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect per-shot API video assets, preferring Pexels.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--provider", choices=["pexels", "pixabay"], default="pexels")
    parser.add_argument("--allow-pixabay-fallback", action="store_true")
    parser.add_argument("--per-page", type=int, default=15)
    parser.add_argument("--max-candidates-per-shot", type=int, default=2)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--bind", action="store_true", help="Bind selected downloaded API materials into asset_bindings.json.")
    parser.add_argument("--approve-passing", action="store_true", help="Mark passing selected API assets approved.")
    parser.add_argument("--min-fit-score", type=int, default=75)
    args = parser.parse_args()

    payload = collect_api_assets(
        args.run_dir,
        primary_provider=args.provider,
        allow_pixabay_fallback=args.allow_pixabay_fallback,
        per_page=args.per_page,
        max_candidates_per_shot=args.max_candidates_per_shot,
        download=not args.no_download,
    )
    stock_shots = len(payload.get("shots", []))
    candidate_count = sum(len(shot.get("candidates", [])) for shot in payload.get("shots", []))
    print(f"api_assets status={payload['status']} provider={payload['primary_provider']} stock_shots={stock_shots} candidates={candidate_count}")

    if args.bind:
        result = select_api_assets(args.run_dir, min_fit_score=args.min_fit_score, approve_passing=args.approve_passing)
        report = result["asset_binding_report"]
        print(f"asset_bindings decision={report['decision']} issues={len(report['issues'])}")


if __name__ == "__main__":
    main()
