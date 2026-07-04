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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mir_emulator.examples import example_from_schema, overlay_compatible
from mir_emulator.spec import Operation, Spec
from mir_emulator.state import StateStore

REGISTER_COUNT = 200

# PUT /status choices, identical across every tracked spec (the swagger files
# state them in the property descriptions, not as enums):
#   state_id "Choices are: {3, 4, 11}, State: {Ready, Pause, Manualcontrol}"
#   mode_id  "Choices are: {3, 7}"
#   clear_error "Choices are: {True}"
STATE_CHOICES = {3: "Ready", 4: "Pause", 11: "Manualcontrol"}
MODE_CHOICES = {3: "Mapping", 7: "Mission"}
UPTIME_START_S = 3600  # the emulated robot "booted" an hour before first contact

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


def _wall_clock() -> float:
    return time.time()


# The one clock everything reads. A plain reassignable attribute on purpose:
# frozen-clock tests and scenario replay (record.py) substitute it.
_now: Callable[[], float] = _wall_clock


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
    # Bound by app.Emulator: seed_collection(key, collection_path) populates a
    # generic collection exactly as a GET on it would, so overrides can check
    # referential integrity against not-yet-touched collections.
    seed_collection: Callable[[str, str], None] | None = None
    # The validated X-MiR-Session id ("" for the shared default robot). The
    # fleet emulator forwards it on embedded robot calls so session isolation
    # composes across emulators.
    session_id: str = ""


# --- fault injection ---------------------------------------------------------
# Drives the robot into the states integrators must handle. Read-side state
# ids beyond the writable {3, 4, 11}: the official swagger never enumerates
# them; they are pinned by MiR's own ROS message table (mir_msgs/RobotState.msg
# in DFKI-NI/mir_robot, mirrored from real robots: EMERGENCYSTOP=10, ERROR=12)
# and match the open-rmf fleet adapter's constants. Error codes here are
# deliberately in a 10xxx emulator range and every description says
# "(emulated fault)" — they are not official MiR error codes.
FAULTS: dict[str, dict[str, Any]] = {
    "emergency_stop": {
        "description": "Emergency stop pressed: state 10, execution freezes until cleared.",
        "state_id": 10,
        "state_text": "EmergencyStop",
        "holds": True,
        "error": {
            "code": 10010,
            "description": "Emergency stop pressed (emulated fault)",
            "module": "SafetySystem",
            "non_resettable": True,
        },
    },
    "error": {
        "description": "General error: state 12; clears via PUT /status {'clear_error': true}.",
        "state_id": 12,
        "state_text": "Error",
        "holds": True,
        "resettable": True,
        "error": {
            "code": 10012,
            "description": "General error (emulated fault)",
            "module": "MissionController",
            "non_resettable": False,
        },
    },
    "localization_lost": {
        "description": "Robot lost localization: state 12; clear_error resets it.",
        "state_id": 12,
        "state_text": "Error",
        "holds": True,
        "resettable": True,
        "error": {
            "code": 10013,
            "description": "Robot is not localized (emulated fault)",
            "module": "Localization",
            "non_resettable": False,
        },
    },
    "battery_critical": {
        "description": "Battery forced to 1.5%; state unchanged.",
        "battery": 1.5,
        "error": {
            "code": 10014,
            "description": "Battery level critical (emulated fault)",
            "module": "Battery",
            "non_resettable": False,
        },
    },
    "blocked_path": {
        "description": "Path blocked: an active planner error while the robot keeps trying.",
        "error": {
            "code": 10015,
            "description": "Path is blocked (emulated fault)",
            "module": "Planner",
            "non_resettable": False,
        },
    },
}


def active_faults(state: StateStore) -> list[str]:
    return [n for n in state.singleton("/faults", {"active": []}).get("active", []) if n in FAULTS]


