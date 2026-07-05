# MiR API endpoint reference (as served by this emulator)

Everything here is verified against `packages/mir-emulator/src/mir_emulator/`
(`behaviors.py`, `fleet.py`, `app.py`, `auth.py`). The robot API mirrors the
official MiR REST API; paths under `/_emulator/*` and `X-MiR-*` headers are
emulator-only test surfaces that do not exist on real hardware.

## Version discovery (connect-time handshake)

Path versions never change (`/api/v2.0.0` robot, `/api/v1` fleet); the
*software* version decides which endpoints and fields exist. Learn it from
the target instead of configuring it — probe in order, first hit wins:

| Probe | Identifies | Version field |
|---|---|---|
| `GET /healthz` | multi-version dispatcher (`"kind": "dispatcher"`) | `versions[]`, `latest`, `fleet_versions[]`, `fleet_latest` |
| `GET /` (JSON) | emulator robot or fleet (`"kind"`) | `emulated_mir_version` / `emulated_fleet_version` |
| `GET /api/v1/system/version` (`x-api-key`) | any MiR Fleet, real included | `version` |
| `GET /swagger.json` | robot serving its spec | `info.version` |
| `GET /api/v2.0.0/status` → 200/401 | robot that won't say | none — unknown |

Against a dispatcher, prefix every call with `/<version>` (robot) or
`/fleet/<version>` (fleet); `latest` aliases the newest. Implementations of
this handshake: `mir_client.discovery` (SDK), `mir_mcp.client.detect_target`
(MCP), `connectTo()` in `docs/index.html` (console).

## Robot API — `/api/v2.0.0`

Auth header: `Authorization: Basic BASE64(<user>:<sha256-hex(password)>)`.
Every operation in the loaded spec is served (289 ops in 3.8.1); the ones
below carry real state. Everything else returns deterministic,
schema-valid examples with generic CRUD semantics.

### GET /status
Key fields: `state_id`/`state_text` (3 Ready, 4 Pause, 5 Executing,
11 Manual control; fault states come from injected faults),
`battery_percentage`, `battery_time_remaining`, `position {x, y,
orientation}`, `velocity`, `mission_text`, `mission_queue_id`, `errors[]`,
`robot_name`, `serial_number`, `uptime`, `distance_to_next_target`,
`moved` (odometry meters). While a mission executes, position patrols a
deterministic out-and-back loop and velocity is non-zero.

### PUT /status
Merges exactly the writable fields; a real robot ignores the rest.
- `state_id`: 3 = Ready (resumes a held mission exactly where it stopped),
  4 = Pause (freezes the mission simulation), 11 = Manual control (also
  freezes). Anything else → 400 with the valid choices.
- `clear_error: true` — empties `errors` and clears *resettable* injected
  faults (an emergency stop cannot be cleared via the API, matching MiR).
- `name`, `serial_number`, `map_id` — string updates.
- `mode_id` — validated against the spec's mode choices.
- `position {x, y, orientation}` — relocalizes; the patrol loop re-anchors.

### Missions
- `GET /missions` — mission definitions seeded from the spec's examples.
  Match user-facing names to `guid`.
- `GET /missions/{guid}` — one definition (404 if unknown).
- `POST /missions {"name": ..., "group_id": ...}` — create a definition;
  `group_id` is required (400 without it). On a real robot it must be an
  existing mission-group guid (`GET /mission_groups` lists them); the
  emulator accepts any string.
- `POST /mission_queue {"mission_id": "<guid>"}` — enqueue; unknown
  mission → 400. Returns the queue entry with monotonic integer `id`.
- `GET /mission_queue` — all entries with live `state`:
  Pending → Executing → Done on a simulated clock
  (`--mission-duration` seconds each, default 10). Aborted entries stay
  Aborted.
- `GET|PUT|DELETE /mission_queue/{id}` — read / patch / cancel one entry.
- `DELETE /mission_queue` — clear the queue, resets `mission_text`.

### Registers (PLC)
- `GET /registers` — 200 integer registers.
- `GET /registers/{1..200}` / `PUT /registers/{id} {"value": <number>}`
  (POST also accepted). Out-of-range id → 404/400 per spec.

### Misc
- `GET /metrics` — OpenMetrics text (battery, uptime, mission counters).
- `GET /swagger.json` (Swagger 2.0, verbatim) / `GET /openapi.json`
  (OpenAPI 3 conversion) — the machine-readable contract for this version.

