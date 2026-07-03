"""Spec-driven Starlette application factory.

Every operation in the loaded MiR spec becomes a route under its base path
(/api/v2.0.0). Behavior overlays (behaviors.py) handle the interesting
stateful endpoints; everything else gets generic, schema-faithful CRUD.
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft4Validator
from jsonschema.exceptions import SchemaError, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mir_emulator import auth, registry
from mir_emulator.behaviors import OVERRIDES, RequestCtx
from mir_emulator.examples import example_from_schema, overlay_compatible
from mir_emulator.openapi3 import to_openapi3
from mir_emulator.spec import Operation, Spec, load_spec
from mir_emulator.state import StateStore

MAX_BODY_BYTES = 2 * 1024 * 1024


def _error_body(status: int, human: str) -> dict:
    return {"error_code": str(status), "error_human": human}


def _respond(status: int, body: Any, media_type: str = "application/json") -> Response:
    if status == 204 or body is None:
        return Response(status_code=status)
    if media_type != "application/json":
        return Response(body, status_code=status, media_type=media_type)
    return JSONResponse(body, status_code=status)


def _starlette_path(op: Operation) -> str:
    path = op.path
    for name, ptype in op.path_param_types().items():
        if ptype == "integer":
            path = path.replace("{" + name + "}", "{" + name + ":int}")
    return path


def _is_item_op(op: Operation) -> bool:
    return op.path.rsplit("/", 1)[-1].startswith("{")


def _storage_key(op: Operation, path_params: dict) -> tuple[str, str | None]:
    """(collection key, item id). Context params are baked into the key so
    e.g. /mission_queue/1/actions and /mission_queue/2/actions don't share."""
    if _is_item_op(op):
        template, last = op.path.rsplit("/", 1)
        id_name = last.strip("{}").split(":")[0]
        item_id = str(path_params.get(id_name, ""))
    else:
        template, id_name, item_id = op.path, None, None
    context = "&".join(f"{k}={v}" for k, v in sorted(path_params.items()) if k != id_name)
    return (f"{template}?{context}" if context else template), item_id