def set_faults(state: StateStore, names: list[str]) -> None:
    """Replace the active fault set; holding faults freeze the sim clock."""
    state.merge_singleton("/faults", {"active": [n for n in names if n in FAULTS]})
    holds = any(FAULTS[n].get("holds") for n in active_faults(state))
    sim = state.singleton("/mission_sim", {"paused_at": None, "paused_total": 0.0, "hold": None})
    if holds and sim.get("paused_at") is None:
        state.merge_singleton("/mission_sim", {"paused_at": _now(), "hold": "fault"})
    elif not holds and sim.get("hold") == "fault" and sim.get("paused_at") is not None:
        state.merge_singleton(
            "/mission_sim",
            {
                "paused_at": None,
                "paused_total": sim.get("paused_total", 0.0) + (_now() - sim["paused_at"]),
                "hold": None,
            },
        )


def faults_doc(state: StateStore) -> dict:
    return {
        "active": active_faults(state),
        "available": {name: fault["description"] for name, fault in FAULTS.items()},
        "clearing": (
            'PUT {"faults": [...]} replaces the set; DELETE clears everything; '
            'resettable faults also clear via PUT /status {"clear_error": true}'
        ),
    }


# --- mission simulation core -------------------------------------------------


def _sim(ctx: RequestCtx) -> dict:
    return ctx.state.singleton(
        "/mission_sim", {"paused_at": None, "paused_total": 0.0, "hold": None}
    )


def _sim_clock(ctx: RequestCtx) -> float:
    """Simulation time: wall clock minus every paused interval."""
    sim = _sim(ctx)
    paused = sim.get("paused_total", 0.0)
    if sim.get("paused_at") is not None:
        paused += _now() - sim["paused_at"]
    return _now() - paused


def _is_paused(ctx: RequestCtx) -> bool:
    return _sim(ctx).get("paused_at") is not None


def _pause_sim(ctx: RequestCtx, hold: str) -> None:
    """Freeze the sim clock. hold is 'pause' (state 4) or 'manual' (state 11);
    a robot under manual control does not execute missions either."""
    if not _is_paused(ctx):
        ctx.state.merge_singleton("/mission_sim", {"paused_at": _now()})
    ctx.state.merge_singleton("/mission_sim", {"hold": hold})


def _resume_sim(ctx: RequestCtx) -> None:
    sim = _sim(ctx)
    if sim.get("paused_at") is not None:
        ctx.state.merge_singleton(
            "/mission_sim",
            {
                "paused_at": None,
                "paused_total": sim.get("paused_total", 0.0) + (_now() - sim["paused_at"]),
                "hold": None,
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


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))


def _public_entry(entry: dict, start: float, finish: float, clock: float) -> dict:
    """Queue entry as the API returns it. Per the spec, `ordered`, `started`
    and `finished` are 'the date and time when the mission was queued/started/
    finished' — derived from the timeline; not-yet-reached ones are omitted,
    exactly like a real robot leaves them unset until they happen."""
    public = {k: v for k, v in entry.items() if not k.startswith("_")}
    public["state"] = _entry_state(start, finish, clock)
    public["ordered"] = _iso(float(entry.get("_queued_at", start)))
    public.pop("started", None)
    public.pop("finished", None)
    if clock >= start:
        public["started"] = _iso(start)
    if clock >= finish:
        public["finished"] = _iso(finish)
    if public.get("id") is not None:
        public["url"] = f"/v2.0.0/mission_queue/{public['id']}"
    if isinstance(public.get("mission_id"), str) and public["mission_id"]:
        public["mission"] = f"/v2.0.0/missions/{public['mission_id']}"
    return public


def _executed_seconds(ctx: RequestCtx) -> float:
    clock = _sim_clock(ctx)
    return sum(max(0.0, min(clock, finish) - start) for _e, start, finish in _timeline(ctx))