## Fleet API — `/api/v1`

Auth header: `x-api-key: <key>` (emulator default `distributor`; override
`--api-key` / env `MIR_EMULATOR_API_KEY`). The fleet embeds real robot
emulators and drives them over their own REST API, so fleet state and robot
state never disagree.

- `GET /robots` — fleet's robots with id, state, battery.
- `GET /robots/{id}` — one robot, live view derived from its emulator.
- `PATCH /robots/{id}` — update writable robot fields.
- `POST /serial-order` — atomic multi-phase dispatch:
  ```json
  {"serial-order": {
     "id": "<optional guid>", "robot-id": "<optional — else round-robin>",
     "priority": "Medium",
     "phases": [{"mission-id": "<guid>", "order-id": "<optional>"}]}}
  ```
  Every phase's `mission-id` is validated against the robot **before**
  anything enqueues (rejected orders are atomic, never partial). Returns
  `201 {"id": "<serial-id>"}`. Duplicate id → 409; unknown robot or
  mission → 400.
- `GET /serial-order/{id}` — the order with per-phase status derived live
  from the robot's mission queue.
- `DELETE /serial-order/{id}` — aborts the order (marks orders aborted).
- `GET /order`, `GET /order/{id}` — flat per-phase order views
  (`robot-end-state` uses official enum values, e.g. "Emergency Stop").
- `GET /site/mission` — missions available across the site (for
  serial-order phases).
- `GET /system/version` — fleet software version.
- `GET /openapi.json` — MiR's official Fleet OpenAPI 3 document, verbatim.

## Emulator-only surfaces (never on real hardware)

- **`GET|PUT|DELETE /_emulator/faults`** — inject/read/clear faults for the
  current session. Names: `emergency_stop`, `error`, `localization_lost`,
  `battery_critical`, `blocked_path`, `mission_failure`. Holding faults
  (emergency stop, error, localization lost) freeze the mission simulation
  and release in place. `blocked_path` does NOT hold: it raises an active
  Planner error while the robot keeps executing (real MiRs replan around
  obstructions). `mission_failure` aborts the running and queued missions.
  `error`, `localization_lost`, and `mission_failure` clear via the
  documented `PUT /status {"clear_error": true}`; `emergency_stop`,
  `blocked_path`, and `battery_critical` model a physical cause and clear
  only by removing it — `PUT`/`DELETE` on this endpoint. `PUT` body:
  `{"faults": ["emergency_stop"]}`.
- **`GET|PUT|DELETE /_emulator/battery`** — battery control for the current
  session. `PUT` body, any subset: `{"percentage": 0-100, "charging":
  true|false, "charge_rate": <percent per simulated second, default 0.5>,
  "target": 0-100 (default 100)}`. While `charging`, the level climbs on
  the sim clock and caps at `target` (a target below the current level
  never discharges); drain still applies per executing second; pause,
  manual control, and holding faults freeze the curve along with the rest
  of the simulation. The `battery_critical` fault overrides everything.
  `DELETE` restores the stock drain-only model. `GET` reports the live
  percentage exactly as `/status` does. Without this surface battery only
  ever drains — "charge to N%" is observable on the emulator only through
  it.
- **`X-MiR-Session: <id>`** header (1–64 chars `[A-Za-z0-9._-]`) — fully
  isolated state per session id, robots *and* fleet. Invalid format → 400.
- **`X-MiR-Latency: <ms>`** header (cap 10000) — delays that one response;
  for client timeout testing.
- **`GET /_emulator/diff?from=<v>&to=<v>`** — structural API changes
  between two tracked versions (dispatcher only; cross-family refused).
- **`/_emulator/ws/status`** — WebSocket push of `/status` documents.
- **`GET|PUT|DELETE /_emulator/recorder`** — scenario recorder.
- **`GET /`** — index: what's being emulated, versions, and how to auth.

## CLI quick reference

```sh
uv run mir-emulator                          # newest robot version, :8080
uv run mir-emulator --mir-version 2.14.7 --port 8081
uv run mir-emulator --fleet-version 1.5.0 --fleet-robots 3.8.1,2.14.7
uv run mir-emulator --no-auth --mission-duration 2   # fast, auth-free tests
uv run mir-emulator --export openapi3 > api.json     # dump the contract
```

Tracked versions live in
`packages/mir-emulator/src/mir_emulator/specs/registry.json`; ask the
running server via `GET /` rather than assuming.
