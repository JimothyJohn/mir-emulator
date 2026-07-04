"""MiR Fleet Enterprise Integration API emulator.

The fleet emulator embeds a set of robot emulators and controls them the way
a real MiR Fleet does: over the robots' own REST API. Every robot call goes
through the embedded robot app's complete HTTP stack — MiR Basic auth, body
validation, behavior overlays, mission simulation — via an in-process ASGI
transport, so the fleet emulator and the robot emulators genuinely test each
other; nothing reaches into another emulator's state directly.

Auth mirrors Fleet Enterprise: an ``x-api-key`` header (default key
``distributor``, same memorable default as the robot account).

Sessions compose: the ``X-MiR-Session`` header the robot emulator honors is
honored here too and forwarded on every embedded robot call, so one session
id yields an isolated fleet AND an isolated set of robots.

Order lifecycle is fully derived: a serial order's phases are enqueued on the
chosen robot's /mission_queue, and order status is read back from the robot's
mission simulation at request time (Pending → Executing → Finished), so fleet
and robot views can never disagree.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mir_emulator import auth, registry
from mir_emulator.app import (
    Emulator,
    _error_body,
    _SecurityHeadersMiddleware,
    _starlette_path,
    create_app,
)
from mir_emulator.behaviors import MISSION_DURATION_S, RequestCtx
from mir_emulator.examples import example_from_schema, overlay_compatible
from mir_emulator.spec import Operation, load_spec

DEFAULT_API_KEY = "distributor"
API_KEY_HEADER = "x-api-key"

# Robot mission-queue state → fleet order-status (both are spec-stated enums).
ORDER_STATUS_BY_ROBOT_STATE = {
    "Pending": "Pending",
    "Executing": "Executing",
    "Done": "Finished",
}


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


@dataclass(frozen=True)
class EmbeddedRobot:
    robot_id: str  # fleet-side uuid, stable per robot slot
    serial: str
    ip: str
    mir_version: str
    default_name: str
    app: Starlette  # a complete robot emulator (auth, validation, simulation)


def _build_robots(
    robot_versions: Sequence[str],
    *,
    username: str,
    password: str,
    seed: bool,
    mission_duration: float,
) -> list[EmbeddedRobot]:
    robots = []
    for i, version in enumerate(robot_versions, start=1):
        app = create_app(
            version,
            username=username,
            password=password,
            seed=seed,
            mission_duration=mission_duration,
        )
        robots.append(
            EmbeddedRobot(
                robot_id=f"00000000-0000-4000-8000-{i:012d}",
                serial=f"{i:015d}",
                ip=f"192.168.12.{19 + i}",
                mir_version=app.state.mir_version,
                default_name=f"MiR_Emulated_{i}",
                app=app,
            )
        )
    return robots


class FleetEmulator(Emulator):
    """The fleet: x-api-key auth, robot passthrough, order dispatch."""

    def __init__(
        self,
        spec,
        *,
        robots: list[EmbeddedRobot],
        fleet_version: str,
        api_key: str = DEFAULT_API_KEY,
        enforce_auth: bool = True,
        username: str = auth.DEFAULT_USERNAME,
        password: str = auth.DEFAULT_PASSWORD,
        seed: bool = True,
        mission_duration: float = MISSION_DURATION_S,
    ) -> None:
        # username/password here are the credentials the FLEET uses to talk to
        # its robots, exactly like a real Fleet holds robot credentials.
        super().__init__(
            spec,
            enforce_auth=enforce_auth,
            username=username,
            password=password,
            seed=seed,
            mission_duration=mission_duration,
        )
        self.fleet_version = fleet_version
        self.api_key = api_key
        self.robots = robots
        self._robot_token = auth.expected_token(username, password)
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._overrides = {
            ("GET", "/api/v1/robots"): self._get_robots,
            ("GET", "/api/v1/robots/{id}"): self._get_robot,
            ("PATCH", "/api/v1/robots/{id}"): self._patch_robot,
            ("POST", "/api/v1/serial-order"): self._post_serial_order,
            ("GET", "/api/v1/serial-order/{id}"): self._get_serial_order,
            ("DELETE", "/api/v1/serial-order/{id}"): self._delete_serial_order,
            ("GET", "/api/v1/order"): self._get_orders,
            ("GET", "/api/v1/order/{id}"): self._get_order,
            ("GET", "/api/v1/site/mission"): self._get_site_missions,
            ("GET", "/api/v1/system/version"): self._get_version,
        }

    # ---- auth & dispatch plumbing -------------------------------------------

    def _authorized(self, request: Request) -> bool:
        if not self.enforce_auth:
            return True
        supplied = request.headers.get(API_KEY_HEADER, "")
        return bool(supplied) and hmac.compare_digest(supplied, self.api_key)

    def _override_for(self, op: Operation):
        return self._overrides.get((op.method, op.path))

    # ---- talking to the embedded robots (over their real REST API) ----------

    def _client_for(self, robot: EmbeddedRobot) -> httpx.AsyncClient:
        client = self._clients.get(robot.robot_id)
        if client is None:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=robot.app),
                base_url=f"http://{robot.ip}",
                timeout=10.0,
            )
            self._clients[robot.robot_id] = client
        return client

    async def _robot_call(
        self,
        robot: EmbeddedRobot,
        method: str,
        path: str,
        session_id: str,
        json_body: Any = None,
    ) -> tuple[int, Any]:
        base = robot.app.state.emulator.spec.base_path
        headers = {"Authorization": f"Basic {self._robot_token}"}
        if session_id:
            headers["X-MiR-Session"] = session_id
        response = await self._client_for(robot).request(
            method, base + path, headers=headers, json=json_body
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload

    def _robot_by_id(self, robot_id: str | None) -> EmbeddedRobot | None:
        return next((r for r in self.robots if r.robot_id == robot_id), None)

    def _next_robot(self, ctx: RequestCtx) -> EmbeddedRobot:
        """Round-robin assignment, per fleet state (so per session)."""
        n = int(ctx.state.singleton("/fleet_rr", {"n": 0}).get("n", 0))
        ctx.state.merge_singleton("/fleet_rr", {"n": n + 1})
        return self.robots[n % len(self.robots)]

    # ---- robots --------------------------------------------------------------

    async def _identity(self, robot: EmbeddedRobot, session_id: str) -> dict:
        code, status = await self._robot_call(robot, "GET", "/status", session_id)
        name, model = robot.default_name, "MiR250"
        if code == 200 and isinstance(status, dict):
            live = str(status.get("robot_name") or "")
            # The robot ships with a shared factory name; per-slot names beat
            # it, but a user rename (PUT /status on the robot) wins outright.
            if live and live != "MiR_Emulated":
                name = live
            model = str(status.get("robot_model") or model)
        return {
            "robot-id": robot.robot_id,
            "serial-number": robot.serial,
            "name": name,
            "model": model,
            "software-version": robot.mir_version,
            "timestamp": _iso_now(),
            "ip": robot.ip,
        }

    async def _get_robots(self, ctx: RequestCtx) -> tuple[int, Any]:
        return 200, {"robots": [await self._identity(r, ctx.session_id) for r in self.robots]}

    async def _get_robot(self, ctx: RequestCtx) -> tuple[int, Any]:
        robot = self._robot_by_id(str(ctx.path_params.get("id")))
        if robot is None:
            return 404, _error_body(404, "Not found")
        doc = example_from_schema(self.spec.deref(ctx.op.success_schema))
        if not isinstance(doc, dict):
            doc = {}
        doc["robot-identity"] = await self._identity(robot, ctx.session_id)
        code, status = await self._robot_call(robot, "GET", "/status", ctx.session_id)
        if code == 200 and isinstance(status, dict):
            runtime = doc.get("runtime-data")
            if isinstance(runtime, dict):
                velocity = status.get("velocity") or {}
                overlay_compatible(
                    runtime,
                    {
                        "battery-percentage": status.get("battery_percentage"),
                        "moved": status.get("moved"),
                        "velocity-linear": velocity.get("linear"),
                        "velocity-angular": velocity.get("angular"),
                        "timestamp": _iso_now(),
                    },
                )
                pose = runtime.get("pose")
                position = status.get("position") or {}
                if isinstance(pose, dict):
                    overlay_compatible(
                        pose,
                        {
                            "x": position.get("x"),
                            "y": position.get("y"),
                            "orientation": position.get("orientation"),
                        },
                    )
            executing = status.get("state_text") == "Executing"
            doc["robot-end-state"] = "Operational" if executing else "Idle"
        patch = ctx.state.singleton(f"/fleet_robot/{robot.robot_id}", {})
        if patch:
            overlay_compatible(doc, patch)
        return 200, doc

    async def _patch_robot(self, ctx: RequestCtx) -> tuple[int, Any]:
        robot = self._robot_by_id(str(ctx.path_params.get("id")))
        if robot is None:
            return 404, _error_body(404, "Not found")
        if isinstance(ctx.body, dict):
            ctx.state.merge_singleton(f"/fleet_robot/{robot.robot_id}", ctx.body)
        status = ctx.op.success_status
        if status == 204:
            return 204, None
        doc = example_from_schema(self.spec.deref(ctx.op.success_schema))
        if isinstance(doc, dict) and isinstance(ctx.body, dict):
            overlay_compatible(doc, ctx.body)
        return status, doc

    # ---- serial orders → robot mission queues --------------------------------

    async def _post_serial_order(self, ctx: RequestCtx) -> tuple[int, Any]:
        body = ctx.body if isinstance(ctx.body, dict) else {}
        order = body.get("serial-order") or {}
        phases = order.get("phases") or []
        if not phases:
            return 400, _error_body(400, "serial-order.phases must not be empty")
        serial_id = str(order.get("id") or ctx.state.next_guid())
        if ctx.state.get("/serial_orders", serial_id) is not None:
            return 409, _error_body(409, "Duplicate serial order id")
        wanted = order.get("robot-id")
        robot = self._robot_by_id(wanted) if wanted else self._next_robot(ctx)
        if robot is None:
            return 400, _error_body(400, "Unknown robot-id")

        order_ids: list[str] = []
        stored_phases: list[dict] = []
        for phase in phases:
            mission_id = phase.get("mission-id")
            code, reply = await self._robot_call(
                robot, "POST", "/mission_queue", ctx.session_id, {"mission_id": mission_id}
            )
            if code >= 400:
                human = ""
                if isinstance(reply, dict):
                    human = str(reply.get("error_human") or "")
                return 400, _error_body(
                    400, f"Robot rejected the phase mission: {human or 'error ' + str(code)}"
                )
            order_id = str(phase.get("order-id") or ctx.state.next_guid())
            ctx.state.insert(
                "/orders",
                order_id,
                {
                    "order-id": order_id,
                    "serial-order-id": serial_id,
                    "mission-id": mission_id,
                    "mission-name": await self._mission_name(robot, mission_id, ctx.session_id),
                    "queue-id": reply.get("id") if isinstance(reply, dict) else None,
                    "robot-id": robot.robot_id,
                    "priority": order.get("priority") or "Medium",
                    "created": _iso_now(),
                },
            )
            order_ids.append(order_id)
            stored_phases.append({**phase, "order-id": order_id})

        ctx.state.insert(
            "/serial_orders",
            serial_id,
            {
                "id": serial_id,
                "priority": order.get("priority") or "Medium",
                "robot-id": robot.robot_id,
                "phases": stored_phases,
                "orders": order_ids,
            },
        )
        return 201, {"id": serial_id}

    async def _mission_name(self, robot: EmbeddedRobot, mission_id: Any, session_id: str) -> str:
        code, mission = await self._robot_call(robot, "GET", f"/missions/{mission_id}", session_id)
        if code == 200 and isinstance(mission, dict):
            return str(mission.get("name") or "emulated")
        return "emulated"

    async def _get_serial_order(self, ctx: RequestCtx) -> tuple[int, Any]:
        rec = ctx.state.get("/serial_orders", str(ctx.path_params.get("id")))
        if rec is None:
            return 404, _error_body(404, "Not found")
        # The response schema allows exactly the serial-order fields.
        return 200, {
            "id": rec["id"],
            "priority": rec.get("priority", "Medium"),
            "robot-id": rec.get("robot-id"),
            "phases": [
                {k: v for k, v in phase.items() if k != "order-id"}
                | {"order-id": phase.get("order-id")}
                for phase in rec.get("phases", [])
            ],
        }

    async def _delete_serial_order(self, ctx: RequestCtx) -> tuple[int, Any]:
        serial_id = str(ctx.path_params.get("id"))
        rec = ctx.state.get("/serial_orders", serial_id)
        if rec is None:
            return 404, _error_body(404, "Not found")
        robot = self._robot_by_id(rec.get("robot-id"))
        for order_id in rec.get("orders", []):
            order = ctx.state.get("/orders", order_id)
            if not order:
                continue
            if robot is not None and order.get("queue-id") is not None:
                code, entry = await self._robot_call(
                    robot, "GET", f"/mission_queue/{order['queue-id']}", ctx.session_id
                )
                # Finished missions stay finished; abort the rest on the robot
                # by removing the queue entry, like a real fleet cancel does.
                if code == 200 and isinstance(entry, dict) and entry.get("state") != "Done":
                    await self._robot_call(
                        robot, "DELETE", f"/mission_queue/{order['queue-id']}", ctx.session_id
                    )
                    ctx.state.update("/orders", order_id, {"aborted": True})
        return ctx.op.success_status, None if ctx.op.success_status == 204 else {}

    # ---- the order read-model, derived live from robot state -----------------

    async def _order_view(self, ctx: RequestCtx, rec: dict) -> dict:
        robot = self._robot_by_id(rec.get("robot-id"))
        entry: dict | None = None
        if robot is not None and rec.get("queue-id") is not None:
            code, payload = await self._robot_call(
                robot, "GET", f"/mission_queue/{rec['queue-id']}", ctx.session_id
            )
            if code == 200 and isinstance(payload, dict):
                entry = payload
        if rec.get("aborted"):
            status = "Aborted"
        elif entry is not None:
            status = ORDER_STATUS_BY_ROBOT_STATE.get(str(entry.get("state")), "Waiting")
        else:
            status = "Waiting"
        order = {
            "order-id": rec["order-id"],
            "order-type": "User",
            "mission-id": rec.get("mission-id"),
            "mission-name": rec.get("mission-name", "emulated"),
            "order-status": status,
            "order-created": rec.get("created", _iso_now()),
            "robot-id": rec.get("robot-id"),
            "robot-name": robot.default_name if robot else None,
            "order-priority": rec.get("priority", "Medium"),
        }
        if entry is not None:
            if entry.get("ordered"):
                order["order-queued"] = entry["ordered"]
            if entry.get("started"):
                order["order-started"] = entry["started"]
            if entry.get("finished"):
                order["order-finished"] = entry["finished"]
        return order

    async def _get_orders(self, ctx: RequestCtx) -> tuple[int, Any]:
        records = list(ctx.state.collection("/orders").values())
        return 200, [await self._order_view(ctx, rec) for rec in records]

    async def _get_order(self, ctx: RequestCtx) -> tuple[int, Any]:
        rec = ctx.state.get("/orders", str(ctx.path_params.get("id")))
        if rec is None:
            return 404, _error_body(404, "Not found")
        return 200, await self._order_view(ctx, rec)

    # ---- site & system --------------------------------------------------------

    async def _get_site_missions(self, ctx: RequestCtx) -> tuple[int, Any]:
        """The fleet's site missions are the union of its robots' missions —
        exactly the ids a serial order phase can dispatch."""
        seen: dict[str, dict] = {}
        for robot in self.robots:
            code, missions = await self._robot_call(robot, "GET", "/missions", ctx.session_id)
            if code == 200 and isinstance(missions, list):
                for mission in missions:
                    if isinstance(mission, dict) and mission.get("guid"):
                        guid = str(mission["guid"])
                        seen.setdefault(
                            guid, {"id": guid, "name": str(mission.get("name") or "emulated")}
                        )
        return 200, {"missions": list(seen.values())}

    async def _get_version(self, ctx: RequestCtx) -> tuple[int, Any]:
        return 200, {"version": self.fleet_version, "fleet-name": "MiR_Fleet_Emulated"}


def create_fleet_app(
    fleet_version: str | None = None,
    *,
    robot_versions: Sequence[str] | None = None,
    api_key: str = DEFAULT_API_KEY,
    enforce_auth: bool = True,
    robot_username: str = auth.DEFAULT_USERNAME,
    robot_password: str = auth.DEFAULT_PASSWORD,
    seed: bool = True,
    cors: bool = False,
    mission_duration: float = MISSION_DURATION_S,
) -> Starlette:
    version, path = registry.fleet_spec_path(fleet_version)
    spec = load_spec(path, version)
    if robot_versions is None:
        latest_robot = registry.supported_versions()[0]
        robot_versions = (latest_robot, latest_robot)
    robots = _build_robots(
        robot_versions,
        username=robot_username,
        password=robot_password,
        seed=seed,
        mission_duration=mission_duration,
    )
    emulator = FleetEmulator(
        spec,
        robots=robots,
        fleet_version=version,
        api_key=api_key,
        enforce_auth=enforce_auth,
        username=robot_username,
        password=robot_password,
        seed=seed,
        mission_duration=mission_duration,
    )

    routes = [
        Route(
            spec.base_path + _starlette_path(op),
            emulator.handler_for(op),
            methods=[op.method],
        )
        for op in spec.operations.values()
    ]

    raw_doc = spec.document

    async def spec_json(_request: Request) -> JSONResponse:
        return JSONResponse(raw_doc)

    entry = registry.fleet_tracked_entry(version)
    docs_url = registry.fleet_registry().get("docs_url_template", "").replace("{version}", version)

    async def index(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "mir-emulator-fleet",
                "emulated_fleet_version": version,
                "api_title": spec.title,
                "base_path": "/api/v1",
                "operations": len(spec.operations),
                "auth": f"{API_KEY_HEADER} header (default key {DEFAULT_API_KEY!r})"
                if enforce_auth
                else "disabled",
                "robots": [
                    {
                        "robot-id": r.robot_id,
                        "name": r.default_name,
                        "software-version": r.mir_version,
                        "ip": r.ip,
                    }
                    for r in robots
                ],
                "sessions": (
                    "Send X-MiR-Session: <1-64 chars of A-Za-z0-9._-> for an isolated "
                    "fleet with its own isolated robots; omit it for the shared fleet"
                ),
                "specs": {"openapi3": "/openapi.json", "swagger2": None},
                "official_docs": docs_url,
                "source": {
                    "primary_source": registry.fleet_registry().get("source"),
                    "provenance": entry["provenance"],
                    "source_url": entry.get("source_url"),
                    "source_sha256": entry.get("source_sha256"),
                    "spec_sha256": entry["sha256"],
                    "official": entry["official"],
                },
            }
        )

    async def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(_error_body(404, "Not found"), status_code=404)

    middleware = [Middleware(_SecurityHeadersMiddleware)]
    if cors:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
        )

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/openapi.json", spec_json),
            Route("/swagger.json", spec_json),  # parity with the robot apps
            *routes,
        ],
        exception_handlers={404: not_found},
        middleware=middleware,
    )
    app.state.emulator = emulator
    app.state.fleet_version = version
    return app
