"""Mission simulation + per-session virtual robots.

The simulation is time-derived (no background tasks), so tests freeze
behaviors._now and step it manually — fully deterministic.
"""

import threading

import pytest
from mir_emulator import behaviors
from mir_emulator.app import MAX_SESSIONS, create_app
from mir_emulator.auth import expected_token
from starlette.testclient import TestClient

AUTH = {"Authorization": f"Basic {expected_token('distributor', 'distributor')}"}
T0 = 1_750_000_000.0


class Clock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def tick(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(behaviors, "_now", c)
    return c


@pytest.fixture()
def client():
    return TestClient(create_app("3.8.1"))


def enqueue(client, headers=AUTH, **body):
    r = client.post(
        "/api/v2.0.0/mission_queue", json={"mission_id": "generic", **body}, headers=headers
    )
    assert r.status_code == 201
    return r.json()


def q_state(client, qid, headers=AUTH):
    r = client.get(f"/api/v2.0.0/mission_queue/{qid}", headers=headers)
    assert r.status_code == 200
    return r.json()["state"]


def status(client, headers=AUTH):
    r = client.get("/api/v2.0.0/status", headers=headers)
    assert r.status_code == 200
    return r.json()


# --- lifecycle ---------------------------------------------------------------


def test_mission_runs_ten_seconds_then_stops(clock, client):
    entry = enqueue(client)
    qid = entry["id"]
    assert entry["state"] == "Pending"

    clock.tick(0.5)  # still inside the pickup lag
    assert q_state(client, qid) == "Pending"

    clock.tick(1.0)  # 1.5s: executing
    assert q_state(client, qid) == "Executing"
    assert status(client)["state_text"] == "Executing"
    assert status(client)["mission_queue_id"] == qid

    clock.tick(9.0)  # 10.5s: 9.5s of execution done, still running
    assert q_state(client, qid) == "Executing"

    clock.tick(1.0)  # 11.5s: past 1s lag + 10s run
    assert q_state(client, qid) == "Done"
    doc = status(client)
    assert doc["state_text"] == "Ready"
    assert doc["state_id"] == 3


def test_missions_run_fifo_one_at_a_time(clock, client):
    first = enqueue(client)["id"]
    second = enqueue(client)["id"]

    clock.tick(2.0)
    assert q_state(client, first) == "Executing"
    assert q_state(client, second) == "Pending"

    clock.tick(10.0)  # 12s: first done (1..11), second executing (11..21)
    assert q_state(client, first) == "Done"
    assert q_state(client, second) == "Executing"

    clock.tick(10.0)  # 22s: all done
    assert q_state(client, second) == "Done"


def test_internal_fields_never_leak(clock, client):
    enqueue(client)
    listing = client.get("/api/v2.0.0/mission_queue", headers=AUTH).json()
    assert listing and all(not k.startswith("_") for e in listing for k in e)


def test_delete_clears_queue_and_status(clock, client):
    enqueue(client)
    clock.tick(2.0)
    deleted = client.delete("/api/v2.0.0/mission_queue", headers=AUTH)
    assert deleted.status_code == 204
    assert client.get("/api/v2.0.0/mission_queue", headers=AUTH).json() == []
    assert status(client)["state_text"] == "Ready"


# --- telemetry coupling ------------------------------------------------------


def test_battery_drains_and_position_moves_while_executing(clock, client):
    before = status(client)
    enqueue(client)
    clock.tick(6.0)  # 5s of execution
    during = status(client)
    assert during["battery_percentage"] < before["battery_percentage"]
    assert during["position"]["x"] == pytest.approx(5.0 + 5 * behaviors.MISSION_SPEED_M_S)
    assert during["velocity"]["linear"] == behaviors.MISSION_SPEED_M_S
    assert during["distance_to_next_target"] > 0

    clock.tick(20.0)  # long done
    after = status(client)
    assert after["velocity"]["linear"] == 0.0
    assert after["moved"] > before["moved"]
    # battery stops draining once the mission is over
    clock.tick(100.0)
    assert status(client)["battery_percentage"] == after["battery_percentage"]


def test_battery_never_drops_below_floor(clock, client):
    # (92.5 - 20) / 0.05%/s = 1450s of execution; 150 missions = 1500s
    for _ in range(150):
        enqueue(client)
    clock.tick(1600.0)
    assert status(client)["battery_percentage"] == behaviors.BATTERY_FLOOR


def test_metrics_reflect_simulation(clock, client):
    enqueue(client)
    clock.tick(6.0)
    text = client.get("/api/v2.0.0/metrics", headers=AUTH).text
    assert "mir_robot_battery_percent 92.25" in text  # 5s * 0.05%/s drained


# --- pause / resume ----------------------------------------------------------


def test_pause_freezes_mission_and_resume_completes_it(clock, client):
    qid = enqueue(client)["id"]
    clock.tick(5.0)  # 4s executed
    pause = client.put("/api/v2.0.0/status", json={"state_id": 4}, headers=AUTH)
    assert pause.status_code == 200
    paused = status(client)
    assert paused["state_text"] == "Pause"
    assert paused["state_id"] == 4

    clock.tick(300.0)  # wall clock races ahead; sim is frozen
    assert q_state(client, qid) == "Executing"
    assert status(client)["state_text"] == "Pause"

    resume = client.put("/api/v2.0.0/status", json={"state_id": 3}, headers=AUTH)
    assert resume.status_code == 200
    clock.tick(5.0)  # 4s + 5s = 9s executed, still going
    assert q_state(client, qid) == "Executing"
    clock.tick(2.0)  # 11s > 10s: done
    assert q_state(client, qid) == "Done"


def test_user_status_writes_survive_simulation(clock, client):
    client.put("/api/v2.0.0/status", json={"robot_name": "conveyor-7"}, headers=AUTH)
    enqueue(client)
    clock.tick(3.0)
    doc = status(client)
    assert doc["robot_name"] == "conveyor-7"
    assert doc["state_text"] == "Executing"  # sim owns state, user owns name


# --- sessions: many users, many robots --------------------------------------


def sess(name):
    return {**AUTH, "X-MiR-Session": name}


def test_sessions_are_isolated_robots(clock, client):
    alice_mission = enqueue(client, headers=sess("alice"))["id"]
    clock.tick(3.0)

    # Alice's robot is executing; Bob's and the shared robot are idle.
    assert status(client, sess("alice"))["state_text"] == "Executing"
    assert status(client, sess("bob"))["state_text"] == "Ready"
    assert status(client)["state_text"] == "Ready"
    assert client.get("/api/v2.0.0/mission_queue", headers=sess("bob")).json() == []
    assert client.get("/api/v2.0.0/mission_queue", headers=AUTH).json() == []
    assert q_state(client, alice_mission, headers=sess("alice")) == "Executing"

    # Registers are isolated too.
    client.put("/api/v2.0.0/registers/1", json={"value": 7}, headers=sess("alice"))
    assert client.get("/api/v2.0.0/registers/1", headers=sess("bob")).json()["value"] == 0.0

    # Pausing Alice's robot does not pause Bob's.
    client.put("/api/v2.0.0/status", json={"state_id": 4}, headers=sess("alice"))
    enqueue(client, headers=sess("bob"))
    clock.tick(3.0)
    assert status(client, sess("alice"))["state_text"] == "Pause"
    assert status(client, sess("bob"))["state_text"] == "Executing"


def test_invalid_session_ids_rejected(client):
    # (non-latin-1 ids can't even be sent by httpx; the Lambda adapter path
    # utf-8-encodes headers and the regex rejects them server-side)
    for bad in ["a" * 65, "spaces here", "semi;colon", "a/b", "a\tb"]:
        r = client.get("/api/v2.0.0/status", headers={**AUTH, "X-MiR-Session": bad})
        assert r.status_code == 400, bad


def test_session_count_is_capped(client):
    emulator = client.app.state.emulator
    for i in range(MAX_SESSIONS + 50):
        emulator.state_for(f"s{i}")
    assert len(emulator._sessions) == MAX_SESSIONS
    # oldest evicted, newest retained
    assert "s0" not in emulator._sessions
    assert f"s{MAX_SESSIONS + 49}" in emulator._sessions


def test_concurrent_session_churn_is_safe(client):
    emulator = client.app.state.emulator
    errors = []

    def churn(worker):
        try:
            for i in range(300):
                store = emulator.state_for(f"w{worker}-{i % 40}")
                store.merge_singleton("/registers", {"1": float(worker)})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=churn, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(emulator._sessions) <= MAX_SESSIONS


def test_custom_mission_duration(clock):
    quick = TestClient(create_app("3.8.1", mission_duration=2.0))
    qid = enqueue(quick)["id"]
    clock.tick(1.5)
    assert q_state(quick, qid) == "Executing"
    clock.tick(2.0)  # 3.5s > 1s lag + 2s run
    assert q_state(quick, qid) == "Done"
