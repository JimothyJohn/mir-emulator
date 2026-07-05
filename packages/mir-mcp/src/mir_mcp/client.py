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
- ``MIR_VERSION`` / ``MIR_FLEET_VERSION`` — optional MiR software version to
  pin when the URL points at a multi-version dispatcher (the public demo);
  default is whatever the dispatcher reports as newest

Version handling is a connect-time handshake, not configuration: the first
request probes the target (``/healthz`` manifest, ``/`` index, the official
fleet ``/api/v1/system/version``, ``/swagger.json``, then a bare
``/api/v2.0.0/status``) to learn what it is and which software version it
runs, and caches the answer per base URL.
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


class TargetResolutionError(RuntimeError):
    """A pinned MIR_VERSION/MIR_FLEET_VERSION the target does not serve."""


# Connect-time handshake results, keyed by base URL. Only successful
# identifications are cached, so a target that boots late is re-probed.
_detected: dict[str, dict[str, Any]] = {}


async def _probe(base_url: str, path: str, headers: dict[str, str] | None = None):
    try:
        return await _request(base_url, headers or {}, "GET", path)
    except httpx.HTTPError:
        return None


async def _identify(base_url: str) -> dict[str, Any] | None:
    """One pass of the discovery handshake; None means nothing MiR answered.

    Same probe order as ``mir_client.discovery`` — the two implementations
    share the protocol, not code, so mir-mcp stays installable on its own.
    """
    hit = await _probe(base_url, "/healthz")
    if hit and hit[0] == 200 and isinstance(hit[1], dict):
        versions = hit[1].get("versions")
        if isinstance(versions, list) and versions:
            fleet_versions = [str(v) for v in hit[1].get("fleet_versions") or []]
            return {
                "kind": "dispatcher",
                "version": None,
                "versions": [str(v) for v in versions],
                "latest": str(hit[1].get("latest") or versions[0]),
                "fleet_versions": fleet_versions,
                "fleet_latest": str(hit[1].get("fleet_latest") or "")
                or (fleet_versions[0] if fleet_versions else None),
            }
    hit = await _probe(base_url, "/", headers={"Accept": "application/json"})
    if hit and hit[0] == 200 and isinstance(hit[1], dict):
        doc = hit[1]
        if doc.get("kind") == "robot" or "emulated_mir_version" in doc:
            return {"kind": "robot", "version": doc.get("emulated_mir_version")}
        if doc.get("kind") == "fleet" or "emulated_fleet_version" in doc:
            return {"kind": "fleet", "version": doc.get("emulated_fleet_version")}
    hit = await _probe(base_url, f"{FLEET_API_PREFIX}/system/version", headers=fleet_headers())
    if (
        hit
        and hit[0] == 200
        and isinstance(hit[1], dict)
        and isinstance(hit[1].get("version"), str)
    ):
        return {"kind": "fleet", "version": hit[1]["version"]}
    hit = await _probe(base_url, "/swagger.json")
    if hit and hit[0] == 200 and isinstance(hit[1], dict):
        info = hit[1].get("info")
        has_spec_marker = hit[1].get("swagger") or hit[1].get("openapi")
        if has_spec_marker and isinstance(info, dict) and isinstance(info.get("version"), str):
            return {"kind": "robot", "version": info["version"]}
    hit = await _probe(base_url, f"{ROBOT_API_PREFIX}/status")
    if hit and hit[0] in (200, 401):
        # The API family is proven (401 = MiR auth in front of it); real
        # robots expose no version field, so the version stays unknown.
        return {"kind": "robot", "version": None}
    return None


async def detect_target(base_url: str) -> dict[str, Any]:
    """What is at *base_url* (robot / fleet / dispatcher) and which MiR
    software version does it run? Cached per URL after the first success."""
    if base_url in _detected:
        return _detected[base_url]
    info = await _identify(base_url)
    if info is None:
        return {"kind": "unknown", "version": None}
    _detected[base_url] = info
    return info


async def resolved_robot_base() -> str:
    """Robot API root: the target itself, or the right dispatcher mount."""
    base = robot_base_url()
    info = await detect_target(base)
    if info["kind"] != "dispatcher":
        return base
    version = os.environ.get("MIR_VERSION") or info["latest"]
    if version not in info["versions"]:
        raise TargetResolutionError(
            f"Error: MIR_VERSION={version} is not served by {base}; "
            f"it serves: {', '.join(info['versions'])}."
        )
    return f"{base}/{version}"


async def resolved_fleet_base() -> str:
    base = fleet_base_url()
    info = await detect_target(base)
    if info["kind"] != "dispatcher":
        return base
    if not info["fleet_versions"]:
        raise TargetResolutionError(f"Error: {base} serves no fleet versions.")
    version = os.environ.get("MIR_FLEET_VERSION") or info["fleet_latest"]
    if version not in info["fleet_versions"]:
        raise TargetResolutionError(
            f"Error: MIR_FLEET_VERSION={version} is not served by {base}; "
            f"it serves: {', '.join(info['fleet_versions'])}."
        )
    return f"{base}/fleet/{version}"


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
    base = await resolved_robot_base()
    return await _request(base, robot_headers(), method, f"{prefix}{path}", json=json)


async def fleet_request(method: str, path: str, *, json: Any = None) -> tuple[int, Any]:
    base = await resolved_fleet_base()
    return await _request(base, fleet_headers(), method, f"{FLEET_API_PREFIX}{path}", json=json)


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