class Emulator:
    def __init__(
        self,
        spec: Spec,
        *,
        enforce_auth: bool = True,
        username: str = auth.DEFAULT_USERNAME,
        password: str = auth.DEFAULT_PASSWORD,
        seed: bool = True,
    ) -> None:
        self.spec = spec
        self.state = StateStore()
        self.enforce_auth = enforce_auth
        self.username = username
        self.password = password
        self.seed = seed

    def _example(self, schema: dict | None) -> Any:
        return example_from_schema(self.spec.deref(schema))

    def _item_schema_for(self, collection_path: str) -> dict | None:
        """Schema of a single element for a collection path.

        MiR spec files frequently declare list endpoints with the element's
        object schema instead of an array — real robots return arrays. Seed
        elements from the declared schema so both shapes stay consistent.
        """
        get_op = self.spec.operations.get(("GET", collection_path))
        if get_op and get_op.success_schema:
            deref = self.spec.deref(get_op.success_schema)
            if deref.get("type") == "array":
                return deref.get("items")
            if deref.get("type") == "object" or "properties" in deref:
                return deref
        for (method, path), op in self.spec.operations.items():
            if method == "GET" and path.startswith(collection_path + "/{") and _is_item_op(op):
                return op.success_schema
        return None

    def _assign_ids(self, item: dict, item_id: str | int) -> None:
        if "guid" in item:
            item["guid"] = str(item_id)
        elif "id" in item and isinstance(item.get("id"), int):
            item["id"] = int(item_id)

    def _seed_collection(self, key: str, collection_path: str) -> None:
        if not self.seed:
            self.state.seed_once(key, [])
            return
        schema = self._item_schema_for(collection_path)
        item = self._example(schema)
        if not isinstance(item, dict):
            self.state.seed_once(key, [])
            return
        if "id" in item and isinstance(item["id"], int):
            item_id: str | int = self.state.next_int_id()
        else:
            item_id = self.state.next_guid()
        self._assign_ids(item, item_id)
        self.state.seed_once(key, [(str(item_id), item)])

    def _validate_body(self, op: Operation, body: Any) -> str | None:
        if body is None or not op.body_schema:
            return None
        schema = self.spec.deref(op.body_schema)
        try:
            Draft4Validator(schema).validate(body)
        except ValidationError as exc:
            # exc.message can embed user input; keep it JSON-encoded and
            # never log it (log injection surface).
            return f"Argument error: {exc.message[:200]}"
        except SchemaError:
            return None  # emulator bug in the spec, not the client's fault
        return None

    def handler_for(self, op: Operation):
        async def handler(request: Request) -> Response:
            return await self.handle(request, op)

        return handler

    async def handle(self, request: Request, op: Operation) -> Response:
        if self.enforce_auth and not auth.is_authorized(
            request.headers.get("authorization"), self.username, self.password
        ):
            return _respond(401, _error_body(401, "Not authorized"))

        body: Any = None
        if op.method in ("POST", "PUT"):
            raw = await request.body()
            if len(raw) > MAX_BODY_BYTES:
                return _respond(400, _error_body(400, "Payload too large"))
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    return _respond(400, _error_body(400, "Invalid JSON"))
            if op.body_required and body is None:
                return _respond(400, _error_body(400, "Missing request body"))
            error = self._validate_body(op, body)
            if error:
                return _respond(400, _error_body(400, error))

        ctx = RequestCtx(
            spec=self.spec,
            state=self.state,
            op=op,
            path_params=dict(request.path_params),
            query=dict(request.query_params),
            body=body,
        )

        override = OVERRIDES.get((op.method, op.path))
        result = await override(ctx) if override else self._generic(ctx)
        status, payload, *rest = result
        return _respond(status, payload, rest[0] if rest else "application/json")

    def _generic(self, ctx: RequestCtx) -> tuple[int, Any]:
        op = ctx.op
        key, item_id = _storage_key(op, ctx.path_params)
        is_item = item_id is not None
        collection_path = op.path.rsplit("/", 1)[0] if is_item else op.path
        has_item_sibling = any(
            path.startswith(op.path + "/{") and _is_item_op(other)
            for (_method, path), other in self.spec.operations.items()
        )

        success_schema = self.spec.deref(op.success_schema)
        is_array_response = success_schema.get("type") == "array"

        if op.method == "GET" and not is_item and (is_array_response or has_item_sibling):
            self._seed_collection(key, collection_path)
            return op.success_status, list(self.state.collection(key).values())

        if op.method == "GET" and is_item:
            self._seed_collection(key, collection_path)
            stored = self.state.get(key, item_id or "")
            if stored is None:
                return 404, _error_body(404, "Not found")
            full = self._example(op.success_schema)
            if isinstance(full, dict) and full:
                # Stored elements are list-shaped; the item schema may type
                # shared fields differently. Only merge compatible values.
                overlay_compatible(full, stored)
                if "guid" in stored:
                    full.setdefault("guid", stored["guid"])
                return op.success_status, full
            return op.success_status, stored

        if op.method == "GET":
            # Singleton document (e.g. /system/info).
            doc = self._example(op.success_schema)
            if isinstance(doc, dict):
                overlay = self.state.singleton(op.path, {})
                doc.update({k: v for k, v in overlay.items() if k in doc})
            return op.success_status, doc

        if op.method == "POST" and (is_array_response or has_item_sibling or not is_item):
            item = self._example(op.success_schema)
            if not isinstance(item, dict):
                return op.success_status, item
            if isinstance(ctx.body, dict):
                item.update(ctx.body)
            if "id" in item and isinstance(item.get("id"), int):
                new_id: str | int = self.state.next_int_id()
            else:
                new_id = self.state.next_guid()
            self._assign_ids(item, new_id)
            # Store a list-shaped element so GET-collection responses keep
            # validating even when the POST response schema types fields
            # differently (a real inconsistency in MiR's files).
            element_schema = self._item_schema_for(collection_path)
            element = self._example(element_schema) if element_schema else None
            if isinstance(element, dict) and element:
                overlay_compatible(element, item)
                self._assign_ids(element, new_id)
                stored = element
            else:
                stored = item
            self.state.insert(key, str(item.get("guid", new_id)), stored)
            return op.success_status, item

        if op.method == "PUT" and is_item:
            self._seed_collection(key, collection_path)
            existing = self.state.get(key, item_id or "")
            if existing is None:
                return 404, _error_body(404, "Not found")
            patch = ctx.body if isinstance(ctx.body, dict) else {}
            updated = overlay_compatible(dict(existing), patch)
            self.state.insert(key, item_id or "", updated)
            full = self._example(op.success_schema)
            if isinstance(full, dict) and full:
                overlay_compatible(full, updated)
                return op.success_status, full
            return op.success_status, updated

        if op.method == "PUT":
            # Singleton update (e.g. PUT /wifi/enabled).
            doc = self._example(op.success_schema)
            if isinstance(doc, dict) and isinstance(ctx.body, dict):
                self.state.merge_singleton(op.path, {k: v for k, v in ctx.body.items() if k in doc})
                doc.update(self.state.singleton(op.path, {}))
            return op.success_status, doc

        if op.method == "DELETE" and is_item:
            self._seed_collection(key, collection_path)
            if not self.state.delete(key, item_id or ""):
                return 404, _error_body(404, "Not found")
            return op.success_status, None if op.success_status == 204 else {}

        if op.method == "DELETE":
            self.state.clear(key)
            return op.success_status, None if op.success_status == 204 else {}

        return op.success_status, self._example(op.success_schema)


