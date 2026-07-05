"""mir-mcp tool tests against real emulator apps over in-process ASGI.

No mocked services: every test drives the actual mir-emulator application
(robot or fleet) through the same HTTP stack production uses, with the MCP
tools' own auth derivation. Contract under test: natural-language-shaped
inputs -> documented MiR API effects -> readable JSON/error strings out.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mir_emulator.app import create_app
from mir_emulator.fleet import create_fleet_app
from mir_mcp import client, server

ENV_VARS = (
    "MIR_ROBOT_URL",
    "MIR_FLEET_URL",
    "MIR_USERNAME",
    "MIR_PASSWORD",
    "MIR_API_KEY",
    "MIR_SESSION",
    "MIR_VERSION",
    "MIR_FLEET_VERSION",
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Discovery results are cached per base URL; tests swap the transport
    # under the same URL, so every test starts with a cold handshake.
    client._detected.clear()


@pytest.fixture
def robot(monkeypatch):
    app = create_app("3.8.1", mission_duration=0.2)
    monkeypatch.setattr(client, "TRANSPORT", httpx.ASGITransport(app=app))


@pytest.fixture
def fleet(monkeypatch):
    app = create_fleet_app("1.5.0", mission_duration=0.2)
    monkeypatch.setattr(client, "TRANSPORT", httpx.ASGITransport(app=app))


def test_every_tool_is_registered():
    tools = {t.name for t in run(server.mcp.list_tools())}
    assert tools == {
        "mir_server_info",
        "mir_robot_status",
        "mir_set_robot_state",
        "mir_clear_error",
        "mir_list_missions",
        "mir_queue_mission",
        "mir_mission_queue",
        "mir_cancel_missions",
        "mir_read_register",
        "mir_write_register",
        "mir_manage_faults",
        "mir_fleet_robots",
        "mir_fleet_dispatch",
        "mir_fleet_order_status",
    }


def test_status_reports_live_state(robot):
    doc = json.loads(run(server.mir_robot_status()))
    assert doc["state_text"] == "Ready"
    assert 0 <= doc["battery_percentage"] <= 100
    assert {"x", "y", "orientation"} <= set(doc["position"])


def test_pause_then_resume_round_trip(robot):
    paused = json.loads(run(server.mir_set_robot_state("pause")))
    assert paused["state_id"] == 4
    ready = json.loads(run(server.mir_set_robot_state("ready")))
    assert ready["state_id"] == 3


def test_queue_mission_by_case_insensitive_name_and_wait(robot):
    result = json.loads(run(server.mir_queue_mission("EMULATED", wait_seconds=10)))
    assert result["mission"] == "emulated"
    assert result["queue_entry"]["state"] == "Done"


def test_queue_mission_by_guid(robot):
    missions = json.loads(run(server.mir_list_missions()))
    result = json.loads(run(server.mir_queue_mission(missions[0]["guid"])))
    assert result["queue_entry"]["state"] in ("Pending", "Executing")


def test_queue_unknown_mission_lists_alternatives(robot):
    out = run(server.mir_queue_mission("take-over-the-world"))
    assert out.startswith("Error: no mission")
    assert "emulated" in out  # tells the agent what IS available


def test_cancel_whole_queue(robot):
    run(server.mir_queue_mission("emulated"))
    assert json.loads(run(server.mir_cancel_missions()))["cancelled"] == "entire mission queue"
    assert json.loads(run(server.mir_mission_queue())) == []


def test_register_write_read_round_trip(robot):
    written = json.loads(run(server.mir_write_register(7, 42)))
    assert float(written["value"]) == 42
    read = json.loads(run(server.mir_read_register(7)))
    assert float(read["value"]) == 42


def test_register_out_of_range_is_an_error(robot):
    assert run(server.mir_read_register(999)).startswith("Error:")


def test_fault_injection_and_recovery(robot):
    injected = json.loads(run(server.mir_manage_faults(["emergency_stop"])))
    assert injected["active"] == ["emergency_stop"]
    assert json.loads(run(server.mir_robot_status()))["state_text"] == "EmergencyStop"
    # clear_error must NOT clear an e-stop (matches real MiR behavior)...
    run(server.mir_clear_error())
    assert json.loads(run(server.mir_robot_status()))["state_text"] == "EmergencyStop"
    # ...but the emulator-only fault surface clears everything.
    cleared = json.loads(run(server.mir_manage_faults()))
    assert cleared["active"] == []
    assert json.loads(run(server.mir_robot_status()))["state_text"] == "Ready"


def test_wrong_password_yields_actionable_401(robot, monkeypatch):
    monkeypatch.setenv("MIR_PASSWORD", "not-the-password")
    out = run(server.mir_robot_status())
    assert out.startswith("Error:")
    assert "401" in out
    assert "MIR_USERNAME" in out  # points at the fix, not just the failure


def test_unreachable_robot_says_how_to_start_one(monkeypatch):
    monkeypatch.setattr(client, "TRANSPORT", None)
    monkeypatch.setenv("MIR_ROBOT_URL", "http://127.0.0.1:9")  # discard port: refuses fast
    out = run(server.mir_robot_status())
    assert out.startswith("Error: cannot reach")
    assert "mir-emulator" in out


def test_fleet_lists_embedded_robots(fleet):
    doc = json.loads(run(server.mir_fleet_robots()))
    robots = doc["robots"]
    assert len(robots) == 2
    one = json.loads(run(server.mir_fleet_robots(robots[0]["robot-id"])))
    assert one["robot-identity"]["robot-id"] == robots[0]["robot-id"]


def test_fleet_dispatch_by_name_then_track_and_abort(fleet):
    order = json.loads(run(server.mir_fleet_dispatch(["EMULATED", "emulated"])))
    serial_id = order["id"]
    status = json.loads(run(server.mir_fleet_order_status(serial_id)))
    assert len(status["phases"]) == 2
    aborted = json.loads(run(server.mir_fleet_order_status(serial_id, abort=True)))
    assert aborted == {"aborted": serial_id}


def test_fleet_dispatch_unknown_mission_is_atomic(fleet):
    out = run(server.mir_fleet_dispatch(["emulated", "no-such-mission"]))
    assert out.startswith("Error: no site mission matches")
    # nothing was dispatched: the fleet has no orders
    assert run(server._fleet("GET", "/order")) == []


def test_fleet_wrong_api_key_is_actionable(fleet, monkeypatch):
    monkeypatch.setenv("MIR_API_KEY", "wrong")
    out = run(server.mir_fleet_robots())
    assert out.startswith("Error:")
    assert "MIR_API_KEY" in out


# ------------------------------------------------- connect-time discovery


@pytest.fixture
def dispatcher(monkeypatch):
    """The multi-version demo dispatcher at the configured base URL."""
    from mir_emulator.serverless import build_app

    monkeypatch.setattr(client, "TRANSPORT", httpx.ASGITransport(app=build_app()))


def test_server_info_identifies_a_robot(robot):
    doc = json.loads(run(server.mir_server_info()))
    assert doc["robot_target"]["kind"] == "robot"
    assert doc["robot_target"]["software_version"] == "3.8.1"
    assert "fleet_target" not in doc  # same URL, and it is not fleet-shaped


def test_server_info_identifies_a_fleet(fleet):
    doc = json.loads(run(server.mir_server_info()))
    assert doc["robot_target"]["kind"] == "fleet"
    assert doc["fleet_target"]["software_version"] == "1.5.0"


def test_tools_work_against_a_dispatcher_root_without_a_version(dispatcher):
    """The headline: point MIR_ROBOT_URL at the multi-version demo root and
    every tool adapts — no version installed, configured, or guessed."""
    from mir_emulator import registry

    doc = json.loads(run(server.mir_robot_status()))
    assert doc["state_text"] == "Ready"
    info = json.loads(run(server.mir_server_info()))
    latest = registry.supported_versions()[0]
    assert info["robot_target"]["kind"] == "dispatcher"
    assert info["robot_target"]["resolved_base"].endswith(f"/{latest}")
    assert info["robot_target"]["available_versions"] == registry.supported_versions()
    # and the fleet side resolves its own newest mount
    robots = json.loads(run(server.mir_fleet_robots()))
    assert len(robots["robots"]) == 2


def test_dispatcher_version_pin_is_honored(dispatcher, monkeypatch):
    from mir_emulator import registry

    oldest = registry.supported_versions()[-1]
    monkeypatch.setenv("MIR_VERSION", oldest)
    info = json.loads(run(server.mir_server_info()))
    assert info["robot_target"]["resolved_base"].endswith(f"/{oldest}")
    doc = json.loads(run(server.mir_robot_status()))
    assert doc["state_text"] == "Ready"


def test_dispatcher_rejects_an_unserved_version_pin(dispatcher, monkeypatch):
    monkeypatch.setenv("MIR_VERSION", "9.9.9")
    out = run(server.mir_robot_status())
    assert out.startswith("Error: MIR_VERSION=9.9.9 is not served")
    info = json.loads(run(server.mir_server_info()))
    assert info["robot_target"]["error"].startswith("Error: MIR_VERSION=9.9.9")


def test_handshake_runs_once_per_base_url(robot, monkeypatch):
    """The probe is a connect-time cost, not a per-request tax."""
    calls: list[str] = []
    original = client._identify

    async def counting(base_url: str) -> dict[str, Any] | None:
        calls.append(base_url)
        return await original(base_url)

    monkeypatch.setattr(client, "_identify", counting)
    run(server.mir_robot_status())
    run(server.mir_robot_status())
    assert len(calls) == 1


def test_unreachable_target_reports_unknown_kind(monkeypatch):
    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(client, "TRANSPORT", Down())
    info = json.loads(run(server.mir_server_info()))
    assert info["robot_target"]["kind"] == "unknown"
    assert "note" in info["robot_target"]