def _mission_snapshot(ctx: RequestCtx) -> dict:
    """Sim-derived /status fields: state, battery, odometry, position."""
    clock = _sim_clock(ctx)
    timeline = _timeline(ctx)
    executed_s = _executed_seconds(ctx)
    battery = max(BATTERY_FLOOR, BATTERY_START - BATTERY_DRAIN_PER_S * executed_s)
    distance = MISSION_SPEED_M_S * executed_s
    paused = _is_paused(ctx)

    fields: dict[str, Any] = {
        "battery_percentage": round(battery, 2),
        "battery_time_remaining": int(battery * BATTERY_SECONDS_PER_PERCENT),
        "moved": round(1204.5 + distance, 2),
        "_distance_m": distance,
    }

    hold_id = 11 if _sim(ctx).get("hold") == "manual" else 4
    executing = next(((e, s, f) for e, s, f in timeline if s <= clock < f), None)
    if executing is not None:
        entry, _start, finish = executing
        remaining = finish - clock
        fields.update(
            {
                "state_id": hold_id if paused else 5,
                "state_text": STATE_CHOICES[hold_id] if paused else "Executing",
                "mission_queue_id": int(entry.get("id", 0)),
                "mission_queue_url": f"/v2.0.0/mission_queue/{entry.get('id')}",
                "mission_text": f"Executing mission (queue id {entry.get('id')})...",
                "distance_to_next_target": round(remaining * MISSION_SPEED_M_S, 2),
            }
        )
    elif paused:
        fields.update({"state_id": hold_id, "state_text": STATE_CHOICES[hold_id]})
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
    overlay = ctx.state.singleton("/status", {})
    for key, value in overlay.items():
        if key in doc:
            doc[key] = value

    snapshot = _mission_snapshot(ctx)
    distance = snapshot.pop("_distance_m")
    for key, value in snapshot.items():
        if key in doc:
            doc[key] = value
    if "uptime" in doc:
        clock = _sim_clock(ctx)
        boot = ctx.state.singleton("/boot", {"at": clock - UPTIME_START_S})
        doc["uptime"] = int(clock - boot["at"])
    if isinstance(doc.get("position"), dict):
        position = dict(doc["position"])
        # Patrol a straight out-and-back segment of the loop. A position set
        # via PUT /status relocalizes the robot: it becomes the loop's home
        # and the patrol offset restarts from the odometry at that moment.
        user_pos = overlay.get("position")
        if not isinstance(user_pos, dict):
            user_pos = {}
        home_x = float(user_pos.get("x", POSITION_HOME_X))
        home_y = float(user_pos.get("y", POSITION_HOME_Y))
        along_loop = (distance - float(overlay.get("_position_anchor_m", 0.0))) % LOOP_LENGTH_M
        half = LOOP_LENGTH_M / 2
        offset = along_loop if along_loop <= half else LOOP_LENGTH_M - along_loop
        orientation = 0.0 if along_loop <= half else 180.0
        if along_loop == 0.0 and "orientation" in user_pos:
            orientation = float(user_pos["orientation"])
        position.update({"x": round(home_x + offset, 2), "y": home_y, "orientation": orientation})
        doc["position"] = position
    if isinstance(doc.get("velocity"), dict):
        moving = snapshot.get("state_text") == "Executing"
        velocity = dict(doc["velocity"])
        if "linear" in velocity:
            velocity["linear"] = MISSION_SPEED_M_S if moving else 0.0
        if "angular" in velocity:
            velocity["angular"] = 0.0
        doc["velocity"] = velocity

    # Injected faults are the outermost layer: they win over user PUTs and
    # the simulation, exactly like a physical fault would.
    active = active_faults(ctx.state)
    if active:
        if "errors" in doc:
            doc["errors"] = [dict(FAULTS[name]["error"]) for name in active]
        for name in active:
            fault = FAULTS[name]
            if "battery" in fault:
                if "battery_percentage" in doc:
                    doc["battery_percentage"] = fault["battery"]
                if "battery_time_remaining" in doc:
                    doc["battery_time_remaining"] = int(
                        fault["battery"] * BATTERY_SECONDS_PER_PERCENT
                    )
        state_fault = next((FAULTS[n] for n in active if "state_id" in FAULTS[n]), None)
        if state_fault is not None:
            if "state_id" in doc:
                doc["state_id"] = state_fault["state_id"]
            if "state_text" in doc:
                doc["state_text"] = state_fault["state_text"]
            if "velocity" in doc and isinstance(doc["velocity"], dict):
                doc["velocity"] = {k: 0.0 for k in doc["velocity"]}
    return doc


async def get_status(ctx: RequestCtx) -> tuple[int, Any]:
    return ctx.op.success_status, _status_doc(ctx)


def _argument_error(human: str) -> tuple[int, Any]:
    return 400, {"error_code": "400", "error_human": f"Argument error: {human}"}