class _SecurityHeadersMiddleware:
    """nosniff/no-store/deny-frame on every response — the emulator often runs
    on shared CI hosts and dev laptops; don't let responses be cached or
    content-sniffed into something they aren't."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("X-Frame-Options", "DENY")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    mir_version: str | None = None,
    *,
    enforce_auth: bool = True,
    username: str = auth.DEFAULT_USERNAME,
    password: str = auth.DEFAULT_PASSWORD,
    seed: bool = True,
    cors: bool = False,
) -> Starlette:
    version, path = registry.spec_path(mir_version)
    spec = load_spec(path, version)
    emulator = Emulator(
        spec, enforce_auth=enforce_auth, username=username, password=password, seed=seed
    )

    routes = [
        Route(
            spec.base_path + _starlette_path(op),
            emulator.handler_for(op),
            methods=[op.method],
        )
        for op in spec.operations.values()
    ]

    # Serve the API definition itself, both flavors, so SDK generators and
    # contract-testing tools can point straight at the emulator.
    raw_doc = spec.document
    openapi3_doc = to_openapi3(raw_doc) if raw_doc.get("swagger") == "2.0" else raw_doc

    async def swagger_json(_request: Request) -> JSONResponse:
        return JSONResponse(raw_doc)

    async def openapi_json(_request: Request) -> JSONResponse:
        return JSONResponse(openapi3_doc)

    async def index(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "mir-emulator",
                "emulated_mir_version": version,
                "api_title": spec.title,
                "base_path": spec.base_path,
                "operations": len(spec.operations),
                "auth": "Basic BASE64(user:SHA-256(password))" if enforce_auth else "disabled",
                "specs": {"swagger2": "/swagger.json", "openapi3": "/openapi.json"},
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
            Route("/swagger.json", swagger_json),
            Route("/openapi.json", openapi_json),
            *routes,
        ],
        exception_handlers={404: not_found},
        middleware=middleware,
    )
    app.state.emulator = emulator
    app.state.mir_version = version
    return app
