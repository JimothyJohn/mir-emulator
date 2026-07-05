# mir-client

Typed Python client for the **MiR robot REST API** (`mir_client.robot`) and the
**MiR Fleet Enterprise Integration API** (`mir_client.fleet`), generated from
the official specs bundled with [mir-emulator](https://github.com/JimothyJohn/mir-emulator).

`mir_client/robot`, `mir_client/fleet`, and `_provenance.py` are entirely
generated (`scripts/generate_client.py`); CI fails if they drift from the spec
registry. Never hand-edit them.

```python
from mir_client import robot_client, fleet_client
from mir_client.robot.api.default import get_status

client = robot_client("http://192.168.12.20")   # or the emulator
status = get_status.sync(client=client)
print(status.state_text, status.battery_percentage)
```

## Version discovery — `connect()`

You don't have to know (or install) the version the target runs. `connect()`
performs a connect-time handshake — it asks the target what it is and which
MiR software version it reports — and returns the right client:

```python
from mir_client import connect, detect_server

client = connect("http://127.0.0.1:8080")        # robot, fleet, or the
client = connect("https://demo.example.com")     # multi-version dispatcher
client = connect("https://demo.example.com", version="2.14.7")  # pin a mount

info = detect_server("http://127.0.0.1:8080")    # just ask, don't build
print(info.kind, info.version)                   # e.g. "robot 3.8.1"
```

The handshake probes, in order: `/healthz` (dispatcher manifest), `/` (the
emulator's JSON index), `/api/v1/system/version` (official Fleet endpoint,
works on real fleets), `/swagger.json` (`info.version`), then a bare
`/api/v2.0.0/status` (proves a robot; version stays unknown). All probes are
unauthenticated reads. Async variants: `detect_server_async`, and
`client_for(info, ...)` to build a client from a prior detection.

The generated models are pinned to one spec per family (see
`mir_client.provenance`); `connect()` warns when the target's version is far
enough away that surfaces may differ (robot: different major; fleet:
different minor).
