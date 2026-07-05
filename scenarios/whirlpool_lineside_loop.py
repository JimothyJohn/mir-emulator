#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "mir-client"]
#
# [tool.uv.sources]
# mir-client = { path = "../packages/mir-client" }
# ///
"""Whirlpool Łódź — line-side delivery loop with a hot-spare robot.

Based on https://mobile-industrial-robots.com/cases/whirlpool
Three MiR200s shuttle 12 dryer doors per run on a ~130 m loop between
preassembly and the assembly line (~3 min 50 s per cycle). Two robots run
missions while the third waits on the charger as a hot spare; MiR Fleet
watches batteries and rotates robots.

What this exercises on the emulator:
  * X-MiR-Session — three fully isolated robots on one emulator
  * PUT /status {"name": ...} — naming robots
  * POST /missions + POST /mission_queue — per-robot mission definitions
  * battery drain across repeated cycles + spare rotation on low battery

Run:
    uv run mir-emulator --mission-duration 2 &
    uv run scenarios/whirlpool_lineside_loop.py
"""

import os
import sys
import time

import httpx
from mir_client import robot_token

MIR_URL = os.environ.get("MIR_URL", "http://127.0.0.1:8080")
API = "/api/v2.0.0"
CYCLES = 4
ROTATE_BELOW = 99.0  # % — artificially high so the demo shows a rotation


def client(session: str) -> httpx.Client:
    user = os.environ.get("MIR_USERNAME", "distributor")
    password = os.environ.get("MIR_PASSWORD", "distributor")
    token = robot_token(user, password)
    return httpx.Client(
        base_url=MIR_URL,
        headers={"Authorization": f"Basic {token}", "X-MiR-Session": session},
        timeout=15,
    )


def require_emulator(c: httpx.Client) -> None:
    try:
        index = c.get("/").json()
    except httpx.ConnectError:
        sys.exit(f"Nothing at {MIR_URL} — start one: uv run mir-emulator --mission-duration 2")
    if "emulated_mir_version" not in index:
        sys.exit(f"{MIR_URL} does not look like the emulator; refusing to run a demo against it.")


def make_mission(c: httpx.Client, name: str) -> str:
    group = c.get(f"{API}/mission_groups").json()[0]["guid"]
    r = c.post(f"{API}/missions", json={"name": name, "group_id": group})
    r.raise_for_status()
    return r.json()["guid"]


def run_mission(c: httpx.Client, guid: str) -> int:
    r = c.post(f"{API}/mission_queue", json={"mission_id": guid})
    r.raise_for_status()
    return r.json()["id"]


def wait_done(c: httpx.Client, queue_id: int, timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = c.get(f"{API}/mission_queue/{queue_id}").json()["state"]
        if state in ("Done", "Aborted"):
            return state
        time.sleep(0.3)
    raise TimeoutError(f"queue entry {queue_id} not finished after {timeout}s")


def battery(c: httpx.Client) -> float:
    return c.get(f"{API}/status").json()["battery_percentage"]


def main() -> None:
    robots = {}
    for n in (1, 2, 3):
        c = client(f"whirlpool-amr-{n}")
        require_emulator(c)
        c.put(f"{API}/status", json={"name": f"MiR200-{n}"})
        robots[n] = {
            "client": c,
            "mission": make_mission(c, "Line-side loop: 12 dryer doors, preassembly -> line"),
        }

    active, spare = [1, 2], 3
    print(f"fleet up: robots 1-3, active={active}, hot spare={spare}\n")

    for cycle in range(1, CYCLES + 1):
        queued = [(n, run_mission(robots[n]["client"], robots[n]["mission"])) for n in active]
        for n, qid in queued:
            state = wait_done(robots[n]["client"], qid)
            if state != "Done":
                sys.exit(f"robot {n} cycle {cycle} ended {state}")
        levels = {n: battery(robots[n]["client"]) for n in robots}
        print(
            f"cycle {cycle}: doors delivered by {active} | battery "
            + " ".join(f"R{n}={levels[n]:.2f}%" for n in sorted(levels))
        )

        # Fleet-style rotation: weakest active robot swaps with the spare
        # once it drops below the threshold and the spare has more charge.
        weakest = min(active, key=lambda n: levels[n])
        if levels[weakest] < ROTATE_BELOW and levels[spare] > levels[weakest]:
            print(
                f"  rotating: R{weakest} ({levels[weakest]:.2f}%) -> charger, "
                f"R{spare} ({levels[spare]:.2f}%) -> line"
            )
            active[active.index(weakest)] = spare
            spare = weakest

    levels = {n: battery(robots[n]["client"]) for n in robots}
    print(
        f"\n{CYCLES} cycles complete. Final battery: "
        + " ".join(f"R{n}={levels[n]:.2f}%" for n in sorted(levels))
    )
    print("Real-world reference: ~3m50s per 130 m loop, one dryer off the line every 15 s.")


if __name__ == "__main__":
    main()
