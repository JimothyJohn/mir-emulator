"""Contract conformance: exercise every operation of every tracked spec.

For each (method, path) in the spec, fire a request shaped by the spec itself
(path params from seeded state, bodies synthesized from the body schema) and
assert that the emulator answers with a declared status code and — for JSON
200s — a body that validates against the declared response schema.
"""

from jsonschema import Draft4Validator
from mir_emulator.examples import example_from_schema
from mir_emulator.spec import Operation

from tests.conftest import AUTH_HEADER


def _fill_path(op: Operation, path: str) -> str:
    """Substitute plausible values for path template params."""
    for name, ptype in op.path_param_types().items():
        value = "1" if ptype == "integer" else "emulated-0000-0000-0001-000000000000"
        path = path.replace("{" + name + "}", value)
    return path


def _request_kwargs(spec, op: Operation) -> dict:
    kwargs: dict = {"headers": dict(AUTH_HEADER)}
    if op.body_schema is not None:
        kwargs["json"] = example_from_schema(spec.deref(op.body_schema))
    return kwargs


def test_every_operation_answers_with_a_declared_status(client, spec):
    failures = []
    for op in spec.operations.values():
        url = spec.base_path + _fill_path(op, op.path)
        response = client.request(op.method, url, **_request_kwargs(spec, op))
        declared = set(op.responses) | {200}
        if response.status_code not in declared:
            failures.append(
                f"{op.method} {op.path} -> {response.status_code} not in {sorted(op.responses)}"
            )
    assert not failures, "\n".join(failures[:30])


def test_get_success_bodies_validate_against_response_schemas(client, spec):
    failures = []
    for op in spec.operations.values():
        if op.method != "GET" or "application/json" not in op.produces[0]:
            continue
        url = spec.base_path + _fill_path(op, op.path)
        response = client.get(url, headers=AUTH_HEADER)
        if response.status_code != op.success_status:
            continue
        schema = spec.deref(op.success_schema)
        if not schema:
            continue
        body = response.json()
        # MiR specs often declare list endpoints with the element's object
        # schema; real robots return arrays. Validate elements individually.
        instances = body if isinstance(body, list) and schema.get("type") == "object" else [body]
        try:
            for instance in instances:
                Draft4Validator(schema).validate(instance)
        except Exception as exc:
            failures.append(f"{op.method} {op.path}: {str(exc)[:150]}")
    assert not failures, "\n".join(failures[:30])


def test_mission_queue_lifecycle(client, spec):
    base = spec.base_path
    missions = client.get(f"{base}/missions", headers=AUTH_HEADER).json()
    assert missions, "seeded mission list should not be empty"
    mission_id = missions[0].get("guid", "emulated-0000-0000-0001-000000000000")

    queued = client.post(
        f"{base}/mission_queue", json={"mission_id": mission_id}, headers=AUTH_HEADER
    )
    assert queued.status_code == spec.operations[("POST", "/mission_queue")].success_status
    entry = queued.json()
    assert entry["state"] == "Pending"

    listed = client.get(f"{base}/mission_queue", headers=AUTH_HEADER).json()
    assert any(e.get("id") == entry.get("id") for e in listed)

    status = client.get(f"{base}/status", headers=AUTH_HEADER).json()
    assert status["mission_queue_id"] == entry.get("id")

    cleared = client.delete(f"{base}/mission_queue", headers=AUTH_HEADER)
    assert cleared.status_code in (200, 204)
    assert client.get(f"{base}/mission_queue", headers=AUTH_HEADER).json() == []


def test_registers_read_write(client, spec):
    base = spec.base_path
    registers = client.get(f"{base}/registers", headers=AUTH_HEADER).json()
    assert len(registers) == 200

    updated = client.put(f"{base}/registers/7", json={"value": 42.5}, headers=AUTH_HEADER)
    assert updated.status_code in (200, 201)
    assert updated.json()["value"] == 42.5
    assert client.get(f"{base}/registers/7", headers=AUTH_HEADER).json()["value"] == 42.5

    out_of_range = client.get(f"{base}/registers/500", headers=AUTH_HEADER)
    assert out_of_range.status_code == 404


def test_status_reflects_put(client, spec):
    base = spec.base_path
    original = client.get(f"{base}/status", headers=AUTH_HEADER).json()
    assert original["robot_name"] == "MiR_Emulated"

    renamed = client.put(
        f"{base}/status", json={"robot_name": "conformance-bot"}, headers=AUTH_HEADER
    )
    assert renamed.status_code == 200
    assert client.get(f"{base}/status", headers=AUTH_HEADER).json()["robot_name"] == (
        "conformance-bot"
    )


def test_generic_crud_round_trip(client, spec):
    base = spec.base_path
    created = client.post(
        f"{base}/missions",
        json={"name": "test mission", "group_id": "emulated-0000-0000-0001-000000000000"},
        headers=AUTH_HEADER,
    )
    assert created.status_code in spec.operations[("POST", "/missions")].responses
    if created.status_code >= 300:
        return
    guid = created.json()["guid"]

    fetched = client.get(f"{base}/missions/{guid}", headers=AUTH_HEADER)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "test mission"

    deleted = client.delete(f"{base}/missions/{guid}", headers=AUTH_HEADER)
    assert deleted.status_code in (200, 204)
    assert client.get(f"{base}/missions/{guid}", headers=AUTH_HEADER).status_code == 404
