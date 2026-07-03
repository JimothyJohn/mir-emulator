"""Hand-written behavior overlays for endpoints where generic CRUD is too dumb.

Everything not listed here falls back to the spec-driven generic handler in
app.py. Overrides receive a RequestCtx and return (status, body) — or
(status, body, media_type) for non-JSON endpoints.

Mission simulation: queued missions "run" without any background task — the
lifecycle is derived from timestamps at read time (Pending → Executing for
MISSION_DURATION_S seconds → Done, FIFO, one robot). This is what makes the
simulation work identically under uvicorn, Docker, and AWS Lambda (where no
code runs between invocations), and what keeps tests deterministic: freeze
`_now` and the whole world stands still. While a mission executes, /status
reports Executing, the position advances along a patrol loop, the battery
drains, and PUT /status {"state_id": 4} pauses the simulation clock
({"state_id": 3} resumes it).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from mir_emulator.examples import example_from_schema, overlay_compatible
from mir_emulator.spec import Operation, Spec
from mir_emulator.state import StateStore

REGISTER_COUNT = 200

# --- mission simulation tuning ----------------------------------------------
MISSION_DURATION_S = 10.0  # each mission executes for this long
MISSION_PENDING_LAG_S = 1.0  # a real robot takes a beat to pick a mission up
MISSION_SPEED_M_S = 0.5  # simulated travel speed while executing
BATTERY_START = 92.5
BATTERY_DRAIN_PER_S = 0.05  # percent per executing second
BATTERY_FLOOR = 20.0
BATTERY_SECONDS_PER_PERCENT = 375  # scales battery_time_remaining
LOOP_LENGTH_M = 40.0  # the simulated robot patrols a 40 m loop
POSITION_HOME_X = 5.0
POSITION_HOME_Y = 5.0


def _now() -> float:
    """Wall clock, in one place so tests can freeze it."""
    return time.time()


# Values overlaid onto the schema-derived /status example. Only keys that
# exist in the schema (and match its types) are applied — see overlay_compatible.
STATUS_OVERLAY: dict[str, Any] = {
    "robot_name": "MiR_Emulated",
    "robot_model": "MiR250",
    "serial_number": "000000000000000",
    "state_id": 3,
    "state_text": "Ready",
    "mode_id": 7,
    "mode_text": "Mission",
    "battery_percentage": 92.5,
    "battery_time_remaining": 34664,
    "uptime": 3600,
    "mission_text": "Waiting for new missions...",
    "moved": 1204.5,
    "distance_to_next_target": 0.0,
}


@dataclass
class RequestCtx:
    spec: Spec
    state: StateStore
    op: Operation
    path_params: dict[str, Any]
    query: dict[str, str]
    body: Any
    mission_duration: float = MISSION_DURATION_S


# --- mission simulation core -------------------------------------------------


def _sim(ctx: RequestCtx) -> dict:
    return ctx.state.singleton("/mission_sim", {"paused_at": None, "paused_total": 0.0})


def _sim_clock(ctx: RequestCtx) -> float:
    """Simulation time: wall clock minus every paused interval."""
    sim = _sim(ctx)
    paused = sim.get("paused_total", 0.0)
    if sim.get("paused_at") is not None:
        paused += _now() - sim["paused_at"]
    return _now() - paused


def _is_paused(ctx: RequestCtx) -> bool:
    return _sim(ctx).get("paused_at") is not None


def _pause_sim(ctx: RequestCtx) -> None:
    if not _is_paused(ctx):
        ctx.state.merge_singleton("/mission_sim", {"paused_at": _now()})


def _resume_sim(ctx: RequestCtx) -> None:
    sim = _sim(ctx)
    if sim.get("paused_at") is not None:
        ctx.state.merge_singleton(
            "/mission_sim",
            {
                "paused_at": None,
                "paused_total": sim.get("paused_total", 0.0) + (_now() - sim["paused_at"]),
            },
        )


def _timeline(ctx: RequestCtx) -> list[tuple[dict, float, float]]:
    """(entry, start, finish) per queued mission — FIFO, one robot at a time."""
    items = sorted(
        ctx.state.collection("/mission_queue").items(),
        key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0,
    )
    out: list[tuple[dict, float, float]] = []
    cursor: float | None = None
    for _id, entry in items:
        queued = float(entry.get("_queued_at", 0.0))
        start = queued + MISSION_PENDING_LAG_S
        if cursor is not None:
            start = max(start, cursor)
        finish = start + ctx.mission_duration
        out.append((entry, start, finish))
        cursor = finish
    return out


def _entry_state(start: float, finish: float, clock: float) -> str:
    if clock < start:
        return "Pending"
    if clock < finish:
        return "Executing"
    return "Done"


def _public_entry(entry: dict, start: float, finish: float, clock: float) -> dict:
    public = {k: v for k, v in entry.items() if not k.startswith("_")}
    public["state"] = _entry_state(start, finish, clock)
    return public


def _mission_snapshot(ctx: RequestCtx) -> dict:
    """Sim-derived /status fields: state, battery, odometry, position."""
    clock = _sim_clock(ctx)
    timeline = _timeline(ctx)
    executed_s = sum(max(0.0, min(clock, finish) - start) for _e, start, finish in timeline)
    battery = max(BATTERY_FLOOR, BATTERY_START - BATTERY_DRAIN_PER_S * executed_s)
    distance = MISSION_SPEED_M_S * executed_s
    paused = _is_paused(ctx)

    fields: dict[str, Any] = {
        "battery_percentage": round(battery, 2),
        "battery_time_remaining": int(battery * BATTERY_SECONDS_PER_PERCENT),
        "moved": round(1204.5 + distance, 2),
        "_position_along_loop": distance % LOOP_LENGTH_M,
    }

    executing = next(((e, s, f) for e, s, f in timeline if s <= clock < f), None)
    if executing is not None:
        entry, _start, finish = executing
        remaining = finish - clock
        fields.update(
            {
                "state_id": 4 if paused else 5,
                "state_text": "Pause" if paused else "Executing",
                "mission_queue_id": int(entry.get("id", 0)),
                "mission_text": f"Executing mission (queue id {entry.get('id')})...",
                "distance_to_next_target": round(remaining * MISSION_SPEED_M_S, 2),
            }
        )
    elif paused:
        fields.update({"state_id": 4, "state_text": "Pause"})
    return fields


def _status_doc(ctx: RequestCtx) -> dict:
    """Layering, later wins: schema example → STATUS_OVERLAY → user PUTs →
    simulation. The sim owns robot state / battery / odometry while missions
    run; user-set fields like robot_name always survive."""
    op = ctx.spec.operations.get(("GET", "/status"), ctx.op)
    base = example_from_schema(ctx.spec.deref(op.success_schema))
    if isinstance(base, dict):
        overlay_compatible(base, STATUS_OVERLAY)
    doc = dict(base) if isinstance(base, dict) else {}
    for key, value in ctx.state.singleton("/status", {}).items():
        if key in doc:
            doc[key] = value

    snapshot = _mission_snapshot(ctx)
    along_loop = snapshot.pop("_position_along_loop")
    for key, value in snapshot.items():
        if key in doc:
            doc[key] = value
    if isinstance(doc.get("position"), dict):
        position = dict(doc["position"])
        # Patrol a straight out-and-back segment of the loop, home at (5, 5).
        half = LOOP_LENGTH_M / 2
        offset = along_loop if along_loop <= half else LOOP_LENGTH_M - along_loop
        position.update(
            {
                "x": round(POSITION_HOME_X + offset, 2),
                "y": POSITION_HOME_Y,
                "orientation": 0.0 if along_loop <= half else 180.0,
            }
        )
        doc["position"] = position
    if isinstance(doc.get("velocity"), dict):
        moving = snapshot.get("state_text") == "Executing"
        velocity = dict(doc["velocity"])
        if "linear" in velocity:
            velocity["linear"] = MISSION_SPEED_M_S if moving else 0.0
        if "angular" in velocity:
            velocity["angular"] = 0.0
        doc["velocity"] = velocity
    return doc


async def get_status(ctx: RequestCtx) -> tuple[int, Any]:
    return ctx.op.success_status, _status_doc(ctx)


async def put_status(ctx: RequestCtx) -> tuple[int, Any]:
    if isinstance(ctx.body, dict):
        # MiR semantics: state_id 4 pauses the robot, 3 sets it Ready again.
        # Pausing freezes the simulation clock, so a paused mission holds its
        # progress and resumes exactly where it stopped.
        state_id = ctx.body.get("state_id")
        if state_id == 4:
            _pause_sim(ctx)
        elif state_id == 3:
            _resume_sim(ctx)
        allowed = _status_doc(ctx)
        patch = {k: v for k, v in ctx.body.items() if k in allowed}
        ctx.state.merge_singleton("/status", patch)
    return ctx.op.success_status, _status_doc(ctx)


async def post_mission_queue(ctx: RequestCtx) -> tuple[int, Any]:
    entry = example_from_schema(ctx.spec.deref(ctx.op.success_schema))
    if not isinstance(entry, dict):
        entry = {}
    if isinstance(ctx.body, dict):
        entry.update(ctx.body)
    queue_id = ctx.state.next_int_id()
    entry["id"] = queue_id
    entry["_queued_at"] = _sim_clock(ctx)
    ctx.state.insert("/mission_queue", str(queue_id), entry)
    ctx.state.merge_singleton("/status", {"mission_queue_id": queue_id})
    clock = _sim_clock(ctx)
    for stored, start, finish in _timeline(ctx):
        if stored is entry:
            return ctx.op.success_status, _public_entry(stored, start, finish, clock)
    return ctx.op.success_status, _public_entry(entry, clock + 1, clock + 2, clock)


async def get_mission_queue(ctx: RequestCtx) -> tuple[int, Any]:
    clock = _sim_clock(ctx)
    return ctx.op.success_status, [
        _public_entry(entry, start, finish, clock) for entry, start, finish in _timeline(ctx)
    ]


async def get_mission_queue_item(ctx: RequestCtx) -> tuple[int, Any]:
    try:
        queue_id = int(next(iter(ctx.path_params.values())))
    except (StopIteration, TypeError, ValueError):
        return 404, {"error_code": "404", "error_human": "Not found"}
    clock = _sim_clock(ctx)
    for entry, start, finish in _timeline(ctx):
        if entry.get("id") == queue_id:
            return ctx.op.success_status, _public_entry(entry, start, finish, clock)
    return 404, {"error_code": "404", "error_human": "Not found"}


async def delete_mission_queue(ctx: RequestCtx) -> tuple[int, Any]:
    ctx.state.clear("/mission_queue")
    ctx.state.merge_singleton("/status", {"mission_queue_id": 0, "mission_text": ""})
    return ctx.op.success_status, None


def _register_doc(ctx: RequestCtx, register_id: int) -> dict:
    values = ctx.state.singleton("/registers", {})
    return {
        "id": register_id,
        "label": "",
        "url": f"/v2.0.0/registers/{register_id}",
        "value": float(values.get(str(register_id), 0.0)),
    }


async def get_registers(ctx: RequestCtx) -> tuple[int, Any]:
    return ctx.op.success_status, [_register_doc(ctx, i) for i in range(1, REGISTER_COUNT + 1)]


def _register_id(ctx: RequestCtx) -> int | None:
    try:
        register_id = int(next(iter(ctx.path_params.values())))
    except (StopIteration, TypeError, ValueError):
        return None
    return register_id if 1 <= register_id <= REGISTER_COUNT else None


async def get_register(ctx: RequestCtx) -> tuple[int, Any]:
    register_id = _register_id(ctx)
    if register_id is None:
        return 404, {"error_code": "404", "error_human": "register not found"}
    return ctx.op.success_status, _register_doc(ctx, register_id)


async def put_register(ctx: RequestCtx) -> tuple[int, Any]:
    register_id = _register_id(ctx)
    if register_id is None:
        return 404, {"error_code": "404", "error_human": "register not found"}
    value = ctx.body.get("value") if isinstance(ctx.body, dict) else None
    if not isinstance(value, int | float) or isinstance(value, bool):
        return 400, {"error_code": "400", "error_human": "value must be a number"}
    ctx.state.merge_singleton("/registers", {str(register_id): float(value)})
    return ctx.op.success_status, _register_doc(ctx, register_id)


async def get_metrics(ctx: RequestCtx) -> tuple[int, Any, str]:
    snapshot = _mission_snapshot(ctx)
    text = (
        "# TYPE mir_robot_battery_percent gauge\n"
        f"mir_robot_battery_percent {snapshot['battery_percentage']}\n"
        "# TYPE mir_robot_uptime_seconds counter\n"
        "mir_robot_uptime_seconds_total 3600\n"
        "# TYPE mir_robot_distance_moved_meters counter\n"
        f"mir_robot_distance_moved_meters_total {snapshot['moved']}\n"
        "# EOF\n"
    )
    return ctx.op.success_status, text, "text/plain; version=0.0.4"


OVERRIDES: dict[tuple[str, str], Any] = {
    ("GET", "/status"): get_status,
    ("PUT", "/status"): put_status,
    ("POST", "/mission_queue"): post_mission_queue,
    ("GET", "/mission_queue"): get_mission_queue,
    ("GET", "/mission_queue/{id}"): get_mission_queue_item,
    ("DELETE", "/mission_queue"): delete_mission_queue,
    ("GET", "/registers"): get_registers,
    ("GET", "/registers/{id}"): get_register,
    ("PUT", "/registers/{id}"): put_register,
    ("GET", "/metrics"): get_metrics,
}
