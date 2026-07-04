---
name: mir-robot-control
description: >
  Control MiR robots (real or emulated) through the MiR REST API from plain
  natural-language commands. Use this skill whenever the user wants to drive,
  inspect, or test a MiR robot or MiR Fleet — "pause the robot", "queue the
  charging mission", "what's the battery at?", "clear the error", "send an
  order to the fleet", "inject an emergency stop", "start a robot to test
  against" — even if they never say "API" or name an endpoint. Also use it
  when a task needs a running MiR robot and none exists yet (this repo's
  emulator can provide one).
---

# MiR robot control

Translate human intent ("pause it", "run the pickup mission", "is anything
broken?") into MiR REST API calls, execute them with `curl` or `httpx`, and
report the result in the user's terms — not raw JSON dumps.

## Step 1: Resolve the target

Work out where the robot is and whether it is real:

1. **User gave a URL/host** — use it. `/api/v2.0.0/...` is a robot,
   `/api/v1/...` is a Fleet.
2. **Something is already listening locally** — probe before starting
   anything new: `curl -s http://127.0.0.1:8080/` (the emulator's index
   describes itself and lists versions).
3. **Nothing running** — start an emulator from this repo:
   ```sh
   uv run mir-emulator &                          # newest robot on :8080
   uv run mir-emulator --fleet-version 1.5.0 &    # a Fleet with embedded robots
   ```
   Useful flags: `--mir-version 2.14.7`, `--port`, `--no-auth`,
   `--mission-duration 3` (seconds per mission — keep it short for tests).

**Real vs emulated matters.** A MiR is a 100+ kg vehicle: against real
hardware, confirm before any state-changing call (PUT/POST/DELETE), never
inject faults or clear the mission queue unprompted, and say what you are
about to do first. Against the emulator, act freely. Treat any non-localhost
target as real unless the user says otherwise.

## Step 2: Authenticate

**Robot API** — MiR Basic auth is *not* standard Basic: the password is
replaced by its lowercase SHA-256 hex digest before base64 encoding.
Factory default account is `distributor` / `distributor`.

```sh
USER=distributor PASS=distributor
TOKEN=$(printf '%s:%s' "$USER" "$(printf '%s' "$PASS" | shasum -a 256 | cut -d' ' -f1)" | base64)
curl -s -H "Authorization: Basic $TOKEN" http://127.0.0.1:8080/api/v2.0.0/status
```

**Fleet API** — a single header: `x-api-key: <key>` (emulator default:
`distributor`).

A 401 means the token derivation or account is wrong — fix that; never retry
a loop against a real robot (accounts can lock).

## Step 3: Map intent to calls

Common intents, robot API (prefix every path with `/api/v2.0.0`):

| Intent | Call |
|---|---|
| "how's the robot / battery / where is it" | `GET /status` — read `state_text`, `battery_percentage`, `position`, `mission_text`, `errors` |
| "pause" / "stop what you're doing" | `PUT /status {"state_id": 4}` |
| "resume" / "continue" / "ready" | `PUT /status {"state_id": 3}` |
| "manual control" | `PUT /status {"state_id": 11}` |
| "clear the error" | `PUT /status {"clear_error": true}` |
| "rename it" / "move it to x,y" | `PUT /status {"name": ...}` / `{"position": {"x":, "y":, "orientation":}}` |
| "what missions exist" | `GET /missions` |
| "run/queue mission X" | find its guid in `GET /missions` (match by `name`), then `POST /mission_queue {"mission_id": "<guid>"}` |
| "what's queued / is it done" | `GET /mission_queue` (or `/mission_queue/{id}`) — `state` walks Pending → Executing → Done |
| "cancel everything" | `DELETE /mission_queue` |
| "cancel job N" | `DELETE /mission_queue/{id}` |
| "read/set PLC register N" | `GET /registers/{n}` / `PUT /registers/{n} {"value": ...}` |

Fleet API (prefix `/api/v1`): `GET /robots`, `GET|PATCH /robots/{id}`,
`POST /serial-order` (body below), `GET|DELETE /serial-order/{id}`,
`GET /order`, `GET /order/{id}`, `GET /site/mission`,
`GET /system/version`. A serial order dispatches missions to a robot:

```json
{"serial-order": {"robot-id": "<optional>", "priority": "Medium",
  "phases": [{"mission-id": "<guid from GET /site/mission>"}]}}
```

Full endpoint details, response fields, and emulator-only test surfaces
(fault injection, session isolation, latency shaping, version diff):
read [references/endpoints.md](references/endpoints.md).

## Step 4: Execute and report

- Verify connectivity with a cheap `GET /status` (robot) or
  `GET /api/v1/robots` (fleet) before acting on the real intent.
- After a state-changing call, **read back** (`GET /status`,
  `GET /mission_queue/{id}`) and report the observed state, not the request
  you sent. "Mission 'Charge' is Executing, battery 34%" — not a JSON blob.
- Mission ids and register values come from the API, never from memory.
  If a named mission doesn't exist, list what does and ask.
- Waiting for a mission: poll `GET /mission_queue/{id}` until `state` is
  `Done` (or `Aborted`); on the emulator each mission takes
  `--mission-duration` seconds (default 10).

## Multi-robot / test isolation (emulator only)

Send `X-MiR-Session: <name>` (1–64 chars of `[A-Za-z0-9._-]`) on every
request to get a private robot/fleet instance per session id — parallel
tests never see each other's state. Keep the header consistent across a
scenario or the state "disappears".
