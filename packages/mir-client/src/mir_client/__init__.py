"""Typed clients for the MiR robot REST API and Fleet Integration API.

``mir_client.robot`` and ``mir_client.fleet`` are generated verbatim from the
official specs (see _provenance for exact versions and hashes); this module
adds the two constructors that wire in MiR's authentication schemes.
"""

from __future__ import annotations

from typing import Any

from mir_client import _provenance as provenance
from mir_client.auth import DEFAULT_PASSWORD, DEFAULT_USERNAME, robot_token
from mir_client.fleet.client import AuthenticatedClient as _FleetClient
from mir_client.robot.client import AuthenticatedClient as _RobotClient

DEFAULT_API_KEY = "distributor"  # the emulator's default; real fleets issue keys


def robot_client(
    base_url: str,
    *,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    **httpx_kwargs: Any,
) -> _RobotClient:
    """A client for one robot: *base_url* is the robot's root (no /api/v2.0.0)."""
    return _RobotClient(
        base_url=base_url.rstrip("/") + "/api/v2.0.0",
        token=robot_token(username, password),
        prefix="Basic",
        httpx_args=httpx_kwargs,
    )


def fleet_client(
    base_url: str, *, api_key: str = DEFAULT_API_KEY, **httpx_kwargs: Any
) -> _FleetClient:
    """A client for MiR Fleet Enterprise: *base_url* is the fleet root."""
    return _FleetClient(
        base_url=base_url.rstrip("/"),
        token=api_key,
        prefix="",
        auth_header_name="x-api-key",
        httpx_args=httpx_kwargs,
    )


__all__ = [
    "DEFAULT_API_KEY",
    "DEFAULT_PASSWORD",
    "DEFAULT_USERNAME",
    "fleet_client",
    "provenance",
    "robot_client",
    "robot_token",
]
