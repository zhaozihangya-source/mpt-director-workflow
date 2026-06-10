from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .endpoint_tools import DEFAULT_MPT_ENDPOINT, MPT_VIDEO_API_PATH, local_task_url as _local_task_url, validate_local_mpt_endpoint
from .io_utils import MPT_DIR, ROOT_DIR, RUNS_DIR, WORKFLOW_DIR, read_json


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
LOCAL_ENV_FILENAMES = (".env", ".env.local")  # .env.local 后加载，优先级更高
DEEPSEEK_KEYCHAIN_SERVICE = "codex.deepseek.api_key"
LOCAL_CONFIG_KEYS = {
    "MPT_API_ENDPOINT",
    "MPT_DIR",
    "DIRECTOR_RUNS_DIR",
    "DIRECTOR_DEFAULT_DURATION_SECONDS",
    "DIRECTOR_DEFAULT_PLATFORM",
    "DIRECTOR_DEFAULT_VOICE_NAME",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "CODEX_EXECUTABLE",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "SOCIAL_UPLOAD_DIR",
}
SECRET_CONFIG_KEYS = {"DEEPSEEK_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"}


@dataclass(frozen=True)
class RuntimeConfig:
    root_dir: Path
    workflow_dir: Path
    runs_dir: Path
    mpt_dir: Path
    mpt_api_endpoint: str
    default_duration_seconds: int
    default_platform: str
    default_voice_name: str
    deepseek_model: str
    deepseek_base_url: str
    codex_executable: str
    social_upload_dir: Path | None
    pexels_configured: bool
    pixabay_configured: bool
    deepseek_api_configured: bool
    deepseek_key_source: str
    deepseek_cli_available: bool
    codex_cli_available: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "root_dir": str(self.root_dir),
            "workflow_dir": str(self.workflow_dir),
            "runs_dir": str(self.runs_dir),
            "mpt_dir": str(self.mpt_dir),
            "mpt_api_endpoint": self.mpt_api_endpoint,
            "default_duration_seconds": self.default_duration_seconds,
            "default_platform": self.default_platform,
            "default_voice_name": self.default_voice_name,
            "deepseek_model": self.deepseek_model,
            "deepseek_base_url": self.deepseek_base_url,
            "codex_executable": self.codex_executable,
            "social_upload_dir": str(self.social_upload_dir) if self.social_upload_dir else "",
            "apis": {
                "pexels": self.pexels_configured,
                "pixabay": self.pixabay_configured,
                "deepseek_api": self.deepseek_api_configured,
                "deepseek_key_source": self.deepseek_key_source,
            },
            "tools": {
                "deepseek_cli": self.deepseek_cli_available,
                "codex_cli": self.codex_cli_available,
            },
        }


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            values[name] = value
    return values


def load_local_env(workflow_dir: Path = WORKFLOW_DIR) -> dict[str, str]:
    merged: dict[str, str] = {}
    for filename in LOCAL_ENV_FILENAMES:
        merged.update(load_dotenv(workflow_dir / filename))
    return merged


def write_local_env(updates: dict[str, str], workflow_dir: Path = WORKFLOW_DIR) -> dict[str, Any]:
    sanitized: dict[str, str] = {}
    for key, value in updates.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in LOCAL_CONFIG_KEYS:
            continue
        sanitized[normalized_key] = str(value or "").strip()

    path = workflow_dir / ".env.local"
    existing = load_dotenv(path)
    changed: list[str] = []
    for key, value in sanitized.items():
        if value == "":
            continue
        if existing.get(key) != value:
            existing[key] = value
            changed.append(key)

    lines = [
        "# Local config written by MPT Director WebUI.",
        "# Do not commit this file.",
        "",
    ]
    for key in sorted(existing):
        value = existing[key]
        safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{safe_value}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "status": "complete",
        "path": str(path),
        "changed": changed,
        "secret_keys_changed": [key for key in changed if key in SECRET_CONFIG_KEYS],
    }


