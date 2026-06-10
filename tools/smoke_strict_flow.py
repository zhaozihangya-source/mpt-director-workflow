#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.asset_tools import build_asset_binding_template
from director_workflow.config import load_runtime_config
from director_workflow.image_tools import build_codex_image_tasks, register_codex_image
from director_workflow.io_utils import write_json
from director_workflow.runtime import build_imagegen_handoff, create_run


def run_smoke(keep: bool = False) -> dict[str, object]:
    temp_path = Path(tempfile.mkdtemp(prefix="mpt-director-smoke-"))
    runs_dir = temp_path / "runs"
    mpt_dir = temp_path / "MoneyPrinterTurbo"
    mpt_dir.mkdir(parents=True, exist_ok=True)

    prior_env = {name: os.environ.get(name) for name in ("DIRECTOR_RUNS_DIR", "MPT_DIR", "MPT_API_ENDPOINT")}
    os.environ["DIRECTOR_RUNS_DIR"] = str(runs_dir)
    os.environ["MPT_DIR"] = str(mpt_dir)
    os.environ["MPT_API_ENDPOINT"] = "http://127.0.0.1:8080/api/v1/videos"

    try:
        config = load_runtime_config()
        run = create_run("strict flow smoke", 45, "douyin", "news explainer", config=config)
        run_dir = Path(str(run["path"]))
        write_json(
            run_dir / "shot_plan.json",
            {
                "status": "complete",
                "shots": [
                    {
                        "id": "s01",
                        "narration": "第一句用生成图承接主题。",
                        "start": 0,
                        "end": 5,
                        "duration": 5,
                        "material_type": "codex_image",
                        "visual_intent": "vertical editorial key image",
                        "search_terms": [],
                        "codex_image_prompt": "Vertical 9:16 editorial key image, no text, no logo",
                    }
                ],
            },
        )

        image_tasks = build_codex_image_tasks(run_dir)
        pending_handoff = build_imagegen_handoff(run_dir)
        suggested = Path(str(image_tasks["tasks"][0]["suggested_output"]))
        suggested.parent.mkdir(parents=True, exist_ok=True)
        suggested.write_bytes(b"fake-png-smoke")
        register_result = register_codex_image(run_dir, "s01", suggested, fit_score=86, approved=True)
        complete_handoff = build_imagegen_handoff(run_dir)
        binding_bundle = build_asset_binding_template(run_dir)

        result = {
            "status": "pass",
            "run_dir": str(run_dir),
            "pending_handoff_status": pending_handoff.get("status"),
            "pending_count_before_register": pending_handoff.get("pending_count"),
            "complete_handoff_status": complete_handoff.get("status"),
            "pending_count_after_register": complete_handoff.get("pending_count"),
            "asset_binding_decision": binding_bundle["asset_binding_report"].get("decision"),
            "registered_output": register_result.get("output_path"),
        }
        if keep:
            result["kept_temp_dir"] = str(temp_path)
        return result
    finally:
        for name, value in prior_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if not keep:
            shutil.rmtree(temp_path, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test the strict Codex image handoff flow without external APIs.")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary run directory for inspection.")
    args = parser.parse_args()

    result = run_smoke(keep=args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
