from __future__ import annotations

from urllib.parse import urlparse

LOCAL_MPT_HOSTS = {"127.0.0.1", "localhost", "::1"}
MPT_VIDEO_API_PATH = "/api/v1/videos"
DEFAULT_MPT_ENDPOINT = "http://127.0.0.1:8080/api/v1/videos"


def validate_local_mpt_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "http":
        raise ValueError("MPT endpoint must use http")
    if parsed.hostname not in LOCAL_MPT_HOSTS:
        raise ValueError("MPT endpoint must point to localhost or 127.0.0.1")
    if parsed.username or parsed.password:
        raise ValueError("MPT endpoint must not contain credentials")
    if parsed.path.rstrip("/") != MPT_VIDEO_API_PATH:
        raise ValueError(f"MPT endpoint path must be {MPT_VIDEO_API_PATH}")
    return endpoint


def local_task_url(endpoint: str, task_id: str) -> str:
    endpoint = validate_local_mpt_endpoint(endpoint)
    return endpoint[: -len(MPT_VIDEO_API_PATH)] + f"/api/v1/tasks/{task_id}"
