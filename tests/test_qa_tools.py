from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.io_utils import read_json, write_json
from director_workflow.qa_tools import qa_video


class QaToolsTest(unittest.TestCase):
    def test_missing_video_fails_closed_with_error_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "brief.json", {"target_duration_seconds": 45})

            report = qa_video(run_dir, run_dir / "missing.mp4")

            self.assertEqual(report["decision"], "needs_revision")
            self.assertFalse(report["checks"]["file_exists"])
            self.assertIn("video_missing", report["errors"])
            saved = read_json(run_dir / "qa_report.json")
            self.assertEqual(saved["decision"], "needs_revision")


if __name__ == "__main__":
    unittest.main()