async def put_status(ctx: RequestCtx) -> tuple[int, Any]:
    """Apply exactly the fields the spec's PutStatus document declares — a
    real robot ignores everything else. MiR semantics per the spec: state_id
    4 pauses the robot, 11 puts it in manual control, 3 sets it Ready again;
    pausing (or taking manual control) freezes the simulation clock, so a
    held mission keeps its progress and resumes exactly where it stopped.
    `name` renames the robot; `position` relocalizes it (see _status_doc)."""
    body = ctx.body if isinstance(ctx.body, dict) else {}

    state_id = body.get("state_id")
    if state_id is not None and state_id not in STATE_CHOICES:
        return _argument_error(f"state_id choices are {sorted(STATE_CHOICES)}")
    mode_id = body.get("mode_id")
    if mode_id is not None and mode_id not in MODE_CHOICES:
        return _argument_error(f"mode_id choices are {sorted(MODE_CHOICES)}")
    if "clear_error" in body and body["clear_error"] is not True:
        return _argument_error("clear_error choices are {True}")

    if state_id == 4:
        _pause_sim(ctx, "pause")
    elif state_id == 11:
        _pause_sim(ctx, "manual")
    elif state_id == 3:
        _resume_sim(ctx)

    patch: dict[str, Any] = {}
    for put_field, status_field in (
        ("name", "robot_name"),
        ("serial_number", "serial_number"),
        ("map_id", "map_id"),
    ):
        if isinstance(body.get(put_field), str):
            patch[status_field] = body[put_field]
    if mode_id is not None:
        patch["mode_id"] = mode_id
        patch["mode_text"] = MODE_CHOICES[mode_id]
    if isinstance(body.get("position"), dict):
        position = {
            k: float(v)
            for k, v in body["position"].items()
            if k in ("x", "y", "orientation")
            and isinstance(v, int | float)
            and not isinstance(v, bool)
        }
        if position:
            patch["position"] = position
            patch["_position_anchor_m"] = _executed_seconds(ctx) * MISSION_SPEED_M_S
    if body.get("clear_error") is True:
        patch["errors"] = []
        # A real robot's error reset clears resettable faults; an emergency
        # stop cannot be cleared from the API.
        remaining = [n for n in active_faults(ctx.state) if not FAULTS[n].get("resettable")]
        set_faults(ctx.state, remaining)
    if patch:
        ctx.state.merge_singleton("/status", patch)
    return ctx.op.success_status, _status_doc(ctx)


def _mission_exists(ctx: RequestCtx, mission_id: Any) -> bool:
    """True when mission_id names a mission on this robot (seeded or created)."""
    if not isinstance(mission_id, str) or not mission_id:
        return False
    if ctx.seed_collection is not None:
        ctx.seed_collection("/missions", "/missions")
    missions = ctx.state.collection("/missions")
    return mission_id in missions or any(
        item.get("guid") == mission_id for item in missions.values()
    )


async def post_mission_queue(ctx: RequestCtx) -> tuple[int, Any]:
    # A real robot rejects queueing a mission it does not know (400 is the
    # declared "Bad request or Argument error" response for this operation).
    mission_id = ctx.body.get("mission_id") if isinstance(ctx.body, dict) else None
    if not _mission_exists(ctx, mission_id):
        return _argument_error("mission_id does not match an existing mission")
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
        if entry.get("id") != queue_id:
            continue
        public = _public_entry(entry, start, finish, clock)
        # The item endpoint returns the full GetMission_queue document (the
        # list uses the slim {id, state, url} shape) — flesh it out from the
        # declared schema, then overlay the live lifecycle fields.
        full = example_from_schema(ctx.spec.deref(ctx.op.success_schema))
        if not isinstance(full, dict) or not full:
            return ctx.op.success_status, public
        overlay_compatible(full, public)
        for key in ("started", "finished"):
            if key in public:
                full[key] = public[key]
            else:
                full.pop(key, None)
        return ctx.op.success_status, full
    return 404, {"error_code": "404", "error_human": "Not found"}