def env_value(name: str, local_env: dict[str, str] | None = None, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    if local_env and name in local_env:
        return local_env[name]
    return default


def truthy_env(name: str, local_env: dict[str, str] | None = None) -> bool:
    return bool(env_value(name, local_env).strip())


def _read_deepseek_key_from_keychain() -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", DEEPSEEK_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def deepseek_api_key(local_env: dict[str, str] | None = None) -> tuple[str, str]:
    direct = env_value("DEEPSEEK_API_KEY", local_env).strip()
    if direct:
        return direct, "env"
    keychain = _read_deepseek_key_from_keychain()
    if keychain:
        return keychain, "keychain"
    return "", ""


def _int_config(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _optional_path(value: str, base_dir: Path = WORKFLOW_DIR) -> Path | None:
    value = value.strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _configured_stock_api(provider: str, local_env: dict[str, str], mpt_dir: Path) -> bool:
    env_names = {
        "pexels": ("PEXELS_API_KEY", "PEXELS_API_KEYS"),
        "pixabay": ("PIXABAY_API_KEY", "PIXABAY_API_KEYS"),
    }.get(provider, ())
    if any(truthy_env(name, local_env) for name in env_names):
        return True
    config_path = mpt_dir / "config.toml"
    if not config_path.exists():
        return False
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError:
        return False
    value = (data.get("app", {}) or {}).get(f"{provider}_api_keys", [])
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return False


def _mpt_api_reachability(endpoint: str, timeout: float = 0.5) -> tuple[bool, str]:
    try:
        endpoint = validate_local_mpt_endpoint(endpoint)
    except ValueError as exc:
        return False, str(exc)
    base_url = endpoint[: -len(MPT_VIDEO_API_PATH)]
    request = urllib.request.Request(f"{base_url}/docs", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.URLError as exc:
        return False, str(getattr(exc, "reason", exc))
    except OSError as exc:
        return False, str(exc)
    return 200 <= status < 500, "" if status < 500 else f"HTTP {status}"


def load_runtime_config(config_path: Path | None = None) -> RuntimeConfig:
    local_env = load_local_env()
    config_path = config_path or Path(env_value("DIRECTOR_CONFIG", local_env)).expanduser()
    file_config = read_json(config_path, {}) if str(config_path) not in {"", "."} and config_path.exists() else {}

    mpt_endpoint = env_value(
        "MPT_API_ENDPOINT",
        local_env,
        str(file_config.get("mpt_api_endpoint") or DEFAULT_MPT_ENDPOINT),
    ).strip()
    if mpt_endpoint:
        validate_local_mpt_endpoint(mpt_endpoint)

    deepseek_key, deepseek_source = deepseek_api_key(local_env)
    codex_executable = env_value("CODEX_EXECUTABLE", local_env, str(file_config.get("codex_executable") or "codex")).strip() or "codex"
    social_upload_dir = _optional_path(
        env_value("SOCIAL_UPLOAD_DIR", local_env, str(file_config.get("social_upload_dir") or ""))
    )
    runs_dir = _optional_path(env_value("DIRECTOR_RUNS_DIR", local_env, str(file_config.get("runs_dir") or ""))) or RUNS_DIR
    mpt_dir = _optional_path(env_value("MPT_DIR", local_env, str(file_config.get("mpt_dir") or ""))) or MPT_DIR

    return RuntimeConfig(
        root_dir=ROOT_DIR,
        workflow_dir=WORKFLOW_DIR,
        runs_dir=runs_dir,
        mpt_dir=mpt_dir,
        mpt_api_endpoint=mpt_endpoint,
        default_duration_seconds=_int_config(
            env_value(
                "DIRECTOR_DEFAULT_DURATION_SECONDS",
                local_env,
                file_config.get("default_duration_seconds", 60),
            ),
            60,
        ),
        default_platform=env_value("DIRECTOR_DEFAULT_PLATFORM", local_env, str(file_config.get("default_platform") or "douyin")),
        default_voice_name=env_value(
            "DIRECTOR_DEFAULT_VOICE_NAME",
            local_env,
            str(file_config.get("default_voice_name") or "gemini:Zephyr-Female"),
        ),
        deepseek_model=env_value("DEEPSEEK_MODEL", local_env, str(file_config.get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL)),
        deepseek_base_url=env_value("DEEPSEEK_BASE_URL", local_env, DEFAULT_DEEPSEEK_BASE_URL).rstrip("/"),
        codex_executable=codex_executable,
        social_upload_dir=social_upload_dir,
        pexels_configured=_configured_stock_api("pexels", local_env, mpt_dir),
        pixabay_configured=_configured_stock_api("pixabay", local_env, mpt_dir),
        deepseek_api_configured=bool(deepseek_key),
        deepseek_key_source=deepseek_source,
        deepseek_cli_available=shutil.which("deepseek") is not None,
        codex_cli_available=shutil.which(codex_executable) is not None,
    )


def api_setup_report(config: RuntimeConfig | None = None) -> dict[str, Any]:
    config = config or load_runtime_config()
    endpoint_issue = ""
    try:
        validate_local_mpt_endpoint(config.mpt_api_endpoint)
    except ValueError as exc:
        endpoint_issue = str(exc)
    mpt_reachable, mpt_reachable_issue = _mpt_api_reachability(config.mpt_api_endpoint) if not endpoint_issue else (False, endpoint_issue)
    return {
        "status": "complete",
        "config": config.to_public_dict(),
        "checks": {
            "mpt_endpoint_local": not endpoint_issue,
            "mpt_endpoint_issue": endpoint_issue,
            "mpt_api_reachable": mpt_reachable,
            "mpt_api_issue": mpt_reachable_issue,
            "pexels_api_key": config.pexels_configured,
            "pixabay_api_key": config.pixabay_configured,
            "deepseek_api_or_cli": config.deepseek_api_configured or config.deepseek_cli_available,
            "codex_cli": config.codex_cli_available,
            "social_upload_dir_exists": bool(config.social_upload_dir and config.social_upload_dir.exists()),
        },
        "security": {
            "secrets_redacted": True,
            "open_source_rule": "Use environment variables, .env.local, or OS keychain. Do not commit real API keys or cookies.",
        },
    }


def mpt_task_endpoint(config: RuntimeConfig | None = None) -> str:
    return (config or load_runtime_config()).mpt_api_endpoint


def codex_executable(config: RuntimeConfig | None = None) -> str:
    return (config or load_runtime_config()).codex_executable


def deepseek_api_credentials(config: RuntimeConfig | None = None) -> tuple[str, str, str]:
    local_env = load_local_env()
    key, _source = deepseek_api_key(local_env)
    if config:
        return key, config.deepseek_base_url, config.deepseek_model
    base_url = env_value("DEEPSEEK_BASE_URL", local_env, DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
    model = env_value("DEEPSEEK_MODEL", local_env, DEFAULT_DEEPSEEK_MODEL)
    return key, base_url, model


def local_task_url(endpoint: str, task_id: str) -> str:
    return _local_task_url(endpoint, task_id)
