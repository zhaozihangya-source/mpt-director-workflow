from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_workflow.config import _optional_path, api_setup_report, load_dotenv, load_runtime_config, write_local_env
from director_workflow.endpoint_tools import DEFAULT_MPT_ENDPOINT
from director_workflow.io_utils import WORKFLOW_DIR


class ConfigTest(unittest.TestCase):
    def test_load_dotenv_parses_simple_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "DEEPSEEK_API_KEY='secret-value'",
                        'PEXELS_API_KEY="pexels-value"',
                        "EMPTY=",
                    ]
                ),
                encoding="utf-8",
            )

            values = load_dotenv(env_path)

            self.assertEqual(values["DEEPSEEK_API_KEY"], "secret-value")
            self.assertEqual(values["PEXELS_API_KEY"], "pexels-value")
            self.assertEqual(values["EMPTY"], "")

    def test_runtime_config_public_report_never_contains_secret_value(self) -> None:
        with (
            tempfile.TemporaryDirectory() as runs_tmp,
            tempfile.TemporaryDirectory() as mpt_tmp,
            patch.dict(
                os.environ,
                {
                    "MPT_API_ENDPOINT": DEFAULT_MPT_ENDPOINT,
                    "DIRECTOR_RUNS_DIR": runs_tmp,
                    "MPT_DIR": mpt_tmp,
                    "DEEPSEEK_API_KEY": "super-secret-key",
                },
                clear=False,
            ),
            patch("director_workflow.config._read_deepseek_key_from_keychain", return_value=""),
        ):
            config = load_runtime_config()
            report = api_setup_report(config)

        self.assertTrue(report["config"]["apis"]["deepseek_api"])
        self.assertNotIn("super-secret-key", str(report))
        self.assertTrue(report["security"]["secrets_redacted"])

    def test_runtime_config_detects_stock_api_keys_from_mpt_config(self) -> None:
        with tempfile.TemporaryDirectory() as runs_tmp, tempfile.TemporaryDirectory() as mpt_tmp:
            mpt_config = Path(mpt_tmp) / "config.toml"
            mpt_config.write_text('[app]\npexels_api_keys=["pexels-secret"]\n', encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {
                        "MPT_API_ENDPOINT": DEFAULT_MPT_ENDPOINT,
                        "DIRECTOR_RUNS_DIR": runs_tmp,
                        "MPT_DIR": mpt_tmp,
                    },
                    clear=False,
                ),
                patch("director_workflow.config._read_deepseek_key_from_keychain", return_value=""),
            ):
                report = api_setup_report(load_runtime_config())

        self.assertTrue(report["config"]["apis"]["pexels"])
        self.assertNotIn("pexels-secret", str(report))

    def test_optional_relative_path_resolves_from_workflow_dir(self) -> None:
        path = _optional_path("../director-runs", base_dir=WORKFLOW_DIR)

        self.assertEqual(path, (WORKFLOW_DIR / "../director-runs").resolve())

    def test_write_local_env_preserves_existing_values_when_update_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text('DEEPSEEK_API_KEY="old-secret"\nCODEX_EXECUTABLE="codex"\n', encoding="utf-8")

            result = write_local_env(
                {
                    "DEEPSEEK_API_KEY": "",
                    "PEXELS_API_KEY": "pexels-secret",
                    "CODEX_EXECUTABLE": "codex-custom",
                    "UNSUPPORTED": "ignored",
                },
                workflow_dir=Path(tmp),
            )
            values = load_dotenv(env_path)

        self.assertEqual(values["DEEPSEEK_API_KEY"], "old-secret")
        self.assertEqual(values["PEXELS_API_KEY"], "pexels-secret")
        self.assertEqual(values["CODEX_EXECUTABLE"], "codex-custom")
        self.assertNotIn("UNSUPPORTED", values)
        self.assertIn("PEXELS_API_KEY", result["secret_keys_changed"])
        self.assertNotIn("pexels-secret", str(result))


if __name__ == "__main__":
    unittest.main()
