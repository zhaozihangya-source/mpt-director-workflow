from __future__ import annotations

import sys
import tempfile
import unittest
import urllib.error
import ssl
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.api_material_tools import (
    _http_json,
    candidate_issues,
    download_candidate,
    provider_order,
    score_api_candidate,
    select_api_assets,
)
from director_workflow.io_utils import read_json, write_json


class ApiMaterialToolsTest(unittest.TestCase):
    def test_provider_order_prefers_pexels_and_requires_explicit_pixabay_fallback(self) -> None:
        self.assertEqual(provider_order(), ["pexels"])
        self.assertEqual(provider_order("pexels", allow_pixabay_fallback=True), ["pexels", "pixabay"])

    def test_pexels_portrait_candidate_scores_as_passing(self) -> None:
        candidate = {
            "provider": "pexels",
            "width": 1080,
            "height": 1920,
            "duration_seconds": 10,
            "source_url": "https://www.pexels.com/video/1/",
            "license_url": "https://www.pexels.com/license/",
        }

        self.assertGreaterEqual(score_api_candidate(candidate), 75)
        self.assertEqual(candidate_issues(candidate), [])

    def test_http_json_retries_transient_ssl_error(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        calls = []

        def fake_urlopen(request: object, timeout: int = 30) -> FakeResponse:
            calls.append((request, timeout))
            if len(calls) == 1:
                raise urllib.error.URLError(ssl.SSLEOFError("unexpected eof"))
            return FakeResponse()

        with (
            patch("director_workflow.api_material_tools.urllib.request.urlopen", fake_urlopen),
            patch("director_workflow.api_material_tools.time.sleep") as sleep,
        ):
            self.assertEqual(_http_json("https://example.test/api", timeout=5), {"ok": True})

        self.assertEqual(len(calls), 2)
        sleep.assert_called_once()

    def test_download_candidate_retries_and_uses_temp_file(self) -> None:
        class FakeResponse:
            def __init__(self, chunks: list[bytes], fail_after_first_chunk: bool = False) -> None:
                self.chunks = chunks
                self.fail_after_first_chunk = fail_after_first_chunk
                self.read_count = 0

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                self.read_count += 1
                if self.fail_after_first_chunk and self.read_count == 2:
                    raise urllib.error.URLError(ssl.SSLEOFError("unexpected eof"))
                if self.chunks:
                    return self.chunks.pop(0)
                return b""

        calls = []

        def fake_urlopen(request: object, timeout: int = 180) -> FakeResponse:
            calls.append((request, timeout))
            if len(calls) == 1:
                return FakeResponse([b"partial"], fail_after_first_chunk=True)
            return FakeResponse([b"complete-video"])

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            candidate = {
                "provider": "pexels",
                "provider_asset_id": "1",
                "source_url": "https://www.pexels.com/video/1/",
                "license_url": "https://www.pexels.com/license/",
                "download_url": "https://videos.pexels.com/video-files/1.mp4",
                "width": 1080,
                "height": 1920,
                "duration_seconds": 10,
                "orientation": "portrait",
            }
            with (
                patch("director_workflow.api_material_tools.urllib.request.urlopen", fake_urlopen),
                patch("director_workflow.api_material_tools.media_info", return_value={"issues": []}),
                patch("director_workflow.api_material_tools.time.sleep") as sleep,
            ):
                result = download_candidate(run_dir, "s01", candidate)

            output_path = run_dir / result["local_path"]
            self.assertEqual(output_path.read_bytes(), b"complete-video")
            self.assertFalse(output_path.with_suffix(output_path.suffix + ".part").exists())
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once()

    def test_select_api_assets_writes_pexels_binding_and_passes_when_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            asset_path = run_dir / "assets" / "api_videos" / "s01" / "s01-pexels-1.mp4"
            asset_path.parent.mkdir(parents=True)
            asset_path.write_bytes(b"fake-video")
            write_json(
                run_dir / "shot_plan.json",
                {
                    "shots": [
                        {
                            "id": "s01",
                            "narration": "第一句。",
                            "duration_hint_seconds": 5,
                            "material_type": "stock_video",
                            "visual_intent": "真实办公室",
                            "search_terms": ["vertical business office"],
                        }
                    ]
                },
            )
            write_json(
                run_dir / "api_asset_candidates.json",
                {
                    "shots": [
                        {
                            "shot_id": "s01",
                            "candidates": [
                                {
                                    "provider": "pexels",
                                    "provider_asset_id": "1",
                                    "source_url": "https://www.pexels.com/video/1/",
                                    "license_url": "https://www.pexels.com/license/",
                                    "local_path": "assets/api_videos/s01/s01-pexels-1.mp4",
                                    "fit_score": 98,
                                    "issues": [],
                                }
                            ],
                        }
                    ]
                },
            )
            write_json(run_dir / "asset_manifest.json", {"assets": [{"path": "assets/api_videos/s01/s01-pexels-1.mp4", "issues": []}]})
            write_json(
                run_dir / "asset_bindings.json",
                {
                    "bindings": [
                        {
                            "shot_id": "s99",
                            "material_type": "stock_video",
                            "asset_path": "assets/api_videos/s99/stale.mp4",
                            "source_provider": "pexels",
                            "license_status": "verified",
                            "commercial_use_status": "platform_license_verified",
                            "fit_score": 100,
                            "approved": True,
                        }
                    ]
                },
            )

            result = select_api_assets(run_dir, approve_passing=True)
            bindings = read_json(run_dir / "asset_bindings.json")
            selection_report = read_json(run_dir / "api_asset_selection_report.json")

            self.assertEqual(result["asset_binding_report"]["decision"], "pass")
            self.assertEqual(selection_report["status"], "complete")
            self.assertEqual([binding["shot_id"] for binding in bindings["bindings"]], ["s01"])
            self.assertEqual(bindings["bindings"][0]["source_provider"], "pexels")
            self.assertEqual(bindings["bindings"][0]["commercial_use_status"], "platform_license_verified")


if __name__ == "__main__":
    unittest.main()