async def put_mission_queue_item(ctx: RequestCtx) -> tuple[int, Any]:
    """PutMission_queue declares cmd, mission_id, priority. Metadata updates
    apply to the stored entry; the generic handler would instead seed a
    phantom entry into a queue that never held one."""
    try:
        queue_id = int(next(iter(ctx.path_params.values())))
    except (StopIteration, TypeError, ValueError):
        return 404, {"error_code": "404", "error_human": "Not found"}
    if ctx.state.get("/mission_queue", str(queue_id)) is None:
        return 404, {"error_code": "404", "error_human": "Not found"}
    body = ctx.body if isinstance(ctx.body, dict) else {}
    patch = {k: body[k] for k in ("mission_id", "priority") if k in body}
    if "mission_id" in patch and not _mission_exists(ctx, patch["mission_id"]):
        return _argument_error("mission_id does not match an existing mission")
    if patch:
        ctx.state.update("/mission_queue", str(queue_id), patch)
    return await get_mission_queue_item(ctx)


async def delete_mission_queue(ctx: RequestCtx) -> tuple[int, Any]:
    ctx.state.clear("/mission_queue")
    ctx.state.merge_singleton("/status", {"mission_queue_id": 0, "mission_text": ""})
    return ctx.op.success_status, None


async def delete_mission_queue_item(ctx: RequestCtx) -> tuple[int, Any]:
    """Remove one queue entry — never executes (if pending) or stops on the
    spot (if executing); the FIFO timeline recomputes without it. The generic
    handler would seed an example entry first, which a real queue never has."""
    try:
        queue_id = int(next(iter(ctx.path_params.values())))
    except (StopIteration, TypeError, ValueError):
        return 404, {"error_code": "404", "error_human": "Not found"}
    if not ctx.state.delete("/mission_queue", str(queue_id)):
        return 404, {"error_code": "404", "error_human": "Not found"}
    return ctx.op.success_status, None if ctx.op.success_status == 204 else {}


def _register_doc(ctx: RequestCtx, register_id: int) -> dict:
    register = ctx.state.singleton("/registers", {}).get(str(register_id), {})
    return {
        "id": register_id,
        "label": str(register.get("label", "")),
        "url": f"/v2.0.0/registers/{register_id}",
        "value": float(register.get("value", 0.0)),
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
    """PUT and POST /registers/{id}: the spec's PostRegister body is
    {value: number, label: string}, both optional — writes merge."""
    register_id = _register_id(ctx)
    if register_id is None:
        return 404, {"error_code": "404", "error_human": "register not found"}
    body = ctx.body if isinstance(ctx.body, dict) else {}
    patch: dict[str, Any] = {}
    if "value" in body:
        if not isinstance(body["value"], int | float) or isinstance(body["value"], bool):
            return _argument_error("value must be a number")
        patch["value"] = float(body["value"])
    if "label" in body:
        if not isinstance(body["label"], str):
            return _argument_error("label must be a string")
        patch["label"] = body["label"]
    if patch:
        current = ctx.state.singleton("/registers", {}).get(str(register_id), {})
        ctx.state.merge_singleton("/registers", {str(register_id): {**current, **patch}})
    return ctx.op.success_status, _register_doc(ctx, register_id)


async def get_metrics(ctx: RequestCtx) -> tuple[int, Any, str]:
    snapshot = _mission_snapshot(ctx)
    clock = _sim_clock(ctx)
    boot = ctx.state.singleton("/boot", {"at": clock - UPTIME_START_S})
    text = (
        "# TYPE mir_robot_battery_percent gauge\n"
        f"mir_robot_battery_percent {snapshot['battery_percentage']}\n"
        "# TYPE mir_robot_uptime_seconds counter\n"
        f"mir_robot_uptime_seconds_total {int(clock - boot['at'])}\n"
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
    ("PUT", "/mission_queue/{id}"): put_mission_queue_item,
    ("DELETE", "/mission_queue"): delete_mission_queue,
    ("DELETE", "/mission_queue/{id}"): delete_mission_queue_item,
    ("GET", "/registers"): get_registers,
    ("GET", "/registers/{id}"): get_register,
    ("PUT", "/registers/{id}"): put_register,
    ("POST", "/registers/{id}"): put_register,
    ("GET", "/metrics"): get_metrics,
}
