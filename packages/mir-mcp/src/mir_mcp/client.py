"""HTTP plumbing shared by every MCP tool.

Configuration is environment-driven so the same server binary points at an
emulator on localhost or a real robot on the shop floor:

- ``MIR_ROBOT_URL``  — robot base URL (default ``http://127.0.0.1:8080``)
- ``MIR_FLEET_URL``  — fleet base URL (default: same as ``MIR_ROBOT_URL``)
- ``MIR_USERNAME`` / ``MIR_PASSWORD`` — robot account (default the MiR
  factory account ``distributor``/``distributor``)
- ``MIR_API_KEY``    — fleet ``x-api-key`` (default ``distributor``)
- ``MIR_SESSION``    — optional ``X-MiR-Session`` id for emulator state
  isolation (emulator-only; harmless against real hardware)
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import httpx

ROBOT_API_PREFIX = "/api/v2.0.0"
FLEET_API_PREFIX = "/api/v1"
REQUEST_TIMEOUT_S = 15.0

# Tests inject an in-process ASGI transport here; production uses real TCP.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def robot_base_url() -> str:
    return os.environ.get("MIR_ROBOT_URL", "http://127.0.0.1:8080").rstrip("/")


def fleet_base_url() -> str:
    return os.environ.get("MIR_FLEET_URL", robot_base_url()).rstrip("/")


def mir_basic_token(username: str, password: str) -> str:
    """MiR Basic auth: the password travels as its lowercase SHA-256 hex digest."""
    digest = hashlib.sha256(password.encode()).hexdigest()
    return base64.b64encode(f"{username}:{digest}".encode()).decode()


def _session_headers() -> dict[str, str]:
    session = os.environ.get("MIR_SESSION", "")
    return {"X-MiR-Session": session} if session else {}


def robot_headers() -> dict[str, str]:
    username = os.environ.get("MIR_USERNAME", "distributor")
    password = os.environ.get("MIR_PASSWORD", "distributor")
    return {
        "Authorization": f"Basic {mir_basic_token(username, password)}",
        **_session_headers(),
    }


def fleet_headers() -> dict[str, str]:
    return {
        "x-api-key": os.environ.get("MIR_API_KEY", "distributor"),
        **_session_headers(),
    }


async def _request(
    base_url: str,
    headers: dict[str, str],
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    async with httpx.AsyncClient(
        transport=TRANSPORT, timeout=REQUEST_TIMEOUT_S, headers=headers
    ) as client:
        response = await client.request(method, f"{base_url}{path}", json=json, params=params)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


async def robot_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    api: bool = True,
) -> tuple[int, Any]:
    """Call the robot. ``api=True`` prefixes /api/v2.0.0; emulator-only
    surfaces (``/_emulator/*``) live at the root and pass ``api=False``."""
    prefix = ROBOT_API_PREFIX if api else ""
    return await _request(robot_base_url(), robot_headers(), method, f"{prefix}{path}", json=json)


async def fleet_request(method: str, path: str, *, json: Any = None) -> tuple[int, Any]:
    return await _request(
        fleet_base_url(), fleet_headers(), method, f"{FLEET_API_PREFIX}{path}", json=json
    )


def describe_http_error(status: int, body: Any, target: str) -> str:
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("error_human") or body.get("error_code") or "")
    if status == 401:
        return (
            f"Error: {target} rejected the credentials (401). Check MIR_USERNAME/"
            "MIR_PASSWORD (robot) or MIR_API_KEY (fleet); the emulator default "
            "for all three is 'distributor'."
        )
    if status == 404:
        return f"Error: not found (404). {detail or 'Check the id — list it first.'}"
    return f"Error: {target} answered {status}. {detail}".strip()


def describe_connection_error(exc: Exception, url: str) -> str:
    return (
        f"Error: cannot reach {url} ({type(exc).__name__}). If you meant the "
        "emulator, start one with `uv run mir-emulator` (robot) or "
        "`uv run mir-emulator --fleet-version 1.5.0` (fleet), or point "
        "MIR_ROBOT_URL/MIR_FLEET_URL at the right host."
    )
