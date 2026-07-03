"""Hand-written behavior overlays for endpoints where generic CRUD is too dumb.

Everything not listed here falls back to the spec-driven generic handler in
app.py. Overrides receive a RequestCtx and return (status, body) — or
(status, body, media_type) for non-JSON endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mir_emulator.examples import example_from_schema, overlay_compatible
from mir_emulator.spec import Operation, Spec
from mir_emulator.state import StateStore

REGISTER_COUNT = 200

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


def _status_doc(ctx: RequestCtx) -> dict:
    op = ctx.spec.operations.get(("GET", "/status"), ctx.op)
    base = example_from_schema(ctx.spec.deref(op.success_schema))
    if isinstance(base, dict):
        overlay_compatible(base, STATUS_OVERLAY)
    overlay = ctx.state.singleton("/status", {})
    doc = dict(base) if isinstance(base, dict) else {}
    for key, value in overlay.items():
        if key in doc:
            doc[key] = value
    return doc


async def get_status(ctx: RequestCtx) -> tuple[int, Any]:
    return ctx.op.success_status, _status_doc(ctx)


async def put_status(ctx: RequestCtx) -> tuple[int, Any]:
    if isinstance(ctx.body, dict):
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
    entry["state"] = "Pending"
    ctx.state.insert("/mission_queue", str(queue_id), entry)
    ctx.state.merge_singleton("/status", {"mission_queue_id": queue_id})
    return ctx.op.success_status, entry


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
    text = (
        "# TYPE mir_robot_battery_percent gauge\n"
        "mir_robot_battery_percent 92.5\n"
        "# TYPE mir_robot_uptime_seconds counter\n"
        "mir_robot_uptime_seconds_total 3600\n"
        "# EOF\n"
    )
    return ctx.op.success_status, text, "text/plain; version=0.0.4"


OVERRIDES: dict[tuple[str, str], Any] = {
    ("GET", "/status"): get_status,
    ("PUT", "/status"): put_status,
    ("POST", "/mission_queue"): post_mission_queue,
    ("DELETE", "/mission_queue"): delete_mission_queue,
    ("GET", "/registers"): get_registers,
    ("GET", "/registers/{id}"): get_register,
    ("PUT", "/registers/{id}"): put_register,
    ("GET", "/metrics"): get_metrics,
}
