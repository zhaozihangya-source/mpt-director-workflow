#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.mpt_tools import create_contact_sheet
from director_workflow.qa_tools import qa_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Run technical QA on a rendered video.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("video_path", type=Path)
    parser.add_argument("--contact-sheet", action="store_true")
    args = parser.parse_args()

    report = qa_video(args.run_dir, args.video_path)
    if args.contact_sheet:
        sheet = args.run_dir / "reports" / "contact_sheet.jpg"
        create_contact_sheet(args.video_path, sheet)
        print(f"contact_sheet={sheet}")
    print(f"decision={report['decision']} duration={report['duration_seconds']}s")


if __name__ == "__main__":
    main()
