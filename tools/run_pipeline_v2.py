#!/usr/bin/env python3
"""Run pipeline v2: 4 阶段状态机（script → materials → render → acceptance）。

重入安全：已完成阶段自动跳过；waiting_for_imagegen 状态下生图登记完后重跑即续。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.pipeline_v2 import run_pipeline_v2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the v2 staged pipeline for a run dir.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--provider", default="auto", choices=["auto", "api", "cli"])
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--codex-model", default="")
    parser.add_argument(
        "--materials-mode",
        default="codex",
        choices=["codex", "stock_only"],
        help="codex: 导演决定 stock/生图混排（有生图断点）; stock_only: 全 Pexels 真实视频（全自动无断点）",
    )
    parser.add_argument("--timeout-seconds", type=int, default=1200, help="MPT render wait")
    parser.add_argument("--show-logs", action="store_true")
    args = parser.parse_args()

    result = run_pipeline_v2(
        args.run_dir.resolve(),
        options={
            "provider": args.provider,
            "count": args.count,
            "codex_model": args.codex_model,
            "materials_mode": args.materials_mode,
            "timeout_seconds": args.timeout_seconds,
        },
    )

    if args.show_logs:
        for line in result["logs"]:
            print(line)
        print("---")
    print(f"status={result['status']} stage={result['stage']}")
    print(f"detail={result['detail']}")
    print(json.dumps(result["stages"], ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] in {"complete", "waiting_for_imagegen"} else 1)


if __name__ == "__main__":
    main()
